from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
from openai import APIStatusError

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.tools import available_tools, execute_tool
from app.services.sessions import SessionService
from app.services.sessions.turn import ToolContext
from tests.fakes.fake_llm import FakeLLM


async def _collect(generator):
    return [event async for event in generator]


async def _never_disconnected() -> bool:
    return False


def _tool_settings(settings, searxng_url: str = ""):
    return settings.model_copy(update={"searxng_url": searxng_url})


def test_available_tools_gates_web_search_on_searxng():
    with_search = available_tools(SimpleNamespace(searxng_url="http://searx.test"))
    without_search = available_tools(SimpleNamespace(searxng_url=""))

    names_with = {tool["function"]["name"] for tool in with_search}
    names_without = {tool["function"]["name"] for tool in without_search}

    assert names_with == {
        "web_search",
        "generate_mermaid",
        "generate_quiz",
        "generate_todo",
        "generate_latex",
        "record_step_actions",
    }
    assert "web_search" not in names_without
    assert len(names_without) == 5


def test_turn_tool_loop_event_order_and_followup(conn, settings, monkeypatch):
    tool_settings = _tool_settings(settings, searxng_url="http://searx.test")
    service = SessionService(conn, tool_settings)
    session = service.create(
        goal="learn recursion", file_ids=[], grounding_mode="grounded"
    )
    fake_llm = FakeLLM()

    searches: list[dict] = []

    async def fake_search(base_url, query, max_results=5):
        searches.append(
            {"base_url": base_url, "query": query, "max_results": max_results}
        )
        return [
            {
                "title": "Wiki noise",
                "url": "https://wikidiff.com/recursion",
                "content": "junk",
            },
            {
                "title": "Recursion explained",
                "url": "https://example.com/recursion",
                "content": "self-reference",
            },
        ]

    monkeypatch.setattr("app.services.web_search.search_web", fake_search)
    fake_llm.script_tool_events(
        [
            {
                "type": "tool_call",
                "name": "web_search",
                "arguments": {"query": "what is recursion"},
                "id": "call_ws",
            }
        ]
    )
    fake_llm.script("recursion", "Recursion is self-reference.")

    events = asyncio.run(
        _collect(
            service.turn(
                session.id,
                "Explain recursion",
                client_msg_id="c1",
                llm=fake_llm,
                is_disconnected=_never_disconnected,
            )
        )
    )

    types = [event["type"] for event in events]
    assert types[0] == "meta"
    assert types.index("tool_call") < types.index("tool_result")
    assert types.index("tool_result") < types.index("delta")
    assert types[-1] == "done"

    meta = events[0]
    assert meta["web_sources"] == []
    assert meta["insufficient"] is False

    tool_call_event = next(e for e in events if e["type"] == "tool_call")
    assert tool_call_event["name"] == "web_search"
    assert tool_call_event["arguments"] == {"query": "what is recursion"}

    tool_result_event = next(e for e in events if e["type"] == "tool_result")
    assert tool_result_event["summary"] == [
        {"title": "Recursion explained", "url": "https://example.com/recursion"}
    ]

    done = events[-1]
    assert done["replayed"] is False
    assert done["actions"] == []
    assert done["web_sources"] == [
        {"title": "Recursion explained", "url": "https://example.com/recursion"}
    ]

    assert len(searches) == 1
    assert searches[0]["base_url"] == "http://searx.test"
    assert searches[0]["query"] == "what is recursion"
    assert searches[0]["max_results"] == 5

    stream_calls = [c for c in fake_llm.calls if c["kind"] == "stream"]
    assert len(stream_calls) >= 2
    followup_messages = stream_calls[1]["messages"]
    assistant_echo = next(
        m
        for m in followup_messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert assistant_echo["tool_calls"][0]["id"] == "call_ws"
    assert assistant_echo["tool_calls"][0]["function"]["name"] == "web_search"
    tool_message = next(m for m in followup_messages if m.get("role") == "tool")
    assert tool_message["tool_call_id"] == "call_ws"
    payload = json.loads(tool_message["content"])
    assert [r["title"] for r in payload["results"]] == ["Recursion explained"]
    assert tool_message["content"].count("wikidiff") == 0


def test_turn_stashes_record_step_actions(conn, settings):
    service = SessionService(conn, _tool_settings(settings))
    session = service.create(
        goal="learn recursion", file_ids=[], grounding_mode="grounded"
    )
    fake_llm = FakeLLM()
    fake_llm.script_tool_events(
        [
            {
                "type": "tool_call",
                "name": "record_step_actions",
                "arguments": {
                    "actions": [
                        {"id": "continue", "label": "Continue", "prompt": "Go on"},
                        {"id": "practice", "label": "Practice", "prompt": "Try one"},
                    ]
                },
                "id": "call_act",
            }
        ]
    )
    fake_llm.script("recursion", "Step taught.")

    events = asyncio.run(
        _collect(
            service.turn(
                session.id,
                "Teach me a step",
                client_msg_id="c2",
                llm=fake_llm,
                is_disconnected=_never_disconnected,
            )
        )
    )

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["summary"] == "2 actions"

    done = events[-1]
    assert [a["id"] for a in done["actions"]] == ["continue", "practice"]
    assert done["actions"][0]["label"] == "Continue"


def test_turn_generate_mermaid_tool_result_card(conn, settings, monkeypatch):
    service = SessionService(conn, _tool_settings(settings))
    session = service.create(goal="learn trees", file_ids=[], grounding_mode="grounded")
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(
        json.dumps({"title": "Call Stack", "source": "graph TD\n A-->B"})
    )

    async def fake_validate(source: str) -> str:
        return "flowchart"

    monkeypatch.setattr("app.services.mermaid.validate_mermaid", fake_validate)
    fake_llm.script_tool_events(
        [
            {
                "type": "tool_call",
                "name": "generate_mermaid",
                "arguments": {"topic": "call stack"},
                "id": "call_mm",
            }
        ]
    )
    fake_llm.script("trees", "The diagram above shows the structure.")

    events = asyncio.run(
        _collect(
            service.turn(
                session.id,
                "Show me a diagram of the call stack",
                client_msg_id="c3",
                llm=fake_llm,
                is_disconnected=_never_disconnected,
            )
        )
    )

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["kind"] == "mermaid"
    assert tool_result["title"] == "Call Stack"
    assert tool_result["diagram_type"] == "flowchart"
    assert "A-->B" in tool_result["source"]
    assert tool_result["summary"] == "mermaid ok"


def test_execute_tool_generate_mermaid_uses_validate_fn():
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(
        json.dumps({"title": "Tree", "source": "```mermaid\ngraph TD\n A-->B\n```"})
    )

    async def fake_mermaid(source: str) -> str:
        return "flowchart"

    ctx = ToolContext(
        settings=SimpleNamespace(context_chunk_budget=8),
        session_dict={"goal": "learn trees"},
        chunks=[],
        mastery_summary="mastery",
        mode="grounded",
        llm=fake_llm,
        validate_fn=None,
        mermaid_validate=fake_mermaid,
    )

    result = asyncio.run(execute_tool("generate_mermaid", {"topic": "trees"}, ctx))

    assert result["kind"] == "mermaid"
    assert result["diagram_type"] == "flowchart"
    assert result["source"] == "graph TD\n A-->B"
    assert result["title"] == "Tree"
    complete_calls = [c for c in fake_llm.calls if c["kind"] == "complete_json"]
    assert len(complete_calls) == 1
    roles = [m["role"] for m in complete_calls[0]["messages"]]
    assert roles == ["system", "user"]
    user_message = complete_calls[0]["messages"][1]
    assert "output_kind=mermaid about: trees" in user_message["content"]
    assert '"source"' in user_message["content"]
    assert "exact JSON shape" in user_message["content"]


def test_execute_tool_quiz_count_mismatch_reports_issue_code():
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(
        json.dumps(
            {
                "questions": [
                    {"question": "q?", "options": ["a", "b"], "correct_index": 0}
                ]
            }
        )
    )

    ctx = ToolContext(
        settings=SimpleNamespace(context_chunk_budget=8),
        session_dict={},
        chunks=[],
        mastery_summary="",
        mode="grounded",
        llm=fake_llm,
    )

    result = asyncio.run(execute_tool("generate_quiz", {"topic": "t", "count": 5}, ctx))
    assert result == {"error": "quiz_count"}


def test_execute_tool_generate_todo_repairs_after_empty_output():
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append("")
    fake_llm.complete_json_responses.append(
        json.dumps({"title": "Recursion plan", "items": [{"text": "Trace one call"}]})
    )

    ctx = ToolContext(
        settings=SimpleNamespace(context_chunk_budget=8),
        session_dict={},
        chunks=[],
        mastery_summary="",
        mode="grounded",
        llm=fake_llm,
    )

    result = asyncio.run(execute_tool("generate_todo", {"topic": "recursion"}, ctx))

    assert result == {
        "kind": "todo",
        "title": "Recursion plan",
        "items": [{"text": "Trace one call", "done": False}],
    }
    complete_calls = [c for c in fake_llm.calls if c["kind"] == "complete_json"]
    assert len(fake_llm.calls) == 2
    assert len(complete_calls) == 2
    first_user = complete_calls[0]["messages"][1]
    assert "output_kind=todo about: recursion" in first_user["content"]
    assert "MUST stay within the current lesson thread" in first_user["content"]
    assert len(complete_calls[1]["messages"]) == 4
    assistant_echo = complete_calls[1]["messages"][2]
    assert assistant_echo == {
        "role": "assistant",
        "content": "(no usable answer: empty_output)",
    }
    repair_note = complete_calls[1]["messages"][3]
    assert repair_note["role"] == "user"
    assert repair_note["content"] == (
        "Your previous answer could not be used (empty_output). "
        "Return ONLY corrected JSON for output_kind=todo about recursion, "
        'EXACTLY this shape: {"title":"...","items":[{"text":"...","done":false}]}'
    )


def test_execute_tool_record_step_actions_validation():
    ctx = ToolContext(
        settings=SimpleNamespace(context_chunk_budget=8),
        session_dict={},
        chunks=[],
        mastery_summary="",
        mode="grounded",
        llm=FakeLLM(),
    )

    bad = asyncio.run(
        execute_tool(
            "record_step_actions",
            {"actions": [{"id": "dance", "label": "Dance", "prompt": "spin"}]},
            ctx,
        )
    )
    assert bad == {"error": "invalid_action"}

    good = asyncio.run(
        execute_tool(
            "record_step_actions",
            {"actions": [{"id": "deeper", "label": "Go deeper", "prompt": "why?"}]},
            ctx,
        )
    )
    assert good == {
        "actions": [{"id": "deeper", "label": "Go deeper", "prompt": "why?"}]
    }


class _scripted_stream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        raise StopAsyncIteration


class _flaky_completions:
    def __init__(self, chunk):
        self.create_calls: list[dict] = []
        self._chunk = chunk

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if kwargs.get("tools"):
            request = httpx.Request("POST", "http://endpoint.test/v1/chat/completions")
            response = httpx.Response(400, request=request)
            raise APIStatusError(
                "tools are not supported", response=response, body=None
            )
        return _scripted_stream([self._chunk])


def test_stream_chat_falls_back_without_tools_on_400(caplog):
    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content="hello"), finish_reason=None)
        ]
    )
    completions = _flaky_completions(chunk)
    stub = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    client = LLMClient.__new__(LLMClient)
    object.__setattr__(client, "_client", stub)
    object.__setattr__(
        client, "_settings", Settings(brot_api_key="test-key", brot_model="test-model")
    )

    tools = [{"type": "function", "function": {"name": "web_search"}}]

    with caplog.at_level(logging.WARNING, logger="app"):
        events = asyncio.run(
            _collect(
                client.stream_chat([{"role": "user", "content": "hi"}], tools=tools)
            )
        )

    assert len(completions.create_calls) == 2
    assert "tools" in completions.create_calls[0]
    assert "tools" not in completions.create_calls[1]
    assert events == [{"type": "delta", "delta": "hello"}]
    assert not any(e.get("type") == "tool_call" for e in events)
    assert any("tools unsupported" in record.message for record in caplog.records)


def test_stream_chat_plain_path_stays_strings():
    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content="plain"), finish_reason=None)
        ]
    )

    async def create(**kwargs):
        return _scripted_stream([chunk])

    stub = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    client = LLMClient.__new__(LLMClient)
    object.__setattr__(client, "_client", stub)
    object.__setattr__(
        client, "_settings", Settings(brot_api_key="test-key", brot_model="test-model")
    )

    chunks = asyncio.run(
        _collect(client.stream_chat([{"role": "user", "content": "hi"}]))
    )
    assert chunks == ["plain"]
