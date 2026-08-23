from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Awaitable, Callable

from app.errors import GenerationCancelled, ProviderError


class FakeLLM:
    def __init__(self) -> None:
        self._scripted: list[tuple[str, str]] = []
        self.complete_json_responses: list[str] = []
        self.probe_result: tuple[bool, str] = (True, "ok")
        self.fail_after: int | None = None
        self.failure: Exception = ProviderError("upstream", "fake mid-stream failure")
        self.calls: list[dict] = []
        self.tool_scripts: list[list[dict]] = []
        self._inflight: dict[str, asyncio.Event] = {}

    def script(self, substring: str, text: str) -> None:
        self._scripted.append((substring, text))

    def script_tool_events(self, events: list[dict]) -> None:
        self.tool_scripts.append(list(events))

    def _match(self, messages: list[dict]) -> str:
        joined = "\n".join(
            (str(m.get("content") or ""))
            for m in messages
            if m.get("role") in ("user", "system")
        )
        for substring, text in reversed(self._scripted):
            if substring in joined:
                return text
        return "A default fake answer."

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
        self.calls.append(
            {
                "kind": "stream",
                "session_id": session_id,
                "messages": list(messages),
                "tools": list(tools) if tools else None,
            }
        )
        if tools:
            scripted = self.tool_scripts.pop(0) if self.tool_scripts else None
            if scripted is not None:
                for event in scripted:
                    if cancel_event is not None and cancel_event.is_set():
                        raise GenerationCancelled("fake: cancelled mid-stream")
                    if event.get("type") == "tool_call":
                        name = event["name"]
                        arguments = event.get("arguments", {})
                        call_id = event.get("id", f"call_{name}")
                        raw_arguments = (
                            arguments
                            if isinstance(arguments, str)
                            else json.dumps(arguments)
                        )
                        wire_call = {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": raw_arguments},
                        }
                        yield {
                            "type": "tool_call",
                            "id": call_id,
                            "name": name,
                            "arguments": arguments,
                            "raw_tool_calls": [wire_call],
                            "assistant_content": "",
                        }
                    else:
                        yield {"type": "delta", "delta": event.get("delta", "")}
                return
        text = self._match(messages)
        if not text:
            return
        words = text.split(" ")
        for index, word in enumerate(words):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled("fake: cancelled mid-stream")
            if self.fail_after is not None and index >= self.fail_after:
                raise self.failure
            piece = word + (" " if index < len(words) - 1 else "")
            yield {"type": "delta", "delta": piece} if tools else piece

    async def complete_json(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> tuple[str | None, str | None]:
        self.calls.append({"kind": "complete_json", "messages": list(messages)})
        if self.complete_json_responses:
            return (self.complete_json_responses.pop(0), None)
        return (self._match(messages), None)

    async def quick_probe(self) -> tuple[bool, str]:
        self.calls.append({"kind": "probe"})
        return self.probe_result

    def begin_inflight(self, session_id: str) -> asyncio.Event:
        if session_id not in self._inflight:
            self._inflight[session_id] = asyncio.Event()
        return self._inflight[session_id]

    def end_inflight(self, session_id: str) -> None:
        self._inflight.pop(session_id, None)

    def is_inflight(self, session_id: str) -> bool:
        return session_id in self._inflight

    def get_inflight_event(self, session_id: str) -> asyncio.Event | None:
        return self._inflight.get(session_id)

    def cancel_inflight(self, session_id: str) -> None:
        event = self._inflight.get(session_id)
        if event:
            event.set()


class RaisingFakeLLM(FakeLLM):
    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self.stream_error: Exception = error or ProviderError(
            "upstream", "fake upstream error"
        )

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
        self.calls.append(
            {"kind": "stream", "session_id": session_id, "messages": list(messages)}
        )
        raise self.stream_error
