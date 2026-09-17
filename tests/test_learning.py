from __future__ import annotations

import asyncio
import json

import pytest

from app.errors import ModelOutputError, NotFoundError, ProviderError
from app.services.learning import LearningService
from app.services.plans import PlansService
from app.services.sessions import SessionService
from app.services.teach import TeachService
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
    result = asyncio.run(
        LearningService(conn, settings).generate_probe(session.id, fake_llm)
    )
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
    result = asyncio.run(
        LearningService(conn, settings).generate_probe(session.id, fake_llm)
    )
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
        asyncio.run(
            LearningService(conn, settings).generate_probe(session.id, fake_llm)
        )
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
        asyncio.run(
            LearningService(conn, settings).generate_probe(session.id, FailingFakeLLM())
        )
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

    row = conn.execute(
        "SELECT * FROM mastery_topics WHERE topic = 'topic-0'"
    ).fetchone()
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
    row = conn.execute(
        "SELECT * FROM mastery_topics WHERE topic = 'topic-0'"
    ).fetchone()
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
    row = conn.execute(
        "SELECT * FROM mastery_topics WHERE topic = 'topic-0'"
    ).fetchone()
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
    row = conn.execute(
        "SELECT * FROM mastery_topics WHERE topic = 'topic-0'"
    ).fetchone()
    assert row["observed_count"] == 1
    assert row["correct_count"] == 1


def test_answer_incorrect_then_correct_retry_regrades(conn, settings, fake_llm):
    session, questions = _probed_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    service.answer_quiz(session.id, questions[0]["id"], 0)
    result = service.answer_quiz(session.id, questions[0]["id"], 1)
    assert result["result"]["outcome"] == "correct"
    assert result["result"]["retry_allowed"] is False
    row = conn.execute(
        "SELECT * FROM mastery_topics WHERE topic = 'topic-0'"
    ).fetchone()
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
    conn.execute(
        "UPDATE sessions SET current_node_id = 'node1' WHERE id = ?", (session.id,)
    )
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


def test_generate_pretest_uses_node_topic(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    conn.execute(
        "INSERT INTO plan_nodes (id, session_id, node_key, title, description, depends_on_json, status, position) VALUES (?, ?, 'n1', 'Group Theory', NULL, '[]', 'current', 0)",
        ("node1", session.id),
    )
    conn.execute(
        "UPDATE sessions SET current_node_id = 'node1' WHERE id = ?", (session.id,)
    )
    conn.commit()
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Pretest Q?",
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
    result = asyncio.run(service.generate_pretest(session.id, fake_llm))
    assert len(result["questions"]) == 1
    question = result["questions"][0]
    assert question["kind"] == "pretest"
    assert question["topic"] == "Group Theory"
    assert question["options"][-1] == "I don't know"
    assert question["correct_index"] == 0


def test_generate_pretest_does_not_change_phase(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Pretest Q?",
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
    result = asyncio.run(service.generate_pretest(session.id, fake_llm))
    assert result["session"]["phase"] == session.phase == "setup"


def test_generate_pretest_falls_back_to_goal_topic(conn, settings, fake_llm):
    session = _create_session(conn, settings, goal="learn algebra")
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Pretest Q?",
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
    result = asyncio.run(service.generate_pretest(session.id, fake_llm))
    assert result["questions"][0]["topic"] == "learn algebra"


def test_generate_pretest_caps_to_single_question(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(_good_questions())
    ]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_pretest(session.id, fake_llm))
    assert len(result["questions"]) == 1
    assert result["questions"][0]["kind"] == "pretest"


def test_generate_pretest_idempotent(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": "Pretest Q?",
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
    first = asyncio.run(service.generate_pretest(session.id, fake_llm))
    second = asyncio.run(service.generate_pretest(session.id, fake_llm))
    assert [q["id"] for q in second["questions"]] == [
        q["id"] for q in first["questions"]
    ]
    assert len(fake_llm.calls) == 1


def test_generate_pretest_no_usable_questions_raises(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = ["garbage", "garbage"]
    with pytest.raises(ModelOutputError) as excinfo:
        asyncio.run(LearningService(conn, settings).generate_pretest(session.id, fake_llm))
    assert excinfo.value.status_code == 422
    assert excinfo.value.retryable is True


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
    # last node's check routes to the closing quiz, not a bare complete
    assert answer["session"]["phase"] == "final_quiz"


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
    assert [q["id"] for q in second["questions"]] == [
        q["id"] for q in first["questions"]
    ]
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


def _planned_checked_session(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [
        json.dumps(
            {
                "nodes": [
                    {"node_key": "k1", "title": "Basics", "depends_on": []},
                    {"node_key": "k2", "title": "Advanced", "depends_on": ["k1"]},
                ]
            }
        )
    ]
    asyncio.run(PlansService(conn, settings).generate_plan(session.id, fake_llm))
    PlansService(conn, settings).approve(session.id)
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
    result = asyncio.run(
        LearningService(conn, settings).generate_check(session.id, fake_llm)
    )
    return session, result["questions"][0]


def test_regrade_correct_when_phase_plan_keeps_plan(conn, settings, fake_llm):
    session, question = _planned_checked_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    service.answer_quiz(session.id, question["id"], 1)
    SessionService(conn, settings).set_phase(session.id, "plan")
    node_before = conn.execute(
        "SELECT current_node_id FROM sessions WHERE id = ?", (session.id,)
    ).fetchone()[0]
    result = service.answer_quiz(session.id, question["id"], 0)
    assert result["result"]["outcome"] == "correct"
    assert result["result"]["next_node"] is None
    assert result["result"]["check_due"] is False
    assert result["session"]["phase"] == "plan"
    node_after = conn.execute(
        "SELECT current_node_id FROM sessions WHERE id = ?", (session.id,)
    ).fetchone()[0]
    assert node_after == node_before
    k2 = conn.execute(
        "SELECT status FROM plan_nodes WHERE session_id = ? AND node_key = 'k2'",
        (session.id,),
    ).fetchone()
    assert k2["status"] == "pending"


def test_regrade_correct_when_phase_complete_stays_complete(conn, settings, fake_llm):
    session, question = _planned_checked_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    service.answer_quiz(session.id, question["id"], 1)
    SessionService(conn, settings).set_phase(session.id, "complete")
    result = service.answer_quiz(session.id, question["id"], 0)
    assert result["result"]["outcome"] == "correct"
    assert result["result"]["next_node"] is None
    # a session already marked complete is never reopened by a regrade
    assert result["session"]["phase"] in ("complete", "final_quiz")


def test_regrade_correct_from_remediate_advances(conn, settings, fake_llm):
    session, question = _planned_checked_session(conn, settings, fake_llm)
    service = LearningService(conn, settings)
    first = service.answer_quiz(session.id, question["id"], 1)
    assert first["session"]["phase"] == "remediate"
    result = service.answer_quiz(session.id, question["id"], 0)
    assert result["result"]["outcome"] == "correct"
    assert result["session"]["phase"] == "teach"
    assert result["result"]["next_node"] is not None
    k2 = conn.execute(
        "SELECT id, status FROM plan_nodes WHERE session_id = ? AND node_key = 'k2'",
        (session.id,),
    ).fetchone()
    assert k2["status"] == "current"
    assert result["session"]["current_node_id"] == k2["id"]


def test_answer_skipped_question_rejected(conn, settings, fake_llm):
    session, question = _planned_checked_session(conn, settings, fake_llm)
    TeachService(conn, settings).skip_quiz(session.id, question["id"])
    with pytest.raises(ValueError) as excinfo:
        LearningService(conn, settings).answer_quiz(session.id, question["id"], 0)
    assert "skipped" in str(excinfo.value)
    stored = conn.execute(
        "SELECT status FROM quiz_questions WHERE id = ?", (question["id"],)
    ).fetchone()
    assert stored["status"] == "skipped"
    assert SessionService(conn, settings).get(session.id).phase == "plan"


def _ready_env_file(conn, file_id="f1"):
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO files (id, display_name, storage_name, mime_type, size_bytes, sha256, status, warnings, error, num_chunks, paired_file_id, subject, source_path, created_at, updated_at) "
        "VALUES (?, 'calc.tex', 'f1.tex', 'text/x-tex', 10, 'sha-f1', 'ready', '[]', NULL, 1, NULL, 'calculus', NULL, ?, ?)",
        (file_id, now, now),
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, chunk_index, text, unicode_text, environment, label, location_kind, page, slide, section, start_line, end_line, char_start, char_end) "
        "VALUES ('c0', ?, 0, 'the theorem text', 'the theorem text', 'theorem', 'thm:rolle', 'lines', NULL, NULL, 'Rolle', 1, 1, 0, 10)",
        (file_id,),
    )
    conn.commit()


def test_answer_notes_question_records_mastery_no_phase_change(
    conn, settings, fake_llm
):
    _ready_env_file(conn)
    session = SessionService(conn, settings).create("learn calculus", ["f1"])
    fake_llm.complete_json_responses = [
        json.dumps(
            {
                "question": "Notes Q?",
                "options": ["x", "y", "z"],
                "correct_index": 1,
                "explanation": "e",
                "topic": "calculus",
                "difficulty": 3,
            }
        ),
        json.dumps({"supported": True}),
    ]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_notes_quiz(session.id, fake_llm, count=1))
    question = result["questions"][0]
    assert session.phase == "setup"
    answer = service.answer_quiz(session.id, question["id"], 1)
    assert answer["result"]["outcome"] == "correct"
    assert answer["result"]["next_node"] is None
    assert answer["result"]["check_due"] is False
    assert answer["session"]["phase"] == "setup"
    row = conn.execute(
        "SELECT evidence_json FROM mastery_topics WHERE topic = 'calculus'"
    ).fetchone()
    evidence = json.loads(row[0])
    assert evidence[0]["source"] == "notes"
    assert evidence[0]["question_id"] == question["id"]


def _answer_outcome(conn, session_id, kind, outcome):
    from app.util import new_id, utc_now
    now = utc_now()
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, topic, difficulty, question, options_json, correct_index, status, user_choice, outcome, created_at, answered_at) "
        "VALUES (?, ?, ?, 'topic', 2, 'Q?', '[\"a\",\"b\"]', 0, 'answered', 0, ?, ?, ?)",
        (new_id(), session_id, kind, outcome, now, now),
    )
    conn.commit()


def test_adaptive_check_count_stalls_on_miss(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    _answer_outcome(conn, session.id, "check", "incorrect")
    service = LearningService(conn, settings)
    assert service._adaptive_question_count(session.id, "learn algebra") == 3


def test_adaptive_check_count_aces_short(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    from app.services import mastery
    mastery.record_evidence(conn, session.id, "learn algebra", "check", "correct")
    mastery.record_evidence(conn, session.id, "learn algebra", "check", "correct")
    _answer_outcome(conn, session.id, "check", "correct")
    _answer_outcome(conn, session.id, "check", "correct")
    service = LearningService(conn, settings)
    assert service._adaptive_question_count(session.id, "learn algebra") == 1


def test_adaptive_check_count_defaults_two(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    service = LearningService(conn, settings)
    assert service._adaptive_question_count(session.id, "learn algebra") == 2


def test_adaptive_check_count_slow_corrects_downgrade_acing(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    from app.services import mastery
    from app.util import new_id, utc_now

    mastery.record_evidence(conn, session.id, "learn algebra", "check", "correct")
    mastery.record_evidence(conn, session.id, "learn algebra", "check", "correct")
    now = utc_now()
    for _ in range(2):
        conn.execute(
            "INSERT INTO quiz_questions (id, session_id, kind, topic, difficulty, question, options_json, correct_index, status, user_choice, outcome, confidence, latency_ms, created_at, answered_at) "
            "VALUES (?, ?, 'check', 'learn algebra', 2, 'Slow Q?', '[\"a\",\"b\"]', 0, 'answered', 0, 'correct', 'know', 12_000, ?, ?)",
            (new_id(), session.id, now, now),
        )
    conn.commit()
    # Confidence (>= 0.6) and outcomes alone would ace; slow effortful recall
    # must downgrade the light check back to the standard 2.
    service = LearningService(conn, settings)
    assert service._adaptive_question_count(session.id, "learn algebra") == 2


def test_adaptive_check_count_fast_corrects_still_ace(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    from app.services import mastery
    from app.util import new_id, utc_now

    mastery.record_evidence(conn, session.id, "learn algebra", "check", "correct")
    mastery.record_evidence(conn, session.id, "learn algebra", "check", "correct")
    now = utc_now()
    for _ in range(2):
        conn.execute(
            "INSERT INTO quiz_questions (id, session_id, kind, topic, difficulty, question, options_json, correct_index, status, user_choice, outcome, confidence, latency_ms, created_at, answered_at) "
            "VALUES (?, ?, 'check', 'learn algebra', 2, 'Fast Q?', '[\"a\",\"b\"]', 0, 'answered', 0, 'correct', 'know', 2000, ?, ?)",
            (new_id(), session.id, now, now),
        )
    conn.commit()
    service = LearningService(conn, settings)
    assert service._adaptive_question_count(session.id, "learn algebra") == 1


def test_generate_check_adaptively_requests_multiple(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    _answer_outcome(conn, session.id, "check", "idk")
    fake_llm.complete_json_responses = [
        json.dumps(
            [
                {
                    "question": f"Check {i}?",
                    "options": ["x", "y"],
                    "correct_index": 0,
                    "explanation": "e",
                    "topic": "model-topic",
                    "difficulty": 3,
                }
                for i in range(3)
            ]
        )
    ]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_check(session.id, fake_llm))
    assert len(result["questions"]) == 3
    for q in result["questions"]:
        assert q["kind"] == "check"
        assert q["topic"] == "learn algebra"


def test_answer_quiz_stores_confidence(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [json.dumps(_good_questions())]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_probe(session.id, fake_llm))
    qid = result["questions"][0]["id"]
    service.answer_quiz(session.id, qid, 1, confidence="confident")
    row = conn.execute(
        "SELECT confidence FROM quiz_questions WHERE id = ?", (qid,)
    ).fetchone()
    assert row["confidence"] == "confident"


def test_answer_quiz_rejects_bad_confidence(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [json.dumps(_good_questions())]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_probe(session.id, fake_llm))
    qid = result["questions"][0]["id"]
    with pytest.raises(ValueError):
        service.answer_quiz(session.id, qid, 1, confidence="certain")


def test_answer_quiz_stores_latency_ms(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [json.dumps(_good_questions())]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_probe(session.id, fake_llm))
    qid = result["questions"][0]["id"]
    service.answer_quiz(session.id, qid, 1, latency_ms=4500)
    row = conn.execute(
        "SELECT latency_ms FROM quiz_questions WHERE id = ?", (qid,)
    ).fetchone()
    assert row["latency_ms"] == 4500
    evidence = json.loads(
        conn.execute(
            "SELECT evidence_json FROM mastery_topics WHERE topic = 'topic-0'"
        ).fetchone()[0]
    )
    assert evidence[0]["latency_ms"] == 4500
    assert evidence[0]["latency_bucket"] == "fast-correct"


def test_answer_quiz_latency_gap_fallback(conn, settings, fake_llm):
    session = _create_session(conn, settings)
    fake_llm.complete_json_responses = [json.dumps(_good_questions())]
    service = LearningService(conn, settings)
    result = asyncio.run(service.generate_probe(session.id, fake_llm))
    qid = result["questions"][0]["id"]
    # Simulate a long gap between question creation and answering: without a UI
    # latency badge the server falls back to the created_at→answered_at gap.
    conn.execute(
        "UPDATE quiz_questions SET created_at = '2026-01-01T10:00:00.000Z' WHERE id = ?",
        (qid,),
    )
    conn.commit()
    service.answer_quiz(session.id, qid, 1)
    row = conn.execute(
        "SELECT latency_ms FROM quiz_questions WHERE id = ?", (qid,)
    ).fetchone()
    assert row["latency_ms"] is not None
    assert row["latency_ms"] > 1000
