from __future__ import annotations

import json

import pytest

from app.services import mastery


def _evidence(conn, topic: str) -> list[dict]:
    row = conn.execute(
        "SELECT evidence_json FROM mastery_topics WHERE topic = ?", (topic,)
    ).fetchone()
    return json.loads(row["evidence_json"]) if row else []


def test_laplace_confidence_math(conn):
    row = mastery.record_evidence(conn, "algebra", "probe", "correct")
    assert row["observed_count"] == 1
    assert row["correct_count"] == 1
    assert row["idk_count"] == 0
    assert row["confidence"] == pytest.approx(2 / 3)

    row = mastery.record_evidence(conn, "algebra", "probe", "incorrect")
    assert row["observed_count"] == 2
    assert row["correct_count"] == 1
    assert row["confidence"] == pytest.approx(2 / 4)

    row = mastery.record_evidence(conn, "algebra", "probe", "correct")
    assert row["observed_count"] == 3
    assert row["correct_count"] == 2
    assert row["confidence"] == pytest.approx(3 / 5)


def test_record_evidence_appends_evidence(conn):
    mastery.record_evidence(conn, "Photosynthesis", "probe", "correct", question_id="q1")
    evidence = _evidence(conn, "photosynthesis")
    assert len(evidence) == 1
    assert evidence[0]["source"] == "probe"
    assert evidence[0]["topic"] == "photosynthesis"
    assert evidence[0]["outcome"] == "correct"
    assert evidence[0]["question_id"] == "q1"
    assert "at" in evidence[0]

    mastery.record_evidence(conn, "Photosynthesis", "check", "incorrect", question_id="q2")
    evidence = _evidence(conn, "photosynthesis")
    assert len(evidence) == 2
    assert evidence[1]["source"] == "check"
    assert evidence[1]["question_id"] == "q2"


def test_record_evidence_with_note(conn):
    mastery.record_evidence(conn, "algebra", "session_outcome", "correct", note="session finished")
    evidence = _evidence(conn, "algebra")
    assert evidence[0]["note"] == "session finished"


def test_topic_normalization(conn):
    assert mastery.normalize_topic("  Group Theory ") == "group theory"
    assert mastery.normalize_topic("") == "general"
    assert mastery.normalize_topic(None) == "general"

    mastery.record_evidence(conn, None, "probe", "correct")
    row = conn.execute(
        "SELECT * FROM mastery_topics WHERE topic = 'general'"
    ).fetchone()
    assert row is not None
    assert row["label"] == "General"


def test_idk_counting(conn):
    row = mastery.record_evidence(conn, "calculus", "probe", "idk")
    assert row["idk_count"] == 1
    assert row["correct_count"] == 0
    assert row["observed_count"] == 1
    assert row["confidence"] == pytest.approx(1 / 3)


def test_apply_statement_levels(conn):
    expected = {"weak": (0, 1 / 3), "moderate": (1, 2 / 3), "strong": (2, 1.0)}
    for level, (correct, confidence) in expected.items():
        row = mastery.apply_statement(conn, f"topic-{level}", level)
        assert row["observed_count"] == 1
        assert row["correct_count"] == correct
        assert row["idk_count"] == 0
        assert row["confidence"] == pytest.approx(confidence, abs=1e-9)


def test_apply_statement_evidence(conn):
    mastery.apply_statement(conn, "algebra", "strong", note="learner is confident")
    evidence = _evidence(conn, "algebra")
    assert len(evidence) == 1
    assert evidence[0]["source"] == "user_statement"
    assert evidence[0]["outcome"] == "strong"
    assert evidence[0]["note"] == "learner is confident"


def test_apply_statement_invalid_level(conn):
    with pytest.raises(ValueError):
        mastery.apply_statement(conn, "algebra", "expert")


def test_summarize_mastery_empty(conn):
    assert mastery.summarize_mastery(conn) == "No prior mastery data."


def test_summarize_mastery_format(conn):
    mastery.record_evidence(conn, "algebra", "probe", "correct")
    mastery.record_evidence(conn, "calculus", "probe", "correct")
    mastery.record_evidence(conn, "calculus", "probe", "correct")
    text = mastery.summarize_mastery(conn)
    assert text == "Algebra (67%); Calculus (75%)"


def test_reset_mastery(conn):
    mastery.record_evidence(conn, "algebra", "probe", "correct")
    mastery.record_evidence(conn, "calculus", "probe", "idk")
    mastery.reset_mastery(conn)
    assert conn.execute("SELECT COUNT(*) FROM mastery_topics").fetchone()[0] == 0


def test_overconfident_wrong_calibrates_extra_miss(conn):
    row = mastery.record_evidence(conn, "algebra", "check", "incorrect", confidence="know")
    # 1 observed outcome + 1 overconfidence penalty = 2 observed, 0 correct
    assert row["observed_count"] == 2
    assert row["correct_count"] == 0
    assert row["overconfident_count"] == 1
    assert row["confidence"] == pytest.approx(1 / 4)


def test_confident_incorrect_also_calibrates(conn):
    row = mastery.record_evidence(conn, "physics", "check", "idk", confidence="confident")
    assert row["overconfident_count"] == 1
    assert row["observed_count"] == 2


def test_guess_correct_counts_as_underconfident(conn):
    row = mastery.record_evidence(conn, "geometry", "check", "correct", confidence="guess")
    assert row["underconfident_count"] == 1
    assert row["overconfident_count"] == 0
    # A correct guess is not penalized.
    assert row["observed_count"] == 1


def test_no_penalty_for_accurate_know(conn):
    row = mastery.record_evidence(conn, "biology", "check", "correct", confidence="know")
    assert row["overconfident_count"] == 0
    assert row["observed_count"] == 1


def test_evidence_records_confidence(conn):
    mastery.record_evidence(conn, "history", "check", "incorrect", confidence="confident", question_id="q9")
    evidence = _evidence(conn, "history")
    assert evidence[0]["confidence"] == "confident"
    assert evidence[0]["question_id"] == "q9"


def test_topic_confidence_returns_value_or_none(conn):
    assert mastery.topic_confidence(conn, "algebra") is None
    mastery.record_evidence(conn, "algebra", "probe", "correct", confidence="guess")
    assert mastery.topic_confidence(conn, "algebra") == pytest.approx(2 / 3)
