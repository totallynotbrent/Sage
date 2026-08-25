from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
from ollama import ResponseError

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.tools import available_tools, execute_tool
from app.services.plans import PlansService
from app.services.sessions import SessionService
from app.services.sessions.turn import ToolContext
from tests.fakes.fake_llm import FakeLLM


async def _collect(generator):
    return [event async for event in generator]


async def _never_disconnected() -> bool:
    return False


def _tool_settings(settings, searxng_url: str = ""):
    return settings.model_copy(update={"searxng_url": searxng_url})


_LEARNING_TOOL_NAMES = {
    "run_probe",
    "grade_answer",
    "build_plan",
    "advance_lesson",
}

_PROBE_JSON = json.dumps(
    {
        "questions": [
            {
                "question": "What does a recursive function do?",
                "options": ["Calls itself", "Loops forever", "Returns None"],
                "correct_index": 0,
                "explanation": "A recursive function invokes itself.",
                "topic": "recursion",
                "difficulty": 2,
            },
            {
                "question": "What does every recursion need?",
                "options": ["A base case", "A global variable"],
                "correct_index": 0,
                "explanation": "Without a base case it never stops.",
                "topic": "recursion",
                "difficulty": 3,
            },
            {
                "question": "Which structure is naturally recursive?",
                "options": ["A file tree", "A single integer"],
                "correct_index": 0,
                "explanation": "Trees contain smaller trees.",
                "topic": "recursion",
                "difficulty": 4,
            },
        ]
    }
)

_PLAN_JSON = json.dumps(
    {
        "nodes": [
            {
                "node_key": "n1",
                "title": "What recursion is",
                "description": "Define self-reference.",
                "depends_on": [],
            },
            {
                "node_key": "n2",
                "title": "The base case",
                "description": "Stop the descent.",
                "depends_on": ["n1"],
            },
            {
                "node_key": "n3",
                "title": "The call stack",
                "description": "How frames pile up.",
                "depends_on": ["n2"],
            },
        ]
    }
)

_CHECK_JSON = json.dumps(
    {
        "questions": [
            {
                "question": "Which part stops a recursion?",
                "options": ["The base case", "The loop counter"],
                "correct_index": 0,
                "explanation": "The base case terminates the chain.",
                "topic": "The call stack",
                "difficulty": 3,
            }
        ]
    }
)


async def _stub_validate_mermaid(source: str) -> str:
    return "flowchart-v2"


def _learning_ctx(conn, settings, fake_llm):
    service = SessionService(conn, settings)
    session = service.create(
        goal="learn recursion", file_ids=[], grounding_mode="grounded"
    )
    ctx = ToolContext(
        settings=settings,
        session_dict=session.model_dump(),
        chunks=[],
        mastery_summary="",
        mode="grounded",
        llm=fake_llm,
        conn=conn,
        session_id=session.id,
        mermaid_validate=_stub_validate_mermaid,
    )
    return session, ctx


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
        *_LEARNING_TOOL_NAMES,
    }
    assert _LEARNING_TOOL_NAMES <= names_without
    assert "web_search" not in names_without
    assert len(names_without) == 9


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
                "arguments": {
                    "query": "what is recursion",
                    "status": "Looking up sources...",
                },
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
    assert tool_call_event["arguments"] == {
        "query": "what is recursion",
        "status": "Looking up sources...",
    }
    assert tool_call_event["status"] == "Looking up sources..."

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


def test_execute_tool_learning_tools_require_conn():
    ctx = ToolContext(
        settings=SimpleNamespace(context_chunk_budget=8),
        session_dict={},
        chunks=[],
        mastery_summary="",
        mode="grounded",
        llm=FakeLLM(),
    )

    for name in ("run_probe", "grade_answer", "build_plan", "advance_lesson"):
        result = asyncio.run(execute_tool(name, {}, ctx))
        assert result == {"error": "unavailable_in_context"}


def test_execute_tool_run_probe_strips_correct_index(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PROBE_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)

    result = asyncio.run(execute_tool("run_probe", {}, ctx))

    assert result["phase"] == "probe"
    assert result["count"] == 3
    assert len(result["questions"]) == 3
    for question in result["questions"]:
        assert set(question) == {"id", "question", "options", "difficulty"}
        assert question["options"][-1] == "I don't know"
    assert SessionService(conn, settings).get(session.id).phase == "probe"


def test_execute_tool_grade_answer_passthrough(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PROBE_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    probe = asyncio.run(execute_tool("run_probe", {}, ctx))
    first_id = probe["questions"][0]["id"]
    second_id = probe["questions"][1]["id"]
    third_id = probe["questions"][2]["id"]

    graded = asyncio.run(
        execute_tool("grade_answer", {"question_id": first_id, "choice_index": 0}, ctx)
    )
    assert set(graded) == {
        "question_id",
        "outcome",
        "correct_index",
        "explanation",
        "probe_complete",
        "retry_allowed",
        "next_node",
        "check_due",
        "summary",
    }
    assert graded["outcome"] == "correct"
    assert graded["summary"] == "graded correct"
    assert graded["correct_index"] == 0
    assert graded["probe_complete"] is False
    assert graded["retry_allowed"] is False

    idk = asyncio.run(
        execute_tool("grade_answer", {"question_id": second_id, "idk": True}, ctx)
    )
    assert idk["outcome"] == "idk"
    assert idk["retry_allowed"] is True

    wrong = asyncio.run(
        execute_tool("grade_answer", {"question_id": third_id, "choice_index": 1}, ctx)
    )
    assert wrong["outcome"] == "incorrect"
    assert wrong["probe_complete"] is True


def test_execute_tool_grade_answer_unknown_question_surfaces_not_found(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PROBE_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    probe = asyncio.run(execute_tool("run_probe", {}, ctx))
    assert (
        probe["grading_hint"]
        == "Grade replies with grade_answer using these exact question_id values."
    )

    missing = asyncio.run(
        execute_tool(
            "grade_answer",
            {"question_id": "deadbeef000042deadbeef00000042", "choice_index": 0},
            ctx,
        )
    )

    assert missing == {"error": "not_found"}


def test_execute_tool_build_plan_returns_stripped_nodes(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PLAN_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)

    result = asyncio.run(execute_tool("build_plan", {}, ctx))

    assert result["phase"] == "plan"
    assert [node["node_key"] for node in result["nodes"]] == ["n1", "n2", "n3"]
    for node in result["nodes"]:
        assert set(node) == {
            "node_key",
            "title",
            "depends_on",
            "status",
            "position",
        }
    assert result["nodes"][1]["depends_on"] == ["n1"]
    assert all(node["status"] == "pending" for node in result["nodes"])
    plan_diagram = result["plan_diagram"]
    assert plan_diagram["diagram_type"] == "flowchart-v2"
    assert plan_diagram["source"].startswith("flowchart TD")
    assert "n1[What recursion is]" in plan_diagram["source"]
    assert "n2[The base case]" in plan_diagram["source"]
    assert "n1 --> n2" in plan_diagram["source"]
    assert SessionService(conn, settings).get(session.id).phase == "plan"


def test_execute_tool_advance_lesson_enters_first_node_from_plan(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PLAN_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    asyncio.run(execute_tool("build_plan", {}, ctx))

    entered = asyncio.run(execute_tool("advance_lesson", {"passed_check": True}, ctx))

    assert entered["advanced"] is True
    assert entered["node"]["node_key"] == "n1"
    assert entered["check_due"] is False
    assert entered["session_phase"] == "teach"
    refreshed = SessionService(conn, settings).get(session.id)
    assert refreshed.phase == "teach"
    assert refreshed.nodes_since_check == 0


def test_execute_tool_advance_lesson_failed_check_routes_to_remediation(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PLAN_JSON)
    fake_llm.complete_json_responses.append(_CHECK_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    asyncio.run(execute_tool("build_plan", {}, ctx))
    PlansService(conn, settings).approve(session.id)

    result = asyncio.run(execute_tool("advance_lesson", {"passed_check": False}, ctx))

    assert result["advanced"] is False
    assert result["node"]["node_key"] == "n1"
    assert result["check_due"] is False
    assert result["session_phase"] == "remediate"
    check_question = result["check_question"]
    assert set(check_question) == {"id", "question", "options"}
    assert check_question["options"][-1] == "I don't know"
    refreshed = SessionService(conn, settings).get(session.id)
    assert refreshed.phase == "check"
    row = conn.execute(
        "SELECT status FROM quiz_questions "
        "WHERE session_id = ? AND kind = 'check' AND status = 'pending'",
        (session.id,),
    ).fetchone()
    assert row is not None
    plan_row = conn.execute(
        "SELECT status FROM plan_nodes WHERE session_id = ? AND node_key = 'n1'",
        (session.id,),
    ).fetchone()
    assert plan_row["status"] == "current"


def test_execute_tool_advance_lesson_appends_check_question_when_due(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PLAN_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    asyncio.run(execute_tool("build_plan", {}, ctx))
    plans = PlansService(conn, settings)
    plans.approve(session.id)

    first = asyncio.run(execute_tool("advance_lesson", {"passed_check": True}, ctx))
    assert first["advanced"] is True
    assert first["node"]["node_key"] == "n2"
    assert first["check_due"] is False
    assert "check_question" not in first

    fake_llm.complete_json_responses.append(_CHECK_JSON)
    due = asyncio.run(execute_tool("advance_lesson", {"passed_check": True}, ctx))

    assert due["advanced"] is True
    assert due["node"]["node_key"] == "n3"
    assert due["check_due"] is True
    check_question = due["check_question"]
    assert set(check_question) == {"id", "question", "options"}
    assert check_question["options"][-1] == "I don't know"
    assert due["session_phase"] == "teach"
    assert SessionService(conn, settings).get(session.id).phase == "check"


class _scripted_stream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        raise StopAsyncIteration


def _ollama_chunk(content):
    return SimpleNamespace(
        done=False,
        message=SimpleNamespace(content=content, tool_calls=None),
    )


class _ollama_flaky:
    """Mimics ollama AsyncClient: tools rejected with 400 -> retry without."""

    def __init__(self, chunk):
        self.chat_calls: list[dict] = []
        self._chunk = chunk

    async def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        if kwargs.get("tools"):
            raise ResponseError(
                "the model does not support tools", status_code=400
            )
        return _scripted_stream([self._chunk])


def test_stream_chat_falls_back_without_tools_on_400(caplog):
    stub = _ollama_flaky(_ollama_chunk("hello"))

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

    assert len(stub.chat_calls) == 2
    assert "tools" in stub.chat_calls[0]
    assert "tools" not in stub.chat_calls[1]
    assert events == [{"type": "delta", "delta": "hello"}]
    assert not any(e.get("type") == "tool_call" for e in events)


def test_stream_chat_plain_path_stays_strings():
    async def chat(**kwargs):
        return _scripted_stream([_ollama_chunk("plain")])

    stub = SimpleNamespace(chat=chat)
    client = LLMClient.__new__(LLMClient)
    object.__setattr__(client, "_client", stub)
    object.__setattr__(
        client, "_settings", Settings(brot_api_key="test-key", brot_model="test-model")
    )

    chunks = asyncio.run(
        _collect(client.stream_chat([{"role": "user", "content": "hi"}]))
    )
    assert chunks == ["plain"]
