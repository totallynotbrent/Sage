"""Sage LLM client — port of bread's OllamaClient (commit 9c7fe71).

Talks to the ollama NATIVE /api/chat endpoint through the official ``ollama``
AsyncClient, including bread's feature-degradation retry ladder.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator

import httpx
from fastapi import Request
from ollama import AsyncClient, ResponseError

from app.config import Settings
from app.errors import GenerationCancelled, ProviderError

logger = logging.getLogger("app")

REQUEST_TIMEOUT = 300.0

UNSUPPORTED_TOOL_HINTS = ("tool", "function", "does not support tools")
UNSUPPORTED_THINK_HINTS = ("think", "thinking")
UNSUPPORTED_IMAGE_HINTS = ("image", "vision", "multimodal")
UNSUPPORTED_FORMAT_HINTS = (
    "format",
    "json schema",
    "structured output",
    "structured outputs",
    "does not support json",
)
RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 4

_THOUGHT_STARTS = ("<|channel|>thought", "<|think|>")
_THOUGHT_ENDS = ("channel|>", "<|channel|>")

# Text-form tool calls some models leak into visible content. The native
# /api/chat path returns structured tool_calls, but gemma occasionally writes
# the call as bare text too — sometimes with brackets, sometimes without
# (e.g. "call:run_probe/" or "[call:run_probe]"). Strip bracketed forms and
# bracketless residues so none of it can reach the UI.
_LEAKED_CALL_RE = re.compile(r"<call:\w+\b[^>]*>?")
_LEAKED_OPEN_RE = re.compile(r"<call:\w+\b[^<]*$")
_LEAKED_BARE_RE = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=[\n\r\t.:;,!?)(\"'-]))"   # boundary before token
    r"\[?call:\w+\b/?\]?"                             # [call:name] / call:name/
    r"(?:\s*status\s*=\s*[\"'][^\"']*[\"'])?"
    r"(?:\s*\([^)\"]*\))?"
)


def _detect_unsupported_feature(message: str) -> str | None:
    lowered = message.lower()
    if any(hint in lowered for hint in UNSUPPORTED_TOOL_HINTS):
        return "tools"
    if any(hint in lowered for hint in UNSUPPORTED_THINK_HINTS):
        return "think"
    if any(hint in lowered for hint in UNSUPPORTED_IMAGE_HINTS):
        return "images"
    if any(hint in lowered for hint in UNSUPPORTED_FORMAT_HINTS):
        return "format"
    return None


class _FeatureUnsupportedError(Exception):
    def __init__(self, feature: str, message: str) -> None:
        super().__init__(message)
        self.feature = feature


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


def _looks_like_leak(text: str) -> bool:
    """True when text begins with a tool-call leak signature.

    Used to hold streamed fragments that might be a leaked ``call:...`` so we
    can discard them if a real structured tool call follows (mirrors bread,
    which never streams content while a tool call is in flight).
    """
    stripped = text.lstrip()
    return bool(re.match(r"^<?call:?", stripped, re.IGNORECASE)) or "<call:" in stripped


def _strip_leaked_calls(text: str) -> str:
    cleaned = _LEAKED_CALL_RE.sub("", text)
    cleaned = _LEAKED_BARE_RE.sub("", cleaned)
    return _LEAKED_OPEN_RE.sub("", cleaned)


class _ThoughtScrubber:
    """Filters reasoning-channel markers out of streamed deltas."""

    def __init__(self) -> None:
        self._buffer = ""
        self._in_thought = False

    def feed(self, raw: str) -> list[str]:
        self._buffer += raw
        out: list[str] = []
        while self._buffer:
            if self._in_thought:
                idx: int | None = None
                found: str | None = None
                for marker in _THOUGHT_ENDS:
                    pos = self._buffer.find(marker)
                    if pos != -1 and (idx is None or pos < idx):
                        idx, found = pos, marker
                if idx is None or found is None:
                    self._buffer = ""
                    break
                self._buffer = self._buffer[idx + len(found):]
                self._in_thought = False
            else:
                idx = None
                found = None
                for marker in _THOUGHT_STARTS:
                    pos = self._buffer.find(marker)
                    if pos != -1 and (idx is None or pos < idx):
                        idx, found = pos, marker
                if idx is None or found is None:
                    emit = self._buffer
                    self._buffer = ""
                    if emit:
                        out.append(emit)
                    break
                if idx > 0:
                    out.append(self._buffer[:idx])
                self._buffer = self._buffer[idx + len(found):]
                self._in_thought = True
        return out

    def flush(self) -> list[str]:
        rest = self._buffer
        self._buffer = ""
        if self._in_thought:
            return []
        return [rest] if rest else []


def _parse_arguments(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def _args_to_json(raw: Any) -> str:
    if isinstance(raw, str):
        return raw or "{}"
    try:
        return json.dumps(raw)
    except (TypeError, ValueError):
        return "{}"


class ChatAttempt:
    """Which features this attempt still includes (bread's degradation model)."""

    __slots__ = ("use_tools", "use_format")

    def __init__(self, use_tools: bool = False, use_format: bool = False) -> None:
        self.use_tools = use_tools
        self.use_format = use_format

    def without(self, feature: str) -> "ChatAttempt":
        clone = ChatAttempt(self.use_tools, self.use_format)
        if feature == "tools":
            clone.use_tools = False
        elif feature == "format":
            clone.use_format = False
        return clone


class LLMClient:
    """Sage's LLM backend — a port of bread's OllamaClient.

    Talks to the ollama NATIVE /api/chat endpoint through the official
    ``ollama`` AsyncClient (the exact stack bread uses), including bread's
    feature-degradation retry ladder.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        host = self._native_base()
        api_key = (settings.brot_api_key or "").strip()
        kwargs: dict[str, Any] = {"host": host, "timeout": REQUEST_TIMEOUT}
        if api_key:
            kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
        self._client = AsyncClient(**kwargs)
        self._probe_cache: tuple[float, tuple[bool, str]] | None = None
        self._inflight: dict[str, dict] = {}

    # -- inflight/cancellation bookkeeping --
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

    # -- helpers --
    def _native_base(self) -> str:
        base = (self._settings.brot_base_url or "").strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return base

    @property
    def model(self) -> str:
        return self._settings.brot_model

    @staticmethod
    def _to_ollama_messages(messages: list[dict]) -> list[dict]:
        """Convert OpenAI-style history entries into ollama-native shape."""
        out: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role == "assistant" and isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
                calls = []
                for c in m["tool_calls"]:
                    fn = c.get("function") or {}
                    calls.append(
                        {
                            "function": {
                                "name": fn.get("name", ""),
                                "arguments": _parse_arguments(fn.get("arguments", "{}")),
                            }
                        }
                    )
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or "",
                        "tool_calls": calls,
                    }
                )
            else:
                out.append(m)
        return out

    @staticmethod
    def _to_ollama_tools(tools: list[dict]) -> list[dict]:
        converted: list[dict] = []
        for t in tools:
            fn = t.get("function") or t
            if not fn.get("name"):
                continue
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters")
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return converted

    # -- streaming chat (bread-style attempts + Sage's event contract) --
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
        as_events = tools is not None
        ollama_messages = self._to_ollama_messages(messages)
        ollama_tools = self._to_ollama_tools(tools) if tools else []

        attempt = ChatAttempt(use_tools=bool(ollama_tools))
        last_error: Exception | None = None

        for _ in range(MAX_ATTEMPTS):
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": ollama_messages,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            if attempt.use_tools and ollama_tools:
                payload["tools"] = ollama_tools
            try:
                async for item in self._run_stream(payload, event, as_events):
                    yield item
                return
            except GenerationCancelled:
                raise
            except _FeatureUnsupportedError as exc:
                logger.warning(
                    "retrying without %s after unsupported-feature error: %s",
                    exc.feature,
                    exc,
                )
                attempt = attempt.without(exc.feature)
                last_error = exc
                continue
            except ResponseError as exc:
                status = getattr(exc, "status_code", None)
                feature = _detect_unsupported_feature(str(exc))
                if feature and getattr(attempt, f"use_{feature}", False):
                    attempt = attempt.without(feature)
                    last_error = exc
                    continue
                if status in RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "retrying after ollama status_code=%s error=%r", status, exc
                    )
                    last_error = exc
                    continue
                logger.error("ollama ResponseError: %r", exc)
                raise self._normalize(exc) from exc
            except Exception as exc:  # noqa: BLE001
                raise self._normalize(exc) from exc

        raise ProviderError(
            "upstream",
            f"ollama chat failed after {MAX_ATTEMPTS} attempts: {last_error}",
        )

    async def _run_stream(
        self,
        payload: dict[str, Any],
        cancel_event: asyncio.Event | None,
        as_events: bool,
    ) -> AsyncIterator:
        # Bread-style leak handling. gemma sometimes writes the tool call as
        # visible text (e.g. "call:run_probe/") inside the SAME message that
        # also carries the structured tool_calls. That leaked text arrives in
        # the stream BEFORE the tool_calls field, so per-delta regex stripping
        # is defeated by token-splitting. Bread avoids the leak by never
        # streaming content while a tool call is in flight: it buffers the
        # message and only surfaces content when NO tool call was made.
        #
        # To keep normal answers streaming, we stream content live but hold
        # back any fragment that looks like the start of a leaked call. If a
        # real tool call follows, the held fragment is discarded; otherwise it
        # is flushed as ordinary prose.
        scrubber = _ThoughtScrubber()
        content_parts: list[str] = []
        pending_calls: list[dict] = []
        held: list[str] = []
        try:
            stream = await self._client.chat(stream=True, **payload)
            async for response in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise GenerationCancelled(
                        "Generation cancelled by the user or a client disconnect."
                    )
                message = getattr(response, "message", None) or {}
                content = getattr(message, "content", "") or ""
                raw_calls = getattr(message, "tool_calls", None) or []
                if raw_calls:
                    for i, call in enumerate(raw_calls):
                        fn = getattr(call, "function", None)
                        name = (getattr(fn, "name", "") or "") if fn else ""
                        if not name:
                            continue
                        args = getattr(fn, "arguments", {}) or {}
                        pending_calls.append(
                            {
                                "id": getattr(call, "id", "") or f"call_{i + 1}",
                                "type": "function",
                                "function": {"name": name, "arguments": _args_to_json(args)},
                            }
                        )
                if content:
                    content_parts.append(content)
                    for piece in scrubber.feed(content):
                        if _looks_like_leak(piece):
                            held.append(piece)
                        elif held:
                            held.append(piece)
                            if not _looks_like_leak("".join(held)):
                                # Resolved into normal prose — release it.
                                flushed = "".join(held)
                                held.clear()
                                cleaned = _strip_leaked_calls(flushed)
                                if cleaned:
                                    yield (
                                        {"type": "delta", "delta": cleaned}
                                        if as_events
                                        else cleaned
                                    )
                        else:
                            cleaned = _strip_leaked_calls(piece)
                            if cleaned:
                                yield (
                                    {"type": "delta", "delta": cleaned}
                                    if as_events
                                    else cleaned
                                )
                if not getattr(response, "done", False):
                    continue
                break
        except ResponseError as exc:
            message_text = str(getattr(exc, "error", exc))
            feature = _detect_unsupported_feature(message_text)
            if feature:
                raise _FeatureUnsupportedError(feature, message_text) from exc
            raise

        if pending_calls:
            # Tool-call turn: discard any held/leaked text and emit only the
            # structured tool calls (the UI renders their output itself).
            joined_content = _strip_leaked_calls("".join(content_parts)).strip()
            for call in pending_calls:
                yield {
                    "type": "tool_call",
                    "id": call["id"],
                    "name": call["function"]["name"],
                    "arguments": _parse_arguments(call["function"]["arguments"]),
                    "raw_tool_calls": pending_calls,
                    "assistant_content": joined_content or None,
                }
            return

        # Plain answer turn: release any held fragment, then flush remainder.
        if held:
            cleaned = _strip_leaked_calls("".join(held))
            if cleaned:
                yield {"type": "delta", "delta": cleaned} if as_events else cleaned
            held.clear()
        for piece in scrubber.flush():
            cleaned = _strip_leaked_calls(piece)
            if cleaned:
                yield {"type": "delta", "delta": cleaned} if as_events else cleaned

    # -- non-streaming JSON helper (plan/diagram/title generation) --
    async def complete_json(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> tuple[str | None, str | None]:
        payload = {
            "model": self.model,
            "messages": self._to_ollama_messages(messages),
            "stream": False,
            "format": "json",
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            response = await self._client.chat(**payload)
        except ResponseError as exc:
            feature = _detect_unsupported_feature(str(exc))
            if feature == "format":
                payload.pop("format", None)
                try:
                    response = await self._client.chat(**payload)
                except Exception as exc2:  # noqa: BLE001
                    logger.warning("complete_json failed: %s", exc2)
                    return None, str(exc2)
            else:
                logger.warning("complete_json failed: %s", exc)
                return None, str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("complete_json failed: %s", exc)
            return None, str(exc)
        message = getattr(response, "message", None)
        content = (getattr(message, "content", "") or "").strip()
        if not content:
            return None, "empty response"
        return content, None

    async def quick_probe(self) -> tuple[bool, str]:
        now = time.monotonic()
        if self._probe_cache and now - self._probe_cache[0] < 30.0:
            return self._probe_cache[1]
        ok = False
        detail = ""
        try:
            await asyncio.wait_for(self._client.list(), timeout=10)
            ok = True
            detail = "ok"
        except Exception as exc:  # noqa: BLE001
            detail = type(exc).__name__
        self._probe_cache = (now, (ok, detail))
        return ok, detail

    @staticmethod
    def _normalize(exc: Exception) -> ProviderError:
        status = getattr(exc, "status_code", None)
        if status is not None:
            kind = "rate_limit" if status == 429 else "upstream"
            return ProviderError(kind, f"HTTP {status}: {exc}")
        text = str(exc).lower()
        if "auth" in text or "unauthorized" in text or "401" in text:
            return ProviderError("auth", str(exc))
        if "timeout" in text or isinstance(exc, asyncio.TimeoutError):
            return ProviderError("timeout", "generation timed out")
        if "connect" in text or "network" in text:
            return ProviderError("network", f"cannot reach model backend: {exc}")
        return ProviderError("upstream", f"{type(exc).__name__}: {exc}")


def get_llm_client(request: Request) -> LLMClient:
    client = getattr(request.app.state, "llm_client", None)
    if client is None:
        client = LLMClient(request.app.state.settings)
        request.app.state.llm_client = client
    return client


def reset_llm_client() -> None:
    """Drop any cached client on app.state."""
    return None
