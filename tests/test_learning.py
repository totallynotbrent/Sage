from __future__ import annotations

import asyncio
import json

import pytest

from app.errors import ModelOutputError, NotFoundError, ProviderError
from app.services.learning import LearningService
from app.services.sessions import SessionService
from tests.fakes.fake_llm import FakeLLM


def _create_session(conn, settings, goal="learn algebra"):
    return SessionService(conn, settings).create(goal, [])


def _good_questions():
    return [
        {
            "question": f"What is Q{i}?",
            "options": ["a", "b", "c"],
            "correct_index": 1,
            "explanation": f"explanation {i}",
            "topic": f"topic-{i}",
            "difficulty": 2,
        }
        for i in range(3)
    ]


def _probed_session(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [json.dumps(_good_questions())]
    result = asyncio.run(LearningService(conn, settings).generate_probe(session.id, fake_llm))
    return session, result["questions"]


def test_generate_probe_creates_three_questions(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    assert len(questions) == 3
    for question in questions:
        assert question["kind"] == "probe"
        assert question["status"] == "pending"
        assert question["options"][-1] == "I don't know"
        assert question["correct_index"] == 1
    assert len(fake_llm.calls) == 1


def test_generate_probe_sets_phase(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    assert session.phase == "setup"
    fake_llm.complete_json_responses = [json.dumps(_good_questions())]
    result = asyncio.run(LearningService(conn, settings).generate_probe(session.id, fake_llm))
    assert result["session"]["phase"] == "probe"


def test_generate_probe_sends_focus(conn, settings, fake_llm):
    session = _create_session(conn, settings, goal="learn group theory")
    fake_llm.complete_json_responses = [json.dumps(_good_questions())]
    asyncio.run(LearningService(conn, settings).generate_probe(session.id, fake_llm))
    user_messages = [
        m["content"]
        for call in fake_llm.calls
        for m in call["messages"]
        if m.get("role") == "user"
    ]
    assert any("Focus on: learn group theory" in text for text in user_messages)


def test_generate_probe_idempotent(conn, settings, fake_llm):
    session, first_questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    second = asyncio.run(service.generate_probe(session.id, fake_llm))
    assert [q["id"] for q in second["questions"]] == [q["id"] for q in first_questions]
    assert len(fake_llm.calls) == 1


def test_generate_probe_no_usable_questions_raises(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = ["garbage", "garbage"]
    with pytest.raises(ModelOutputError) as excinfo:
        asyncio.run(LearningService(conn, settings).generate_probe(session.id, fake_llm))
    assert excinfo.value.status_code == 422
    assert excinfo.value.retryable is True


def test_generate_probe_missing_session(conn, settings, fake_llm):
    with pytest.raises(NotFoundError):
        asyncio.run(LearningService(conn, settings).generate_probe("nope", fake_llm))


def test_generate_probe_provider_failure_raises_502(conn, settings):
    session = _create_session(conn, settings)

    class FailingFakeLLM(FakeLLM):
        async def complete_json(self, messages, *, max_tokens=1200, temperature=0.1):
            return (None, "connection: Could not connect to the model endpoint.")

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(LearningService(conn, settings).generate_probe(session.id, FailingFakeLLM()))
    assert excinfo.value.code == "connection"
    assert excinfo.value.status_code == 502


def test_answer_correct_grades_and_records_mastery(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    result = service.answer_quiz(session.id, questions[0]["id"], 1)
    assert result["result"]["question_id"] == questions[0]["id"]
    assert result["result"]["outcome"] == "correct"
    assert result["result"]["correct_index"] == 1
    assert result["result"]["explanation"] == "explanation 0"
    assert result["result"]["probe_complete"] is False
    assert result["result"]["retry_allowed"] is False

    row = conn.execute("SELECT * FROM mastery_topics WHERE topic = 'topic-0'").fetchone()
    assert row["observed_count"] == 1
    assert row["correct_count"] == 1
    stored = conn.execute(
        "SELECT * FROM quiz_questions WHERE id = ?", (questions[0]["id"],)
    ).fetchone()
    assert stored["status"] == "answered"
    assert stored["user_choice"] == 1
    assert stored["outcome"] == "correct"


def test_answer_incorrect_allows_retry(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    result = service.answer_quiz(session.id, questions[0]["id"], 0)
    assert result["result"]["outcome"] == "incorrect"
    assert result["result"]["retry_allowed"] is True
    assert result["session"]["phase"] == "probe"


def test_answer_idk_stores_minus_one(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    result = service.answer_quiz(session.id, questions[0]["id"], idk=True)
    assert result["result"]["outcome"] == "idk"
    stored = conn.execute(
        "SELECT * FROM quiz_questions WHERE id = ?", (questions[0]["id"],)
    ).fetchone()
    assert stored["user_choice"] == -1
    assert stored["outcome"] == "idk"
    row = conn.execute("SELECT * FROM mastery_topics WHERE topic = 'topic-0'").fetchone()
    assert row["idk_count"] == 1


def test_answer_last_option_is_idk(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    idk_index = len(questions[0]["options"]) - 1
    result = service.answer_quiz(session.id, questions[0]["id"], idk_index)
    assert result["result"]["outcome"] == "idk"


def test_answer_replay_does_not_double_count(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    first = service.answer_quiz(session.id, questions[0]["id"], 1)
    second = service.answer_quiz(session.id, questions[0]["id"], 1)
    assert second["result"]["outcome"] == "correct"
    assert second["result"]["question_id"] == first["result"]["question_id"]
    row = conn.execute("SELECT * FROM mastery_topics WHERE topic = 'topic-0'").fetchone()
    assert row["observed_count"] == 1
    assert row["correct_count"] == 1
    evidence = json.loads(row["evidence_json"])
    assert len(evidence) == 1


def test_answer_correct_then_different_choice_rejected(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    service.answer_quiz(session.id, questions[0]["id"], 1)
    with pytest.raises(ValueError) as excinfo:
        service.answer_quiz(session.id, questions[0]["id"], 0)
    assert "already answered correctly" in str(excinfo.value)
    row = conn.execute("SELECT * FROM mastery_topics WHERE topic = 'topic-0'").fetchone()
    assert row["observed_count"] == 1
    assert row["correct_count"] == 1


def test_answer_incorrect_then_correct_retry_regrades(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    service.answer_quiz(session.id, questions[0]["id"], 0)
    result = service.answer_quiz(session.id, questions[0]["id"], 1)
    assert result["result"]["outcome"] == "correct"
    assert result["result"]["retry_allowed"] is False
    row = conn.execute("SELECT * FROM mastery_topics WHERE topic = 'topic-0'").fetchone()
    assert row["observed_count"] == 2
    assert row["correct_count"] == 1
    evidence = json.loads(row["evidence_json"])
    assert len(evidence) == 2


def test_answer_out_of_range_choice_raises(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    with pytest.raises(ValueError):
        LearningService(conn, settings).answer_quiz(session.id, questions[0]["id"], 99)


def test_answer_unknown_question_404(conn, settings):
    session = _create_session(conn, settings)
    with pytest.raises(NotFoundError):
        LearningService(conn, settings).answer_quiz(session.id, "nope", 0)


def test_probe_complete_after_all_answered(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    for question in questions:
        result = service.answer_quiz(session.id, question["id"], 1)
    assert result["result"]["probe_complete"] is True
    assert result["session"]["phase"] == "probe"


def test_generate_check_uses_node_topic(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    conn.execute(
        "INSERT INTO plan_nodes (id, session_id, node_key, title, description, depends_on_json, status, position) VALUES (?, ?, 'n1', 'Group Theory', NULL, '[]', 'current', 0)",
        ("node1", session.id),
    )
    conn.execute("UPDATE sessions SET current_node_id = 'node1' WHERE id = ?", (session.id,))
    conn.commit()
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Check Q?",
                    "options": ["x", "y"],
                    "correct_index": 0,
                    "explanation": "e",
                    "topic": "model-topic",
                    "difficulty": 3,
                }
            ]
        )
    ]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_check(session.id, fake_llm))
    assert result["session"]["phase"] == "check"
    assert len(result["questions"]) == 1
    question = result["questions"][0]
    assert question["kind"] == "check"
    assert question["topic"] == "Group Theory"
    assert question["options"][-1] == "I don't know"


def test_generate_check_falls_back_to_goal_topic(conn, settings, fake_llm):
    session = _create_session(conn, settings, goal="learn algebra")
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Check Q?",
                    "options": ["x", "y"],
                    "correct_index": 0,
                    "explanation": "e",
                    "topic": None,
                    "difficulty": 3,
                }
            ]
        )
    ]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_check(session.id, fake_llm))
    assert result["questions"][0]["topic"] == "learn algebra"


def test_wrong_check_answer_sets_remediate(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Check Q?",
                    "options": ["x", "y"],
                    "correct_index": 0,
                    "explanation": "e",
                    "topic": None,
                    "difficulty": 3,
                }
            ]
        )
    ]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_check(session.id, fake_llm))
    question = result["questions"][0]
    answer = service.answer_quiz(session.id, question["id"], 1)
    assert answer["result"]["outcome"] == "incorrect"
    assert answer["session"]["phase"] == "remediate"
    evidence = json.loads(
        conn.execute(
            "SELECT evidence_json FROM mastery_topics WHERE topic = 'learn algebra'"
        ).fetchone()[0]
    )
    assert evidence[0]["source"] == "check"


def test_correct_check_answer_advances_session(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Check Q?",
                    "options": ["x", "y"],
                    "correct_index": 0,
                    "explanation": "e",
                    "topic": None,
                    "difficulty": 3,
                }
            ]
        )
    ]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_check(session.id, fake_llm))
    question = result["questions"][0]
    answer = service.answer_quiz(session.id, question["id"], 0)
    assert answer["result"]["outcome"] == "correct"
    assert answer["result"]["next_node"] is None
    assert answer["result"]["check_due"] is False
    assert answer["session"]["phase"] == "complete"


def test_check_generation_idempotent(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Check Q?",
                    "options": ["x", "y"],
                    "correct_index": 0,
                    "explanation": "e",
                    "topic": None,
                    "difficulty": 3,
                }
            ]
        )
    ]
    service = LearningService(conn, settings)
    first = asyncio.run(service.generate_check(session.id, fake_llm))
    second = asyncio.run(service.generate_check(session.id, fake_llm))
    assert [q["id"] for q in second["questions"]] == [q["id"] for q in first["questions"]]
    assert len(fake_llm.calls) == 1


def test_answer_corrupt_options_json_raises_clean_value_error(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, question, options_json, correct_index, status, created_at) "
        "VALUES ('q-broken', ?, 'probe', 'Q?', 'not-json', 0, 'pending', '2026-01-01T00:00:00Z')",
        (session.id,),
    )
    conn.commit()
    with pytest.raises(ValueError) as excinfo:
        LearningService(conn, settings).answer_quiz(session.id, "q-broken", 0)
    assert "corrupted" in str(excinfo.value)
