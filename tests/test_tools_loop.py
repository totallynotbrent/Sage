from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
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
    "run_final_quiz",
    "start_review",
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
        "generate_quiz",
        "generate_todo",
        "generate_latex",
        "record_step_actions",
        *_LEARNING_TOOL_NAMES,
    }
    assert _LEARNING_TOOL_NAMES <= names_without
    assert "generate_mermaid" not in names_without
    assert "web_search" not in names_without
    assert len(names_without) == 10


def test_available_tools_strict_drops_web_search_keeps_teaching():
    settings = SimpleNamespace(searxng_url="http://searx.test")
    strict = {t["function"]["name"] for t in available_tools(settings, mode="strict")}
    grounded = {t["function"]["name"] for t in available_tools(settings, mode="grounded")}
    assert "web_search" in grounded
    assert "web_search" not in strict
    assert {"run_probe", "build_plan", "advance_lesson", "run_final_quiz", "grade_answer"} <= strict


def test_strict_prompt_is_pdf_first_and_no_web():
    from app.llm.messages import make_system_prompt

    session = {"goal": "learn calculus", "phase": "teach", "current_node_id": "n1"}
    strict = make_system_prompt(session, "strict", "")
    grounded = make_system_prompt(session, "grounded", "")
    assert "Teach from the uploaded PDF first" in strict
    assert "cannot search the web" in strict
    assert "Teach from the uploaded PDF first" not in grounded


def test_execute_tool_start_review_returns_due_cards(conn, settings, fake_llm):
    # Issue #3: review is model-triggered via the start_review tool. It must
    # return this session's due cards (with their original question) so the UI
    # can render a review batch from the model's tool call.
    import asyncio as _aio

    from app.services import review as review_service

    session, ctx = _learning_ctx(conn, settings, fake_llm)
    # Register a question + a due (new) card for this session.
    review_service.register_card(
        conn, session.id, "recursion", "q-due", "check", "idk"
    )
    result = _aio.run(execute_tool("start_review", {}, ctx))
    assert result.get("due_count") == 1
    cards = result.get("cards") or []
    assert len(cards) == 1
    assert cards[0]["session_id"] == session.id
    assert cards[0]["card_id"]


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


def test_execute_tool_run_final_quiz_reasks_probe_and_spans(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.extend([_PROBE_JSON, _PLAN_JSON])
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    probe = asyncio.run(execute_tool("run_probe", {}, ctx))
    probe_count = probe["count"]
    asyncio.run(execute_tool("build_plan", {}, ctx))
    PlansService(conn, settings).approve(session.id)

    fake_llm.complete_json_responses.append(_CHECK_JSON)
    result = asyncio.run(execute_tool("run_final_quiz", {}, ctx))

    assert result["phase"] == "final_quiz"
    assert result["count"] == len(result["questions"])
    # It re-asks this session's probe questions, plus fresh covering ones.
    assert result["count"] >= probe_count + 1
    assert SessionService(conn, settings).get(session.id).phase == "final_quiz"


def test_run_final_quiz_dedupes_duplicate_stems(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.extend(
        [_PROBE_JSON, _PLAN_JSON, _CHECK_JSON, _CHECK_JSON]
    )
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    asyncio.run(execute_tool("run_probe", {}, ctx))
    asyncio.run(execute_tool("build_plan", {}, ctx))
    PlansService(conn, settings).approve(session.id)
    r1 = asyncio.run(execute_tool("run_final_quiz", {}, ctx))
    first_total = conn.execute(
        "SELECT COUNT(*) FROM quiz_questions WHERE session_id=? AND kind='final'",
        (session.id,),
    ).fetchone()[0]
    # Mark round 1 answered so round 2 regenerates instead of returning pending.
    for q in r1["questions"]:
        conn.execute(
            "UPDATE quiz_questions SET status='answered' WHERE id=?", (q["id"],)
        )
    conn.commit()
    asyncio.run(execute_tool("run_final_quiz", {}, ctx))
    rows = conn.execute(
        "SELECT question FROM quiz_questions WHERE session_id=? AND kind='final'",
        (session.id,),
    ).fetchall()
    stems = [str(r["question"]).strip().lower() for r in rows]
    assert len(stems) == len(set(stems)), "final quiz contains duplicate stems"
    assert len(stems) == first_total, "final quiz regrew duplicate questions"


def test_execute_tool_removed_mermaid_returns_unsupported():
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(json.dumps({"title": "Tree"}))

    ctx = ToolContext(
        settings=SimpleNamespace(context_chunk_budget=8),
        session_dict={"goal": "learn trees"},
        chunks=[],
        mastery_summary="mastery",
        mode="grounded",
        llm=fake_llm,
        validate_fn=None,
        mermaid_validate=None,
    )

    result = asyncio.run(execute_tool("generate_mermaid", {"topic": "trees"}, ctx))

    # The mermaid tool is gone; calling it surfaces a generation error, not a
    # diagram payload.
    assert result.get("error")


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


def test_execute_tool_grade_answer_resolves_ordinal_and_prefix(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PROBE_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    probe = asyncio.run(execute_tool("run_probe", {}, ctx))
    first_id = probe["questions"][0]["id"]

    # ordinal "1" resolves to the first presented question (grade lands)
    graded = asyncio.run(
        execute_tool(
            "grade_answer", {"question_id": "1", "choice_index": 0}, ctx
        )
    )
    assert graded["outcome"] == "correct"
    assert graded["question_id"] == first_id

    # an unambiguous prefix also resolves
    prefixed = asyncio.run(
        execute_tool(
            "grade_answer",
            {"question_id": first_id[:8], "choice_index": 0},
            ctx,
        )
    )
    assert prefixed["outcome"] == "correct"
    assert prefixed["question_id"] == first_id


def test_slim_tool_schemas_are_valid(settings):
    tools = available_tools(settings, lightweight=True)
    assert tools
    for tool in tools:
        fn = tool["function"]
        assert tool["type"] == "function"
        assert fn["name"].strip() and fn["description"].strip()
        props = fn["parameters"]["properties"]
        assert isinstance(props, dict)  # arg-less tools have empty props by design
        assert "status" not in props
        for req in fn["parameters"].get("required", []):
            assert req in props, f"{fn['name']} requires missing prop {req}"


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
    # The plan no longer ships a mermaid flowchart (removed per user request).
    assert "plan_diagram" not in result
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


def test_execute_tool_advance_lesson_advances_without_checks(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PLAN_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    asyncio.run(execute_tool("build_plan", {}, ctx))
    PlansService(conn, settings).approve(session.id)

    result = asyncio.run(execute_tool("advance_lesson", {}, ctx))

    assert result["advanced"] is True
    assert result["node"]["node_key"] == "n2"
    assert result["check_due"] is False
    assert "check_questions" not in result
    assert "check_question" not in result
    assert result["session_phase"] == "teach"
    refreshed = SessionService(conn, settings).get(session.id)
    assert refreshed.phase == "teach"


def test_execute_tool_advance_lesson_never_emits_check_questions(conn, settings):
    fake_llm = FakeLLM()
    fake_llm.complete_json_responses.append(_PLAN_JSON)
    session, ctx = _learning_ctx(conn, settings, fake_llm)
    asyncio.run(execute_tool("build_plan", {}, ctx))
    PlansService(conn, settings).approve(session.id)

    first = asyncio.run(execute_tool("advance_lesson", {}, ctx))
    assert first["advanced"] is True
    assert first["node"]["node_key"] == "n2"
    assert first["check_due"] is False
    assert "check_question" not in first

    fake_llm.complete_json_responses.append(_CHECK_JSON)
    second = asyncio.run(execute_tool("advance_lesson", {}, ctx))

    assert second["advanced"] is True
    assert second["node"]["node_key"] == "n3"
    # No intermediate check is ever emitted regardless of node count.
    assert second["check_due"] is False
    assert "check_question" not in second
    assert "check_questions" not in second
    assert second["session_phase"] == "teach"
    assert SessionService(conn, settings).get(session.id).phase == "teach"


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
        client, "_settings", Settings(api_key="test-key", model="test-model")
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
        client, "_settings", Settings(api_key="test-key", model="test-model")
    )

    chunks = asyncio.run(
        _collect(client.stream_chat([{"role": "user", "content": "hi"}]))
    )
    assert chunks == ["plain"]


def test_tool_only_turn_gets_fallback_prose(conn, settings, monkeypatch):
    tool_settings = _tool_settings(settings, searxng_url="http://searx.test")
    tool_settings = tool_settings.model_copy(update={"streaming": False})
    service = SessionService(conn, tool_settings)
    session = service.create(
        goal="learn recursion", file_ids=[], grounding_mode="grounded"
    )
    fake_llm = FakeLLM()

    async def fake_search(base_url, query, max_results=5):
        return [{"title": "Recursion", "url": "https://example.com/r", "content": "self"}]

    monkeypatch.setattr("app.services.web_search.search_web", fake_search)
    # Tool call, then the follow-up text pass yields EMPTY text (tool-only turn).
    fake_llm.script_tool_events(
        [
            {
                "type": "tool_call",
                "name": "web_search",
                "arguments": {"query": "what is recursion", "status": "Searching..."},
                "id": "call_ws",
            }
        ]
    )
    fake_llm.script("Explain recursion", "")
    # Fallback completion returns prose.
    fake_llm.complete_json_responses = [
        json.dumps("Recursion is when a function calls itself.")
    ]

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
    assert "tool_call" in types and "tool_result" in types
    done = events[-1]
    assert done["type"] == "done"
    # The tool-only turn got a fallback prose reply persisted (non-empty).
    assert done["message_id"] is not None
    text = "".join(e.get("text", "") for e in events if e["type"] == "buffered_text")
    assert "Recursion is when a function calls itself." in text
    check = sqlite3.connect(settings.db_path)
    check.row_factory = sqlite3.Row
    persisted = check.execute(
        "SELECT content FROM messages WHERE id = ?", (done["message_id"],)
    ).fetchone()
    check.close()
    assert persisted is not None and "Recursion is when a function calls itself." in persisted["content"]


def test_teach_turn_without_actions_gets_default_continue(conn, settings):
    tool_settings = _tool_settings(settings)
    tool_settings = tool_settings.model_copy(update={"streaming": False})
    service = SessionService(conn, tool_settings)
    session = service.create(
        goal="learn how erasers work", file_ids=[], grounding_mode="grounded"
    )
    service.set_phase(session.id, "teach")
    fake_llm = FakeLLM()
    # The model explains the node in prose and stops, never calling the
    # record_step_actions tool (the reported "no continue button" case).
    fake_llm.script("adhesion", "Adhesion lets an eraser lift graphite off paper.")

    events = asyncio.run(
        _collect(
            service.turn(
                session.id,
                "explain adhesion",
                client_msg_id="c1",
                llm=fake_llm,
                is_disconnected=_never_disconnected,
            )
        )
    )
    done = events[-1]
    assert done["type"] == "done"
    actions = done.get("actions") or []
    assert len(actions) == 1
    assert actions[0]["id"] == "continue"
    assert actions[0]["label"] == "Continue"
    assert actions[0]["prompt"] == "advance to the next idea"


def test_teach_turn_model_actions_not_overridden(conn, settings):
    tool_settings = _tool_settings(settings)
    tool_settings = tool_settings.model_copy(update={"streaming": False})
    service = SessionService(conn, tool_settings)
    session = service.create(
        goal="learn how erasers work", file_ids=[], grounding_mode="grounded"
    )
    service.set_phase(session.id, "teach")
    fake_llm = FakeLLM()
    # Same turn shape as the existing tool+text tests: the model records its
    # own action, then writes a closing reply.
    fake_llm.script_tool_events(
        [
            {
                "type": "tool_call",
                "name": "record_step_actions",
                "arguments": {
                    "actions": [
                        {"id": "next_topic", "label": "Next", "prompt": "go on"}
                    ],
                    "status": "teaching",
                },
                "id": "call_sa",
            }
        ]
    )
    fake_llm.script("go on", "Next, friction lifts the debris.")

    events = asyncio.run(
        _collect(
            service.turn(
                session.id,
                "go on",
                client_msg_id="c2",
                llm=fake_llm,
                is_disconnected=_never_disconnected,
            )
        )
    )
    done = events[-1]
    assert done["type"] == "done"
    actions = done.get("actions") or []
    assert len(actions) == 1
    assert actions[0]["id"] == "next_topic"
    assert actions[0]["label"] == "Next"
