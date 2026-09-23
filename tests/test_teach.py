from __future__ import annotations

import asyncio
import json

import pytest

from app.errors import ModelOutputError, NotFoundError, ProviderError
from app.services.sessions import SessionService
from app.services.teach import TeachService
from tests.fakes.fake_llm import FakeLLM


def _create_session(conn, settings):
    return SessionService(conn, settings).create("learn algebra", [])


def _teach_session(conn, settings, phase="teach"):
    session = _create_session(conn, settings)
    now = "2026-01-01T00:00:00Z"
    for index, (node_id, node_key, title) in enumerate(
        [("n1", "k1", "Basics"), ("n2", "k2", "Advanced"), ("n3", "k3", "Expert")]
    ):
        conn.execute(
            "INSERT INTO plan_nodes (id, session_id, node_key, title, description, "
            "depends_on_json, status, position, children_json) "
            "VALUES (?, ?, ?, ?, NULL, '[]', 'pending', ?, '[]')",
            (node_id, session.id, node_key, title, index),
        )
    conn.execute("UPDATE plan_nodes SET status = 'current' WHERE id = 'n1'")
    conn.execute(
        "UPDATE sessions SET phase = ?, current_node_id = 'n1' WHERE id = ?",
        (phase, session.id),
    )
    conn.commit()
    return session


def _answered_check_question(conn, session_id):
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, topic, question, options_json, "
        "correct_index, explanation, status, user_choice, outcome, created_at) "
        "VALUES ('q1', ?, 'check', 'topic', 'Q?', '[\"a\", \"b\", \"I don''t know\"]', 1, "
        "'expl', 'answered', 0, 'incorrect', '2026-01-01T00:00:00Z')",
        (session_id,),
    )
    conn.commit()


def test_advance_marks_done_and_moves(conn, settings):
    session = _teach_session(conn, settings)
    result = TeachService(conn, settings).advance(session.id)
    assert result["node"]["id"] == "n2"
    assert result["node"]["status"] == "current"
    assert result["check_due"] is False
    n1 = conn.execute("SELECT * FROM plan_nodes WHERE id = 'n1'").fetchone()
    assert n1["status"] == "done"
    assert result["session"]["phase"] == "teach"
    assert result["session"]["nodes_since_check"] == 1


def test_advance_respects_dependencies(conn, settings):
    # a positionally-earlier pending node with unfinished deps must be skipped
    # for the first node whose dependencies are all done
    session = _create_session(conn, settings)
    nodes = [
        ("d1", "k1", "Basics", "[]", 0, "done"),
        ("d2", "k2", "Advanced", '["k1"]', 1, "pending"),
        ("d3", "k3", "Expert", '["k1", "k2"]', 2, "pending"),
        ("d4", "k4", "Applied", "[]", 3, "pending"),
    ]
    for node_id, node_key, title, deps, position, status in nodes:
        conn.execute(
            "INSERT INTO plan_nodes (id, session_id, node_key, title, description, "
            "depends_on_json, status, position, children_json) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, '[]')",
            (node_id, session.id, node_key, title, deps, status, position),
        )
    conn.execute(
        "UPDATE sessions SET phase = 'teach', current_node_id = 'd1' WHERE id = ?",
        (session.id,),
    )
    conn.commit()

    # k2 is positionally first but pending deps are done (k1 done) so it is
    # eligible; but if k2 were still pending itself, k3 (deps k1+k2) must wait
    result = TeachService(conn, settings).advance(session.id)
    assert result["node"]["node_key"] == "k2"

    # now k2 done; k3 deps satisfied, and it must win over nothing-else
    conn.execute("UPDATE plan_nodes SET status = 'done' WHERE node_key = 'k2'")
    conn.execute("UPDATE plan_nodes SET status = 'current' WHERE node_key = 'k2'")
    conn.execute(
        "UPDATE sessions SET current_node_id = (SELECT id FROM plan_nodes WHERE node_key = 'k2') WHERE id = ?",
        (session.id,),
    )
    conn.commit()
    result = TeachService(conn, settings).advance(session.id)
    assert result["node"]["node_key"] == "k4" or result["node"]["node_key"] == "k3"
    # k3 depends on k1+k2 (both done) so k3 is eligible and positionally first
    assert result["node"]["node_key"] == "k3"


def test_advance_never_flags_check_due(conn, settings):
    session = _teach_session(conn, settings)
    service = TeachService(conn, settings)
    first = service.advance(session.id)
    assert first["check_due"] is False
    second = service.advance(session.id)
    assert second["node"]["id"] == "n3"
    assert second["check_due"] is False


def test_advance_completes_plan(conn, settings):
    session = _teach_session(conn, settings)
    service = TeachService(conn, settings)
    service.advance(session.id)
    service.advance(session.id)
    result = service.advance(session.id)
    assert result["node"] is None
    assert result["check_due"] is False
    assert result["session"]["phase"] == "complete"
    assert result["session"]["current_node_id"] is None


def test_advance_requires_teach_phase(conn, settings):
    session = _create_session(conn, settings)
    with pytest.raises(ValueError):
        TeachService(conn, settings).advance(session.id)


def test_continue_from_remediate_resets_counter(conn, settings):
    session = _teach_session(conn, settings, phase="remediate")
    result = TeachService(conn, settings).continue_after_remediate(session.id)
    assert result["node"]["id"] == "n2"
    assert result["session"]["phase"] == "teach"
    assert result["session"]["nodes_since_check"] == 0


def test_continue_requires_remediate_phase(conn, settings):
    session = _teach_session(conn, settings, phase="teach")
    with pytest.raises(ValueError):
        TeachService(conn, settings).continue_after_remediate(session.id)


def test_complete_sets_phase(conn, settings):
    session = _teach_session(conn, settings)
    result = TeachService(conn, settings).complete(session.id)
    assert result["session"]["phase"] == "complete"


def test_hint_records_feedback_action(conn, settings, fake_llm):
    session = _teach_session(conn, settings)
    _answered_check_question(conn, session.id)
    fake_llm.complete_json_responses = ["Think about the first step."]
    result = asyncio.run(TeachService(conn, settings).hint(session.id, "q1", fake_llm))
    assert result["hint"] == "Think about the first step."
    row = conn.execute(
        "SELECT * FROM feedback_actions WHERE session_id = ? AND question_id = 'q1'",
        (session.id,),
    ).fetchone()
    assert row["action"] == "hint"
    assert row["content"] == "Think about the first step."


def test_hint_requires_answered(conn, settings, fake_llm):
    session = _teach_session(conn, settings)
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, question, options_json, "
        "correct_index, status, created_at) "
        "VALUES ('q-pending', ?, 'check', 'Q?', '[]', 0, 'pending', '2026-01-01T00:00:00Z')",
        (session.id,),
    )
    conn.commit()
    with pytest.raises(ValueError):
        asyncio.run(TeachService(conn, settings).hint(session.id, "q-pending", fake_llm))


def test_hint_unknown_question_404(conn, settings, fake_llm):
    session = _teach_session(conn, settings)
    with pytest.raises(NotFoundError):
        asyncio.run(TeachService(conn, settings).hint(session.id, "ghost", fake_llm))


def test_hint_provider_failure_raises_502(conn, settings):
    session = _teach_session(conn, settings)
    _answered_check_question(conn, session.id)

    class FailingFakeLLM(FakeLLM):
        async def complete_json(self, messages, *, max_tokens=1200, temperature=0.1):
            self.calls.append({"kind": "complete_json", "messages": list(messages)})
            return (None, "connection: Could not connect to the model endpoint.")

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(TeachService(conn, settings).hint(session.id, "q1", FailingFakeLLM()))
    assert excinfo.value.code == "connection"
    assert excinfo.value.status_code == 502


def test_hint_empty_output_raises_422(conn, settings, fake_llm):
    session = _teach_session(conn, settings)
    _answered_check_question(conn, session.id)
    fake_llm.complete_json_responses = [""]
    with pytest.raises(ModelOutputError) as excinfo:
        asyncio.run(TeachService(conn, settings).hint(session.id, "q1", fake_llm))
    assert excinfo.value.status_code == 422
    assert excinfo.value.retryable is True


def test_reveal_returns_correct_option(conn, settings):
    session = _teach_session(conn, settings)
    _answered_check_question(conn, session.id)
    result = TeachService(conn, settings).reveal(session.id, "q1")
    assert result["question_id"] == "q1"
    assert result["correct_index"] == 1
    assert result["correct_option"] == "b"
    assert result["explanation"] == "expl"
    row = conn.execute(
        "SELECT * FROM feedback_actions WHERE session_id = ? AND question_id = 'q1'",
        (session.id,),
    ).fetchone()
    assert row["action"] == "reveal"
    assert row["content"] == "b"


def test_reveal_requires_answered(conn, settings):
    session = _teach_session(conn, settings)
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, question, options_json, "
        "correct_index, status, created_at) "
        "VALUES ('q-pending', ?, 'check', 'Q?', '[]', 0, 'pending', '2026-01-01T00:00:00Z')",
        (session.id,),
    )
    conn.commit()
    with pytest.raises(ValueError):
        TeachService(conn, settings).reveal(session.id, "q-pending")


def test_skip_quiz_returns_to_plan(conn, settings):
    session = _teach_session(conn, settings)
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, question, options_json, "
        "correct_index, status, created_at) "
        "VALUES ('q1', ?, 'check', 'Q?', '[]', 0, 'pending', '2026-01-01T00:00:00Z')",
        (session.id,),
    )
    conn.execute("UPDATE sessions SET nodes_since_check = 2 WHERE id = ?", (session.id,))
    conn.commit()
    result = TeachService(conn, settings).skip_quiz(session.id, "q1")
    row = conn.execute("SELECT * FROM quiz_questions WHERE id = 'q1'").fetchone()
    assert row["status"] == "skipped"
    assert result["session"]["phase"] == "plan"
    assert result["session"]["nodes_since_check"] == 0


def test_skip_quiz_requires_check_kind(conn, settings):
    session = _teach_session(conn, settings)
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, question, options_json, "
        "correct_index, status, created_at) "
        "VALUES ('q1', ?, 'probe', 'Q?', '[]', 0, 'pending', '2026-01-01T00:00:00Z')",
        (session.id,),
    )
    conn.commit()
    with pytest.raises(ValueError):
        TeachService(conn, settings).skip_quiz(session.id, "q1")


def test_skip_quiz_unknown_question_404(conn, settings):
    session = _teach_session(conn, settings)
    with pytest.raises(NotFoundError):
        TeachService(conn, settings).skip_quiz(session.id, "ghost")
