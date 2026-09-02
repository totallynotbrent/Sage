from __future__ import annotations

import json

import pytest

from app.services import mastery

SID = "session-test"


def _evidence(conn, topic: str) -> list[dict]:
    row = conn.execute(
        "SELECT evidence_json FROM mastery_topics WHERE topic = ?", (topic,)
    ).fetchone()
    return json.loads(row["evidence_json"]) if row else []


def test_laplace_confidence_math(conn):
    row = mastery.record_evidence(conn, SID, "algebra", "probe", "correct")
    assert row["observed_count"] == 1
    assert row["correct_count"] == 1
    assert row["idk_count"] == 0
    assert row["confidence"] == pytest.approx(2 / 3)

    row = mastery.record_evidence(conn, SID, "algebra", "probe", "incorrect")
    assert row["observed_count"] == 2
    assert row["correct_count"] == 1
    assert row["confidence"] == pytest.approx(2 / 4)

    row = mastery.record_evidence(conn, SID, "algebra", "probe", "correct")
    assert row["observed_count"] == 3
    assert row["correct_count"] == 2
    assert row["confidence"] == pytest.approx(3 / 5)


def test_record_evidence_appends_evidence(conn):
    mastery.record_evidence(conn, SID, "Photosynthesis", "probe", "correct", question_id="q1")
    evidence = _evidence(conn, "photosynthesis")
    assert len(evidence) == 1
    assert evidence[0]["source"] == "probe"
    assert evidence[0]["topic"] == "photosynthesis"
    assert evidence[0]["outcome"] == "correct"
    assert evidence[0]["question_id"] == "q1"
    assert "at" in evidence[0]

    mastery.record_evidence(conn, SID, "Photosynthesis", "check", "incorrect", question_id="q2")
    evidence = _evidence(conn, "photosynthesis")
    assert len(evidence) == 2
    assert evidence[1]["source"] == "check"
    assert evidence[1]["question_id"] == "q2"


def test_record_evidence_with_note(conn):
    mastery.record_evidence(conn, SID, "algebra", "session_outcome", "correct", note="session finished")
    evidence = _evidence(conn, "algebra")
    assert evidence[0]["note"] == "session finished"


def test_topic_normalization(conn):
    assert mastery.normalize_topic("  Group Theory ") == "group theory"
    assert mastery.normalize_topic("") == "general"
    assert mastery.normalize_topic(None) == "general"

    mastery.record_evidence(conn, SID, None, "probe", "correct")
    row = conn.execute(
        "SELECT * FROM mastery_topics WHERE topic = 'general'"
    ).fetchone()
    assert row is not None
    assert row["label"] == "General"


def test_idk_counting(conn):
    row = mastery.record_evidence(conn, SID, "calculus", "probe", "idk")
    assert row["idk_count"] == 1
    assert row["correct_count"] == 0
    assert row["observed_count"] == 1
    assert row["confidence"] == pytest.approx(1 / 3)


def test_apply_statement_levels(conn):
    expected = {"weak": (0, 1 / 3), "moderate": (1, 2 / 3), "strong": (2, 1.0)}
    for level, (correct, confidence) in expected.items():
        row = mastery.apply_statement(conn, SID, f"topic-{level}", level)
        assert row["observed_count"] == 1
        assert row["correct_count"] == correct
        assert row["idk_count"] == 0
        assert row["confidence"] == pytest.approx(confidence, abs=1e-9)


def test_apply_statement_evidence(conn):
    mastery.apply_statement(conn, SID, "algebra", "strong", note="learner is confident")
    evidence = _evidence(conn, "algebra")
    assert len(evidence) == 1
    assert evidence[0]["source"] == "user_statement"
    assert evidence[0]["outcome"] == "strong"
    assert evidence[0]["note"] == "learner is confident"


def test_apply_statement_invalid_level(conn):
    with pytest.raises(ValueError):
        mastery.apply_statement(conn, SID, "algebra", "expert")


def test_summarize_mastery_empty(conn):
    assert mastery.summarize_mastery(conn, SID) == "No prior mastery data."


def test_summarize_mastery_format(conn):
    mastery.record_evidence(conn, SID, "algebra", "probe", "correct")
    mastery.record_evidence(conn, SID, "calculus", "probe", "correct")
    mastery.record_evidence(conn, SID, "calculus", "probe", "correct")
    text = mastery.summarize_mastery(conn, SID)
    assert text == "Algebra (67%); Calculus (75%)"


def test_reset_mastery(conn):
    mastery.record_evidence(conn, SID, "algebra", "probe", "correct")
    mastery.record_evidence(conn, SID, "calculus", "probe", "idk")
    mastery.reset_mastery(conn)
    assert conn.execute("SELECT COUNT(*) FROM mastery_topics").fetchone()[0] == 0


def test_overconfident_wrong_calibrates_extra_miss(conn):
    row = mastery.record_evidence(conn, SID, "algebra", "check", "incorrect", confidence="know")
    # 1 observed outcome + 1 overconfidence penalty = 2 observed, 0 correct
    assert row["observed_count"] == 2
    assert row["correct_count"] == 0
    assert row["overconfident_count"] == 1
    assert row["confidence"] == pytest.approx(1 / 4)


def test_confident_incorrect_also_calibrates(conn):
    row = mastery.record_evidence(conn, SID, "physics", "check", "idk", confidence="confident")
    assert row["overconfident_count"] == 1
    assert row["observed_count"] == 2


def test_guess_correct_counts_as_underconfident(conn):
    row = mastery.record_evidence(conn, SID, "geometry", "check", "correct", confidence="guess")
    assert row["underconfident_count"] == 1
    assert row["overconfident_count"] == 0
    # A correct guess is not penalized.
    assert row["observed_count"] == 1


def test_no_penalty_for_accurate_know(conn):
    row = mastery.record_evidence(conn, SID, "biology", "check", "correct", confidence="know")
    assert row["overconfident_count"] == 0
    assert row["observed_count"] == 1


def test_evidence_records_confidence(conn):
    mastery.record_evidence(conn, SID, "history", "check", "incorrect", confidence="confident", question_id="q9")
    evidence = _evidence(conn, "history")
    assert evidence[0]["confidence"] == "confident"
    assert evidence[0]["question_id"] == "q9"


def test_topic_confidence_returns_value_or_none(conn):
    assert mastery.topic_confidence(conn, SID, "algebra") is None
    mastery.record_evidence(conn, SID, "algebra", "probe", "correct", confidence="guess")
    assert mastery.topic_confidence(conn, SID, "algebra") == pytest.approx(2 / 3)


def test_latency_bucket_four_ways():
    assert mastery.latency_bucket("correct", 2000) == "fast-correct"
    assert mastery.latency_bucket("correct", 12_000) == "slow-correct"
    assert mastery.latency_bucket("incorrect", 2000) == "fast-wrong"
    assert mastery.latency_bucket("idk", 12_000) == "slow-wrong"
    assert mastery.latency_bucket("correct", None) is None


def test_is_slow_answer_threshold():
    assert not mastery.is_slow_answer(8000)
    assert mastery.is_slow_answer(8001)
    assert not mastery.is_slow_answer(None)


def test_coerce_latency_ms():
    assert mastery.coerce_latency_ms(None) is None
    assert mastery.coerce_latency_ms(42) == 42
    assert mastery.coerce_latency_ms("2500") == 2500
    assert mastery.coerce_latency_ms(2.6) == 3
    assert mastery.coerce_latency_ms("2.4s") is None
    assert mastery.coerce_latency_ms(-5) is None
    assert mastery.coerce_latency_ms(9_999_999_999) is None


def test_fast_correct_is_strong(conn):
    row = mastery.record_evidence(conn, SID, "algebra", "check", "correct", latency_ms=2000)
    assert row["observed_count"] == 1
    assert row["confidence"] == pytest.approx(2 / 3)


def test_slow_correct_weakens_confidence(conn):
    row = mastery.record_evidence(conn, SID, "algebra", "check", "correct", latency_ms=12_000)
    # The effortful recall counts as an extra implicit observed event.
    assert row["observed_count"] == 2
    assert row["correct_count"] == 1
    assert row["confidence"] == pytest.approx(2 / 4)


def test_fast_wrong_counts_as_plain_guess(conn):
    row = mastery.record_evidence(conn, SID, "algebra", "check", "incorrect", latency_ms=2000)
    assert row["observed_count"] == 1
    assert row["correct_count"] == 0
    assert row["confidence"] == pytest.approx(1 / 3)


def test_slow_wrong_schedules_hardest(conn):
    row = mastery.record_evidence(conn, SID, "algebra", "check", "incorrect", latency_ms=12_000)
    assert row["observed_count"] == 2
    assert row["correct_count"] == 0
    assert row["confidence"] == pytest.approx(1 / 4)


def test_latency_buckets_monotonic_ladder(conn):
    fast_correct = mastery.record_evidence(conn, SID, "t1", "check", "correct", latency_ms=2000)
    slow_correct = mastery.record_evidence(conn, SID, "t2", "check", "correct", latency_ms=12_000)
    fast_wrong = mastery.record_evidence(conn, SID, "t3", "check", "incorrect", latency_ms=2000)
    slow_wrong = mastery.record_evidence(conn, SID, "t4", "check", "incorrect", latency_ms=12_000)
    confs = [
        fast_correct["confidence"],
        slow_correct["confidence"],
        fast_wrong["confidence"],
        slow_wrong["confidence"],
    ]
    assert confs == sorted(confs, reverse=True)
    assert len(set(confs)) == 4


def test_evidence_records_latency(conn):
    mastery.record_evidence(
        conn, SID, "algebra", "check", "correct", latency_ms=3200, question_id="q42"
    )
    evidence = _evidence(conn, "algebra")
    assert evidence[0]["latency_ms"] == 3200
    assert evidence[0]["latency_bucket"] == "fast-correct"

    mastery.record_evidence(
        conn, SID, "algebra", "check", "incorrect", latency_ms=15_000, question_id="q43"
    )
    evidence = _evidence(conn, "algebra")
    assert evidence[1]["latency_bucket"] == "slow-wrong"
