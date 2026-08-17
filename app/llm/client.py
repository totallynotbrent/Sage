"""Thin wrapper around the OpenAI-compatible (BROT) endpoint.

Responsibilities:
  - streaming chat completions with per-session cancellation,
  - non-streaming structured completions,
  - a cheap, cached reachability probe for /api/health,
  - normalization of SDK errors into ``ProviderError`` codes,
  - tracking in-flight generations per session so /stop and duplicate-turn
    detection can work.

The API key is never logged; it only ever reaches the configured endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

import httpx
from fastapi import Request
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)

from app.config import Settings
from app.errors import GenerationCancelled, ProviderError

logger = logging.getLogger("app")

#: How long a reachability probe may take (short, so /api/health stays snappy).
_PROBE_TIMEOUT = httpx.Timeout(connect=5, read=30, write=10, pool=5)
_PROBE_CACHE_SECONDS = 30.0

#: Long timeouts for real generations: the local model may be slow.
GENERATION_TIMEOUT = httpx.Timeout(connect=30, read=900, write=60, pool=30)


class LLMClient:
    """An OpenAI-compatible client for the configured BROT endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # A non-empty placeholder keeps openai>=1.0 happy when the key is unset;
        # config-valid routes reject before reaching the client anyway.
        api_key = settings.BROT_api_key or "unset"
        self._client = AsyncOpenAI(
            base_url=settings.BROT_base_url,
            api_key=api_key,
            timeout=GENERATION_TIMEOUT,
        )
        self._probe_cache: tuple[float, tuple[bool, str]] | None = None
        # session_id -> {"event": asyncio.Event, "active": bool}
        self._inflight: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    # In-flight tracking / cancellation
    # ------------------------------------------------------------------ #
    def begin_inflight(self, session_id: str) -> asyncio.Event:
        """Register an in-flight generation for ``session_id`` and return its
        cancellation event. Idempotent within one generation."""
        entry = self._inflight.get(session_id)
        if entry is None:
            entry = {"event": asyncio.Event(), "active": True}
            self._inflight[session_id] = entry
        return entry["event"]

    def end_inflight(self, session_id: str) -> None:
        """Mark the session's generation finished and drop its tracking entry."""
        self._inflight.pop(session_id, None)

    def is_inflight(self, session_id: str) -> bool:
        """True when a generation is currently active for the session."""
        entry = self._inflight.get(session_id)
        return bool(entry and entry.get("active"))

    def get_inflight_event(self, session_id: str) -> asyncio.Event | None:
        """Return the session's cancellation event, if any."""
        entry = self._inflight.get(session_id)
        return entry["event"] if entry else None

    def cancel_inflight(self, session_id: str) -> None:
        """Request cancellation of the session's in-flight generation."""
        entry = self._inflight.get(session_id)
        if entry:
            entry["active"] = False
            entry["event"].set()
            logger.info("cancellation requested for session %s", session_id)

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #
    async def stream_chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1500,
        temperature: float = 0.3,
        session_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat-completion text deltas.

        If ``cancel_event`` is set (or the session's registered event is set)
        mid-stream, ``GenerationCancelled`` is raised. ``session_id`` is used to
        look up the registered cancellation event when ``cancel_event`` is None.
        """
        event = cancel_event or (
            self.get_inflight_event(session_id) if session_id else None
        )
        try:
            stream = await self._client.chat.completions.create(
                model=self._settings.BROT_model,
                messages=messages,
                stream=True,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            iterator = stream.__aiter__()
            while True:
                chunk = await self._next_chunk(iterator, event)
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = choices[0].delta.content if choices[0].delta else None
                if delta:
                    yield delta
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize every failure
            raise self._normalize(exc) from exc

    @staticmethod
    async def _next_chunk(iterator: AsyncIterator, cancel_event: asyncio.Event | None):
        """Fetch the next stream chunk, aborting early on cancellation."""
        if cancel_event is None:
            return await anext(iterator)
        cancel_task = asyncio.ensure_future(cancel_event.wait())
        next_task = asyncio.ensure_future(anext(iterator))
        done, _ = await asyncio.wait(
            {cancel_task, next_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_task in done:
            next_task.cancel()
            raise GenerationCancelled(
                "Generation cancelled by the user or a client disconnect."
            )
        return next_task.result()

    # ------------------------------------------------------------------ #
    # Structured (non-streaming)
    # ------------------------------------------------------------------ #
    async def complete_json(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> tuple[str | None, str | None]:
        """Request a single non-streamed completion.

        Returns ``(full_text, error_text)``; on failure ``full_text`` is None and
        ``error_text`` describes the normalized problem. Never raises.
        """
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.BROT_model,
                messages=messages,
                stream=False,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = response.choices[0].message.content if response.choices else None
            return (content or "", None)
        except Exception as exc:  # noqa: BLE001 - normalize every failure
            provider_error = self._normalize(exc)
            return (None, f"{provider_error.code}: {provider_error.detail or provider_error.message}")

    # ------------------------------------------------------------------ #
    # Health probe
    # ------------------------------------------------------------------ #
    async def quick_probe(self) -> tuple[bool, str]:
        """Return ``(reachable, message)`` with a 30-second result cache.

        Uses a short-timeout client so health checks never hang.
        """
        now = time.monotonic()
        if self._probe_cache and now - self._probe_cache[0] < _PROBE_CACHE_SECONDS:
            return self._probe_cache[1]

        probe_client = self._client.with_options(timeout=_PROBE_TIMEOUT)
        try:
            await probe_client.chat.completions.create(
                model=self._settings.BROT_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            )
            result = (True, "ok")
        except Exception as exc:  # noqa: BLE001 - normalize every failure
            provider_error = self._normalize(exc)
            result = (
                False,
                f"{provider_error.code}: {provider_error.detail or provider_error.message}",
            )
        self._probe_cache = (now, result)
        return result

    # ------------------------------------------------------------------ #
    # Error normalization
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize(exc: Exception) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        if isinstance(exc, AuthenticationError):
            return ProviderError("auth", "The model endpoint rejected the API key.")
        if isinstance(exc, RateLimitError):
            headers = getattr(exc, "headers", None) or {}
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
            detail = f"retry-after={retry_after}" if retry_after else "retry the request later"
            return ProviderError(
                "rate_limit",
                "The model endpoint is rate-limited.",
                detail=detail,
            )
        if isinstance(exc, APITimeoutError):
            return ProviderError("timeout", "The model endpoint timed out.")
        if isinstance(exc, APIConnectionError):
            return ProviderError(
                "connection", "Could not connect to the model endpoint."
            )
        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", None)
            code = "bad_request" if status == 400 else "upstream"
            return ProviderError(code, f"The model endpoint returned status {status}.")
        if isinstance(exc, ValueError) and "api_key" in str(exc).lower():
            return ProviderError("auth", "The API key is invalid or missing.")
        return ProviderError(
            "upstream", f"Unexpected model endpoint error: {type(exc).__name__}"
        )


_client_singleton: LLMClient | None = None


def get_llm_client(request: Request) -> LLMClient:
    """FastAPI dependency: the process-wide LLM client for the app's settings."""
    global _client_singleton
    settings = request.app.state.settings
    if _client_singleton is None:
        _client_singleton = LLMClient(settings)
    return _client_singleton


def reset_llm_client() -> None:
    """Drop the cached client singleton (used when an app is recreated)."""
    global _client_singleton
    _client_singleton = None
