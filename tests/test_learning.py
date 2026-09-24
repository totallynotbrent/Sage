from __future__ import annotations

import asyncio
import json

import pytest

from app.services.learning import LearningService
from app.services.plans import PlansService
from app.services.sessions import SessionService
from app.services.teach import TeachService


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






