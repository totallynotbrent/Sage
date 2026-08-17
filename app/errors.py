"""Domain exceptions and the JSON error handler.

Every Sage error is serialized as::

    {"error": {"code": ..., "message": ..., "detail": ..., "retryable": ...}}

Provider errors carry a machine code (auth|rate_limit|timeout|connection|
bad_request|upstream) that maps to an HTTP status. The API key is stripped
from every message before it is returned to the client.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.logging_setup import mask_secret

#: Provider error code -> HTTP status.
PROVIDER_STATUS = {
    "auth": 401,
    "rate_limit": 429,
    "timeout": 504,
    "connection": 502,
    "bad_request": 400,
    "upstream": 502,
}

#: Provider codes that a client retry may fix.
RETRYABLE_CODES = {"rate_limit", "timeout", "connection", "upstream"}


class SageError(Exception):
    """Base class for all domain errors raised by Sage."""

    status_code = 500
    code = "internal_error"
    retryable = False

    def __init__(self, message: str, *, detail: Any = None) -> None:
        self.message = message
        self.detail = detail
        super().__init__(message)


class ConfigError(SageError):
    """The endpoint configuration is missing or invalid."""

    status_code = 400
    code = "config_error"

    def __init__(self, problems: list[str] | str) -> None:
        if isinstance(problems, str):
            problems = [problems]
        super().__init__(
            "The model endpoint is not configured.",
            detail="; ".join(problems),
        )


class ProviderError(SageError):
    """The configured model endpoint returned an error."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        detail: Any = None,
    ) -> None:
        if code not in PROVIDER_STATUS:
            raise ValueError(f"unknown provider error code: {code}")
        self.code = code
        self.status_code = PROVIDER_STATUS[code]
        self.retryable = code in RETRYABLE_CODES
        message = message or f"The model endpoint reported an error ({code})."
        super().__init__(message, detail=detail)


class NotFoundError(SageError):
    status_code = 404
    code = "not_found"

    def __init__(self, resource: str, identifier: str | None = None) -> None:
        detail = f"{resource} not found"
        if identifier:
            detail += f": {identifier}"
        super().__init__(detail, detail=detail)


class ConflictError(SageError):
    status_code = 409
    code = "conflict"

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message, detail=detail)


class FileTooLargeError(SageError):
    status_code = 413
    code = "file_too_large"


class UnsupportedFormatError(SageError):
    status_code = 415
    code = "unsupported_format"


class ExtractionError(SageError):
    status_code = 422
    code = "extraction_failed"


class ModelOutputError(SageError):
    status_code = 422
    code = "model_output"


class StorageFullError(SageError):
    status_code = 507
    code = "storage_full"


class ContextTooLongError(SageError):
    status_code = 400
    code = "context_too_long"


class GenerationCancelled(SageError):
    """Raised internally when a generation is cancelled or the client goes away.

    This never reaches the HTTP exception handler: streaming routes convert it
    into an SSE ``error`` event.
    """

    status_code = 499
    code = "cancelled"
    retryable = True


def _error_body(code: str, message: str, detail: Any, retryable: bool) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "detail": detail,
            "retryable": retryable,
        }
    }


def register_exception_handlers(app: FastAPI, secret: str = "") -> None:
    """Attach JSON exception handlers to ``app`` for every Sage error shape."""

    @app.exception_handler(SageError)
    async def on_sage_error(request: Request, exc: SageError) -> JSONResponse:
        body = _error_body(
            exc.code,
            mask_secret(exc.message, secret),
            mask_secret(str(exc.detail) if exc.detail is not None else None, secret),
            exc.retryable,
        )
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "bad_request", "The request body is invalid.", str(exc.errors()), False
            ),
        )

    @app.exception_handler(HTTPException)
    async def on_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                "http_error", mask_secret(str(exc.detail), secret), None, False
            ),
        )

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the real error server-side; never leak internals to the client.
        import logging

        logging.getLogger("app").exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=_error_body(
                "internal_error", "An unexpected server error occurred.", None, True
            ),
        )
