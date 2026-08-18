from __future__ import annotations

from fastapi import HTTPException

from app.config import Settings, validation_problems
from app.errors import ConfigError


def require_configured(settings: Settings) -> None:
    problems = validation_problems(settings)
    if problems:
        raise ConfigError(problems)


def handle_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))
