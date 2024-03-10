from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator

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

_PROBE_TIMEOUT = httpx.Timeout(connect=5, read=30, write=10, pool=5)
_PROBE_CACHE_SECONDS = 30.0

GENERATION_TIMEOUT = httpx.Timeout(connect=30, read=300, write=60, pool=30)

_THOUGHT_STARTS = ("<|channel|>thought", "<|think|>")
_THOUGHT_ENDS = ("channel|>", "<|channel|>")


def _strip_thought(text: str) -> str:
    if not text:
        return text
    text = re.sub(
        r"<\|channel\|>thought.*?(?:channel\|>|<\|channel\|>)",
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"<\|think\|>.*?(?:channel\|>|<\|channel\|>)", "", text, flags=re.DOTALL
    )
    return text


class _ThoughtScrubber:
    def __init__(self) -> None:
        self.in_thought = False
        self.buf = ""
        self._longest = max(len(s) for s in _THOUGHT_STARTS)

    def feed(self, raw: str) -> list[str]:
        pieces: list[str] = []
        self.buf += raw
        while self.buf:
            if not self.in_thought:
                earliest = None
                earliest_start = ""
                for start in _THOUGHT_STARTS:
                    idx = self.buf.find(start)
                    if idx != -1 and (earliest is None or idx < earliest):
                        earliest = idx
                        earliest_start = start
                if earliest is not None:
                    if earliest > 0:
                        pieces.append(self.buf[:earliest])
                    self.buf = self.buf[earliest + len(earliest_start) :]
                    self.in_thought = True
                    continue
                keep = 0
                for start in _THOUGHT_STARTS:
                    for k in range(self._longest - 1, 0, -1):
                        if len(self.buf) >= k and start.startswith(self.buf[-k:]):
                            keep = max(keep, k)
                            break
                        if len(self.buf) < k and start.startswith(self.buf):
                            keep = max(keep, len(self.buf))
                            break
                if keep and len(self.buf) > keep:
                    pieces.append(self.buf[:-keep])
                    self.buf = self.buf[-keep:]
                    break
                if keep:
                    break
                pieces.append(self.buf)
                self.buf = ""
                break
            else:
                earliest = None
                earliest_end = ""
                for end in _THOUGHT_ENDS:
                    idx = self.buf.find(end)
                    if idx != -1 and (earliest is None or idx < earliest):
                        earliest = idx
                        earliest_end = end
                if earliest is not None:
                    self.buf = self.buf[earliest + len(earliest_end) :]
                    self.in_thought = False
                    continue
                keep = 0
                for end in _THOUGHT_ENDS:
                    for k in range(len(end) - 1, 0, -1):
                        if len(self.buf) >= k and end.startswith(self.buf[-k:]):
                            keep = max(keep, k)
                            break
                if keep:
                    self.buf = self.buf[-keep:] if len(self.buf) > keep else self.buf
                    break
                self.buf = ""
                break
        return pieces

    def flush(self) -> list[str]:
        remainder = self.buf
        self.buf = ""
        if remainder and not self.in_thought:
            stripped = _strip_thought(remainder)
            return [stripped] if stripped else []
        return []


def _parse_arguments(raw: str) -> Any:
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return raw


def _wire_tool_calls(pending: dict[int, dict]) -> list[dict]:
    calls: list[dict] = []
    for index in sorted(pending):
        entry = pending[index]
        calls.append(
            {
                "id": entry["id"] or f"call_{index}",
                "type": "function",
                "function": {
                    "name": entry["name"],
                    "arguments": entry["arguments"] or "{}",
                },
            }
        )
    return calls


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        api_key = settings.brot_api_key or "unset"
        self._client = AsyncOpenAI(
            base_url=settings.brot_base_url,
            api_key=api_key,
            timeout=GENERATION_TIMEOUT,
        )
        self._probe_cache: tuple[float, tuple[bool, str]] | None = None
        self._inflight: dict[str, dict] = {}

    def begin_inflight(self, session_id: str) -> asyncio.Event:
        entry = self._inflight.get(session_id)
        if entry is None:
            entry = {"event": asyncio.Event(), "active": True}
            self._inflight[session_id] = entry
        return entry["event"]

    def end_inflight(self, session_id: str) -> None:
        self._inflight.pop(session_id, None)

    def is_inflight(self, session_id: str) -> bool:
        entry = self._inflight.get(session_id)
        return bool(entry and entry.get("active"))

    def get_inflight_event(self, session_id: str) -> asyncio.Event | None:
        entry = self._inflight.get(session_id)
        return entry["event"] if entry else None

    def cancel_inflight(self, session_id: str) -> None:
        entry = self._inflight.get(session_id)
        if entry:
            entry["active"] = False
            entry["event"].set()
            logger.info("cancellation requested for session %s", session_id)

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1500,
        temperature: float = 0.3,
        session_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator:
        event = cancel_event or (
            self.get_inflight_event(session_id) if session_id else None
        )
        try:
            iterator = await self._open_stream(
                messages, max_tokens=max_tokens, temperature=temperature, tools=tools
            )
            as_events = tools is not None
            scrubber = _ThoughtScrubber()
            pending: dict[int, dict] = {}
            content_parts: list[str] = []
            while True:
                try:
                    chunk = await self._next_chunk(iterator, event)
                except StopAsyncIteration:
                    break
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                finish_reason = getattr(choice, "finish_reason", None)
                delta_obj = choice.delta if choice.delta else None
                if delta_obj is not None:
                    for tool_delta in getattr(delta_obj, "tool_calls", None) or []:
                        index = getattr(tool_delta, "index", 0) or 0
                        entry = pending.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        call_id = getattr(tool_delta, "id", None)
                        if call_id:
                            entry["id"] = call_id
                        function = getattr(tool_delta, "function", None)
                        if function is not None:
                            fn_name = getattr(function, "name", None)
                            if fn_name:
                                entry["name"] = fn_name
                            args_piece = getattr(function, "arguments", None)
                            if args_piece:
                                entry["arguments"] += args_piece
                raw = (
                    getattr(delta_obj, "content", None)
                    if delta_obj is not None
                    else None
                )
                if delta_obj is not None and (
                    getattr(delta_obj, "reasoning_content", None)
                    or getattr(delta_obj, "reasoning", None)
                    or getattr(delta_obj, "thinking", None)
                ):
                    if not raw:
                        continue
                if not raw:
                    continue
                content_parts.append(raw)
                for piece in scrubber.feed(raw):
                    yield {"type": "delta", "delta": piece} if as_events else piece
                if finish_reason == "tool_calls":
                    break
            for piece in scrubber.flush():
                yield {"type": "delta", "delta": piece} if as_events else piece
            if pending and as_events:
                raw_calls = _wire_tool_calls(pending)
                joined_content = "".join(content_parts)
                for call in raw_calls:
                    yield {
                        "type": "tool_call",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "arguments": _parse_arguments(call["function"]["arguments"]),
                        "raw_tool_calls": raw_calls,
                        "assistant_content": joined_content,
                    }
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize every failure
            raise self._normalize(exc) from exc

    async def _open_stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        temperature: float,
        tools: list[dict] | None,
    ):
        kwargs: dict = {}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            stream = await self._client.chat.completions.create(
                model=self._settings.brot_model,
                messages=messages,
                stream=True,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
            return stream.__aiter__()
        except APIStatusError as exc:
            status = getattr(exc, "status_code", None)
            if tools and status == 400 and "tool" in str(exc).lower():
                logger.warning("tools unsupported by endpoint; retrying without tools")
                stream = await self._client.chat.completions.create(
                    model=self._settings.brot_model,
                    messages=messages,
                    stream=True,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return stream.__aiter__()
            raise

    @staticmethod
    async def _next_chunk(iterator: AsyncIterator, cancel_event: asyncio.Event | None):
        if cancel_event is None:
            return await anext(iterator)
        cancel_task = asyncio.ensure_future(cancel_event.wait())
        next_task = asyncio.ensure_future(anext(iterator))
        try:
            done, _ = await asyncio.wait(
                {cancel_task, next_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except BaseException:
            for task in (cancel_task, next_task):
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
            raise
        if cancel_task in done:
            next_task.cancel()
            try:
                await next_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            except Exception:
                pass
            raise GenerationCancelled(
                "Generation cancelled by the user or a client disconnect."
            )
        cancel_task.cancel()
        try:
            await cancel_task
        except asyncio.CancelledError:
            pass
        return next_task.result()

    async def complete_json(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> tuple[str | None, str | None]:
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.brot_model,
                messages=messages,
                stream=False,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = response.choices[0].message.content if response.choices else None
            cleaned = _strip_thought(content or "")
            return (cleaned, None)
        except Exception as exc:  # noqa: BLE001 - normalize every failure
            provider_error = self._normalize(exc)
            return (
                None,
                f"{provider_error.code}: {provider_error.detail or provider_error.message}",
            )

    async def quick_probe(self) -> tuple[bool, str]:
        now = time.monotonic()
        if self._probe_cache and now - self._probe_cache[0] < _PROBE_CACHE_SECONDS:
            return self._probe_cache[1]

        probe_client = self._client.with_options(timeout=_PROBE_TIMEOUT)
        try:
            await probe_client.chat.completions.create(
                model=self._settings.brot_model,
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

    @staticmethod
    def _normalize(exc: Exception) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        if isinstance(exc, AuthenticationError):
            return ProviderError("auth", "The model endpoint rejected the API key.")
        if isinstance(exc, RateLimitError):
            headers = getattr(exc, "headers", None) or {}
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
            detail = (
                f"retry-after={retry_after}"
                if retry_after
                else "retry the request later"
            )
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
    global _client_singleton
    settings = request.app.state.settings
    if _client_singleton is None:
        _client_singleton = LLMClient(settings)
    return _client_singleton


def reset_llm_client() -> None:
    global _client_singleton
    _client_singleton = None
