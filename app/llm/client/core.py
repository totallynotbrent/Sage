"""Sage LLM client core — SageOllamaClient, a bread-parity Ollama transport plus Sage SSE inflight bookkeeping and compat wrappers.

Splitting note: ChatAttempt/OllamaClientConfig are kept verbatim in attempt.py/config.py for easy bread parity diffs; helpers live in utils.py; leaked-call stripping in leak_guard.py."
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from ollama import AsyncClient, ResponseError

from app.config import Settings
from app.errors import GenerationCancelled, ProviderError
from app.llm.base import (
    FinishReason,
    LLMClient,
    LLMMessage,
    LLMUsage,
    StreamChunk,
    ToolCall,
    ToolSchema,
)

from app.llm.client.attempt import ChatAttempt
from app.llm.client.config import OllamaClientConfig
from app.llm.client.leak_guard import _strip_leaked_calls
from app.llm.client.utils import (
    RETRYABLE_STATUS_CODES,
    _FeatureUnsupportedError,
    _coerce_logprobs,
    _compact_model_details,
    _detect_unsupported_feature,
    _dump_sdk_value,
    _get,
    _optional_int,
    _optional_text,
    _parse_arguments,
)

class SageOllamaClient(LLMClient):
    """Bread-identical Ollama client plus Sage SSE inflight bookkeeping."""

    def __init__(self, config: OllamaClientConfig | Settings | None = None, settings: Settings | None = None) -> None:
        # Accept either OllamaClientConfig or raw Settings (compat: get_llm_client passes Settings)
        if isinstance(config, Settings) or (config is None and settings is not None):
            actual_settings = config if isinstance(config, Settings) else settings  # type: ignore
            assert actual_settings is not None
            config = OllamaClientConfig.from_settings(actual_settings)
        elif config is None:
            # Fallback for tests that construct via __new__ and set attributes manually
            raise TypeError("SageOllamaClient requires a config or Settings")
        self.config = config
        backends = config.all_backends()
        if not backends:
            backends = [(config.host, config.api_key)]
        self._backends = backends
        self._clients: list[AsyncClient] = []
        self._client_labels: list[str] = []
        for host, key in backends:
            self._clients.append(self._build_client(host, key))
            label = f"{host} (key …{key[-4:]})" if len(backends) > 1 and key else host
            self._client_labels.append(label)
        self._rr_index = 0
        self._rr_lock = asyncio.Lock()
        self._status_lock = asyncio.Lock()
        self._status_cache: dict[str, Any] | None = None
        self._status_cache_at = 0.0
        self.bot: Any = None
        # Sage SSE inflight (session_id → {event, active}) — kept here so turn.py
        # can call llm.begin_inflight / is_inflight even though bread has no such concept.
        self._inflight: dict[str, dict] = {}
        self._probe_cache: tuple[float, tuple[bool, str]] | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "SageOllamaClient":
        return cls(OllamaClientConfig.from_settings(settings))

    # -- Sage inflight compat (used by SessionService.turn) --
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
            try:
                logger.info("cancellation requested for session %s", session_id)
            except Exception:
                pass

    @property
    def model(self) -> str:
        return self.config.model

    def attach_bot(self, bot: Any) -> None:
        self.bot = bot

    def _build_client(self, host: str, api_key: str) -> AsyncClient:
        kwargs: dict[str, Any] = {"host": host, "timeout": self.config.request_timeout}
        if api_key:
            kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
        return AsyncClient(**kwargs)

    async def _next_client(self) -> tuple[AsyncClient, str]:
        if len(self._clients) == 1:
            return self._clients[0], self._client_labels[0]
        async with self._rr_lock:
            idx = self._rr_index % len(self._clients)
            self._rr_index += 1
            return self._clients[idx], self._client_labels[idx]

    async def check_connection(self) -> bool:
        client, _label = await self._next_client()
        try:
            await client.list()
            return True
        except Exception as exc:
            try:
                logger.error(f"Ollama connection check failed: type={type(exc).__name__} error={exc!r}")
            except Exception:
                pass
            return False

    async def status_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        if (
            self._status_cache is not None
            and now - self._status_cache_at < self.config.status_cache_seconds
        ):
            return self._status_cache
        async with self._status_lock:
            now = time.monotonic()
            if (
                self._status_cache is not None
                and now - self._status_cache_at < self.config.status_cache_seconds
            ):
                return self._status_cache
            results: list[dict[str, Any]] = []
            for index, client in enumerate(self._clients):
                label = self._client_labels[index]
                result: dict[str, Any] = {"backend": label, "available": False}
                version_method = getattr(client, "version", None)
                if callable(version_method):
                    try:
                        version = await version_method()
                        result["version"] = _dump_sdk_value(version)
                        result["available"] = True
                    except Exception as exc:
                        result["version_error"] = f"{type(exc).__name__}: {exc}"
                for method_name, result_key in (("list", "models"), ("ps", "running")):
                    try:
                        response = await getattr(client, method_name)()
                        result[result_key] = _dump_sdk_value(response)
                        result["available"] = True
                    except Exception as exc:
                        result[result_key] = []
                        result[f"{result_key}_error"] = f"{type(exc).__name__}: {exc}"
                if self.config.model:
                    try:
                        details = await client.show(self.config.model)
                        result["configured_model_details"] = _compact_model_details(details)
                    except Exception as exc:
                        result["configured_model_details_error"] = f"{type(exc).__name__}: {exc}"
                results.append(result)
            self._status_cache = {
                "configured_model": self.config.model,
                "backends": results,
                "thinking": self.config.thinking,
                "thinking_level": self.config.thinking_level,
                "streaming": self.config.streaming,
                "logprobs": self.config.logprobs,
                "top_logprobs": self.config.top_logprobs,
                "status_cache_seconds": self.config.status_cache_seconds,
            }
            self._status_cache_at = time.monotonic()
            return self._status_cache

    async def close(self) -> None:
        for client in self._clients:
            try:
                await client.close()
            except Exception:
                pass

    # -- canonical bread API: chat_stream(LLMMessage/ToolSchema → StreamChunk) --
    def chat_stream(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSchema] | None = None,
        model: str | None = None,
        images: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        return self._chat_stream_generator(messages, tools, model, images, kwargs)

    async def _chat_stream_generator(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSchema] | None,
        model: str | None,
        images: list[str] | None,
        kwargs: dict[str, Any],
    ) -> AsyncIterator[StreamChunk]:
        if not hasattr(self, "config") or getattr(self, "config", None) is None:
            # Test harness constructs via __new__ and only sets _settings.
            # Force streaming=True: these clients exercise the stream path explicitly.
            try:
                from app.config import Settings as _S
                _settings = getattr(self, "_settings", None) or _S(brot_api_key="test-key")
                cfg = OllamaClientConfig.from_settings(_settings)  # type: ignore
                cfg.streaming = True
                self.config = cfg
            except Exception:
                self.config = OllamaClientConfig(host="http://localhost", model="test", streaming=True)  # type: ignore
        target_model = model or self.config.model
        force_images = bool(kwargs.get("force_images", False))
        has_message_images = any(message.images for message in messages)
        attempt = ChatAttempt(
            use_tools=bool(tools),
            use_think=bool(self.config.thinking or self.config.thinking_level.strip()),
            use_images=(bool(images) or has_message_images)
            and (self.config.image_processing_enabled or force_images),
            use_format="format" in kwargs,
            use_logprobs=self.config.logprobs or "logprobs" in kwargs or "top_logprobs" in kwargs,
        )
        last_error: Exception | None = None
        for _ in range(self.config.max_retries):
            try:
                async for chunk in self._run_attempt(
                    messages=messages,
                    tools=tools,
                    model=target_model,
                    images=images,
                    attempt=attempt,
                    extra_kwargs=kwargs,
                ):
                    yield chunk
                return
            except _FeatureUnsupportedError as exc:
                if exc.feature not in attempt.disabled_features():
                    attempt = attempt.without(exc.feature)
                    last_error = exc
                    continue
                last_error = exc
            except ResponseError as exc:
                feature = _detect_unsupported_feature(str(exc))
                if feature is not None and feature not in attempt.disabled_features():
                    attempt = attempt.without(feature)
                    last_error = exc
                    continue
                if exc.status_code in RETRYABLE_STATUS_CODES:
                    last_error = exc
                    continue
                self._record_error_event(
                    target_model,
                    exc,
                    images=images,
                    tools=tools,
                    think=attempt.use_think,
                )
                last_error = exc
                break
        if last_error is not None:
            yield StreamChunk(
                content="",
                done=True,
                finish_reason="error",
                raw={"error": str(last_error)},
            )

    async def _run_attempt(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSchema] | None,
        model: str,
        images: list[str] | None,
        attempt: ChatAttempt,
        extra_kwargs: dict[str, Any],
    ) -> AsyncIterator[StreamChunk]:
        ollama_messages = self._build_messages(
            messages,
            images if attempt.use_images else None,
            include_message_images=attempt.use_images,
        )
        tool_dicts = self.build_tool_schemas(tools) if (attempt.use_tools and tools) else None
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": ollama_messages,
            "options": self._build_options(),
            "keep_alive": self.config.keep_alive,
        }
        # Hybrid mode (SAGE_STREAMING=false + thinking on): ALWAYS talk to ollama
        # with stream=True, but only relay `thinking` deltas live. Content and
        # tool_calls are buffered until the final chunk so structured tool calls
        # are caught intact — the UI fake-types the finished text afterwards.
        hybrid_thinking = not getattr(self.config, "streaming", True) and attempt.use_think
        if hybrid_thinking:
            request_kwargs["stream"] = True
            stream_response = True
        extra_kwargs = dict(extra_kwargs)
        extra_kwargs.pop("force_images", None)
        temperature = extra_kwargs.pop("temperature", None)
        stream_response = extra_kwargs.pop("stream", self.config.streaming)
        if temperature is not None:
            request_kwargs["options"]["temperature"] = float(temperature)
        if attempt.use_tools and tool_dicts:
            request_kwargs["tools"] = tool_dicts
        if attempt.use_think:
            request_kwargs["think"] = self.config.thinking_level.strip() or True
        format_value = extra_kwargs.pop("format", None)
        if attempt.use_format and format_value is not None:
            request_kwargs["format"] = format_value
        if attempt.use_logprobs and (self.config.logprobs or "logprobs" in extra_kwargs):
            request_kwargs["logprobs"] = extra_kwargs.pop("logprobs", self.config.logprobs)
        if attempt.use_logprobs and (
            self.config.top_logprobs > 0 or "top_logprobs" in extra_kwargs
        ):
            request_kwargs["top_logprobs"] = extra_kwargs.pop(
                "top_logprobs", self.config.top_logprobs
            )
        if not attempt.use_logprobs:
            extra_kwargs.pop("logprobs", None)
            extra_kwargs.pop("top_logprobs", None)
        request_kwargs.update(extra_kwargs)

        if hybrid_thinking:
            async for chunk in self._hybrid_chat(request_kwargs):
                yield chunk
        elif stream_response:
            async for chunk in self._stream_chat(request_kwargs):
                yield chunk
        else:
            chunk = await self._single_chat(request_kwargs)
            yield chunk

    async def _hybrid_chat(self, request_kwargs: dict[str, Any]) -> AsyncIterator[StreamChunk]:
        """Stream thinking live; buffer content + tool_calls until done.

        Gives the UI real-time reasoning while guaranteeing tool_calls arrive
        complete (the whole reason streaming was disabled).
        """
        client, _label = await self._next_client()
        request_kwargs.pop("stream", None)  # already forced via stream=True below
        stream = await client.chat(stream=True, **request_kwargs)
        thinking_parts: list[str] = []
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        last_done = False
        usage_chunk = None
        async for response in stream:
            message = _get(response, "message")
            think_delta = _get(message, "thinking", "") or ""
            if think_delta:
                # Buffer reasoning — no live thinking chunks. The UI gets exactly
                # one stream animation (the final answer's fake typing).
                thinking_parts.append(think_delta)
            content_delta = _get(message, "content", "") or ""
            if content_delta:
                content_parts.append(content_delta)
            for tc in self._extract_tool_calls(message):
                tool_calls.append(tc)
            if bool(_get(response, "done", False)):
                last_done = True
                usage_chunk = response
                break
        final_content = "".join(content_parts)
        # Leak guard on buffered text when tools present
        if tool_calls and final_content and "call:" in final_content.lower():
            final_content = _strip_leaked_calls(final_content)
        buffered_thinking = "".join(thinking_parts)
        if buffered_thinking:
            # Single consolidated reasoning chunk — no live streaming of thoughts
            yield StreamChunk(
                content="",
                thinking=buffered_thinking,
                tool_calls=[],
                done=False,
                finish_reason=None,
            )
        yield StreamChunk(
            content=final_content,
            thinking="",
            tool_calls=tool_calls,
            done=last_done,
            finish_reason=self._infer_finish_reason(last_done, tool_calls, final_content),
            raw=None,
        )

    async def _stream_chat(self, request_kwargs: dict[str, Any]) -> AsyncIterator[StreamChunk]:
        if hasattr(self, "_client") and not hasattr(self, "_clients"):
            # old-test harness: single fake client stored as _client
            try:
                stream = await self._client.chat(stream=True, **request_kwargs)  # type: ignore
            except Exception:
                raise
            async for response in stream:
                chunk = self._response_to_chunk(response)
                yield chunk
                if chunk.done:
                    return
            return
        client, label = await self._next_client()
        try:
            stream = await client.chat(stream=True, **request_kwargs)
        except Exception:
            raise
        async for response in stream:
            chunk = self._response_to_chunk(response)
            yield chunk
            if chunk.done:
                return

    async def _single_chat(self, request_kwargs: dict[str, Any]) -> StreamChunk:
        request_kwargs["stream"] = False
        if hasattr(self, "_client") and not hasattr(self, "_clients"):
            try:
                response = await self._client.chat(**request_kwargs)  # type: ignore
            except Exception:
                raise
            return self._response_to_chunk(response)
        client, label = await self._next_client()
        try:
            response = await client.chat(**request_kwargs)
        except Exception:
            raise
        return self._response_to_chunk(response)

    def _build_options(self) -> dict[str, Any]:
        return {
            "num_ctx": self.config.num_ctx,
            "num_thread": self.config.num_threads,
            "temperature": self.config.temperature,
        }

    def _build_messages(
        self,
        messages: list[LLMMessage],
        images: list[str] | None,
        include_message_images: bool = True,
    ) -> list[dict[str, Any]]:
        ollama_messages: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            entry = self._message_to_dict(message, include_images=include_message_images)
            if message.role == "user" and index == len(messages) - 1 and images:
                entry["images"] = list(images)
            ollama_messages.append(entry)
        return ollama_messages

    def _message_to_dict(self, message: LLMMessage, include_images: bool = True) -> dict[str, Any]:
        entry: dict[str, Any] = {"role": message.role}
        if message.content:
            entry["content"] = message.content
        if message.thinking:
            entry["thinking"] = message.thinking
        if include_images and message.images:
            entry["images"] = list(message.images)
        if message.tool_calls:
            entry["tool_calls"] = [
                {"function": {"name": call.name, "arguments": call.arguments}}
                for call in message.tool_calls
            ]
        if message.role == "tool":
            if message.tool_name:
                entry["tool_name"] = message.tool_name
            if message.tool_call_id:
                entry["tool_call_id"] = message.tool_call_id
        return entry

    def _response_to_chunk(self, response: Any) -> StreamChunk:
        message = _get(response, "message")
        content = _get(message, "content", "") or ""
        thinking = _get(message, "thinking", "") or ""
        tool_calls = self._extract_tool_calls(message)
        # If the model leaked a text-form call alongside structured tool_calls, strip it.
        if tool_calls and content and "call:" in content.lower():
            content = _strip_leaked_calls(content)
        done = bool(_get(response, "done", False))
        finish_reason = self._infer_finish_reason(done, tool_calls, content)
        usage = LLMUsage(
            model=_optional_text(_get(response, "model")),
            total_duration_ns=_optional_int(_get(response, "total_duration")),
            load_duration_ns=_optional_int(_get(response, "load_duration")),
            prompt_eval_count=_optional_int(_get(response, "prompt_eval_count")),
            prompt_eval_duration_ns=_optional_int(_get(response, "prompt_eval_duration")),
            eval_count=_optional_int(_get(response, "eval_count")),
            eval_duration_ns=_optional_int(_get(response, "eval_duration")),
            logprobs=_coerce_logprobs(_get(response, "logprobs")),
        )
        has_usage = any(
            value is not None
            for value in (
                usage.model,
                usage.total_duration_ns,
                usage.load_duration_ns,
                usage.prompt_eval_count,
                usage.prompt_eval_duration_ns,
                usage.eval_count,
                usage.eval_duration_ns,
            )
        ) or bool(usage.logprobs)
        raw = None
        if self.config.debug_raw:
            try:
                raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)
            except Exception:
                raw = None
        return StreamChunk(
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
            done=done,
            finish_reason=finish_reason,
            usage=usage if has_usage else None,
            raw=raw,
        )

    def _extract_tool_calls(self, message: Any) -> list[ToolCall]:
        raw_calls = _get(message, "tool_calls")
        if not raw_calls:
            return []
        calls: list[ToolCall] = []
        for index, raw_call in enumerate(raw_calls):
            function = _get(raw_call, "function")
            if function is None:
                continue
            name = _get(function, "name", "") or ""
            if not name:
                continue
            arguments = _get(function, "arguments", {}) or {}
            if isinstance(arguments, str):
                arguments = _parse_arguments(arguments)
            elif not isinstance(arguments, dict):
                arguments = {}
            call_id = _get(raw_call, "id", "") or f"call_{index + 1}"
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        return calls

    def _infer_finish_reason(
        self,
        done: bool,
        tool_calls: list[ToolCall],
        content: str,
    ) -> FinishReason | None:
        if not done:
            return None
        if tool_calls:
            return "tool_calls"
        if not content:
            return "length"
        return "stop"

    def _record_error_event(
        self,
        model: str,
        exc: Exception,
        images: list[str] | None = None,
        tools: list[ToolSchema] | None = None,
        think: bool = False,
    ) -> None:
        bot = getattr(self, "bot", None)
        log_event = getattr(bot, "log_event", None)
        if not callable(log_event):
            return
        status_code = getattr(exc, "status_code", None)
        error_body = getattr(exc, "error", None)
        try:
            log_event(
                "error",
                f"LLM error: type={type(exc).__name__} error={exc!r}",
                source="ollama",
                model=model,
                status_code=status_code,
                error_body=error_body,
                had_images=bool(images),
                had_tools=bool(tools),
                had_think=think,
            )
        except Exception:
            pass

    # -- Sage-compat wrappers (dict messages/tools → StreamChunk events) --
    @staticmethod
    def _dict_to_llm_messages(messages: list[dict]) -> list[LLMMessage]:
        out: list[LLMMessage] = []
        for m in messages:
            role = m.get("role", "user")
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"
            tool_calls: list[ToolCall] = []
            for c in (m.get("tool_calls") or []):
                fn = c.get("function") or {}
                name = fn.get("name") or ""
                if not name:
                    continue
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except Exception:
                        args = {}
                tool_calls.append(ToolCall(id=c.get("id") or f"call_{len(tool_calls)+1}", name=name, arguments=args if isinstance(args, dict) else {}))
            out.append(LLMMessage(
                role=role,  # type: ignore
                content=m.get("content") or "",
                images=list(m.get("images") or []),
                tool_calls=tool_calls,
                tool_call_id=m.get("tool_call_id"),
                tool_name=m.get("tool_name"),
            ))
        return out

    @staticmethod
    def _dict_to_tool_schemas(tools: list[dict] | None) -> list[ToolSchema] | None:
        if not tools:
            return None
        out: list[ToolSchema] = []
        for t in tools:
            fn = t.get("function") or t
            name = fn.get("name") or ""
            if not name:
                continue
            out.append(ToolSchema(name=name, description=fn.get("description") or "", parameters=fn.get("parameters") or {"type": "object", "properties": {}}))
        return out or None

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1500,
        temperature: float = 0.3,
        session_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
        tools: list[dict] | None = None,
    ):
        """Sage's turn.py calls this. Mirrors old client's as_events contract:
        yield plain strings when tools is None, dict events when tools is set."""
        llm_messages = self._dict_to_llm_messages(messages)
        tool_schemas = self._dict_to_tool_schemas(tools)
        as_events = tools is not None
        async for chunk in self.chat_stream(llm_messages, tool_schemas, temperature=temperature):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled("Generation cancelled by the user or a client disconnect.")
            # Reasoning streams live: every thinking delta forwarded as it arrives
            if chunk.thinking:
                yield {"type": "thinking", "thinking": chunk.thinking}
            if chunk.tool_calls:
                # Pre-tool text: the model's short lead-in sentence ("Let me check...").
                # Emit it BEFORE the tool_call event so the UI shows text → tool activity.
                pre_text = _strip_leaked_calls(chunk.content or "")
                if pre_text.strip():
                    yield {"type": "delta", "delta": pre_text} if as_events else pre_text
                for tc in chunk.tool_calls:
                    raw = [{"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}]
                    yield {
                        "type": "tool_call",
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "raw_tool_calls": raw,
                        "assistant_content": pre_text or None,
                    }
            elif chunk.content:
                cleaned = _strip_leaked_calls(chunk.content)
                if not cleaned:
                    continue
                if as_events:
                    yield {"type": "delta", "delta": cleaned}
                else:
                    yield cleaned
            if chunk.done and chunk.finish_reason == "error":
                raise ProviderError("upstream", str((chunk.raw or {}).get("error") or "upstream error"))

    async def complete_json(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> tuple[str | None, str | None]:
        llm_messages = self._dict_to_llm_messages(messages)
        # Use single (non-stream) path with format=json, like bread's generate helpers.
        collected = ""
        async for chunk in self.chat_stream(llm_messages, None, temperature=temperature, format="json", stream=False):  # type: ignore
            collected += chunk.content or ""
            if chunk.done:
                break
        text = collected.strip() if collected else None
        # Strip leaked markup even from JSON path (shouldn't happen, but cheap)
        if text:
            text = _strip_leaked_calls(text)
        return (text, None) if text else (None, "empty_response")

    async def quick_probe(self) -> tuple[bool, str]:
        # Cached reachability probe (bread doesn't have this; sage's health check uses it)
        import time as _time
        now = _time.monotonic()
        if self._probe_cache and now - self._probe_cache[0] < 30:
            return self._probe_cache[1]
        ok = await self.check_connection()
        result: tuple[bool, str] = (ok, "ok" if ok else "ollama unreachable")
        self._probe_cache = (now, result)
        return result


# Back-compat alias — turn.py and tests import LLMClient
LLMClient = SageOllamaClient

