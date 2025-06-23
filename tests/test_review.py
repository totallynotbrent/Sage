from __future__ import annotations

import sqlite3

from app.services import review as review_service
from app.services import mastery
from app.services.sessions import SessionService

SID = "session-test"


def _session(conn, settings, goal="learn calculus"):
    return SessionService(conn, settings).create(goal, [])


def test_register_card_creates_and_reschedules(conn, settings):
    session = _session(conn, settings)
    # First answer: fresh card (New state) is immediately due.
    c1 = review_service.register_card(conn, session.id, "derivatives", "q1", "check", "correct")
    assert c1 is not None
    assert c1["topic"] == "derivatives"
    assert c1["reps"] == 1
    assert c1["lapses"] == 0

    # Review the same question again (correct) -> reps increment, same card.
    c2 = review_service.register_card(conn, session.id, "derivatives", "q1", "check", "correct")
    assert c2["reps"] == 2
    assert c2["card_id"] == c1["card_id"]

    # A wrong answer bumps lapses.
    c3 = review_service.register_card(conn, session.id, "derivatives", "q1", "check", "idk")
    assert c3["lapses"] == 1
    assert c3["reps"] == 3


def test_fresh_card_is_due_then_scheduled_out(conn, settings):
    session = _session(conn, settings)
    # A brand-new (New-state) card is due immediately.
    card = review_service.register_card(conn, session.id, "derivatives", "q1", "check", "idk")
    due_now = review_service.due_cards(conn, session.id)
    assert any(c["card_id"] == card["card_id"] for c in due_now)

    # Answering it correctly (Good) pushes the next review into the future.
    review_service.register_card(conn, session.id, "derivatives", "q1", "check", "correct")
    due_after = review_service.due_cards(conn, session.id)
    assert not any(c["card_id"] == card["card_id"] for c in due_after)


def test_status_counts_and_topics(conn, settings):
    session = _session(conn, settings)
    review_service.register_card(conn, session.id, "derivatives", "q1", "check", "idk")
    review_service.register_card(conn, session.id, "integrals", "q2", "check", "idk")
    status = review_service.review_status(conn, session.id)
    assert status["total_cards"] == 2
    # Both were just answered incorrectly (Again) -> still due now.
    assert status["due_cards"] == 2
    topics = {t["topic"]: t for t in status["topics"]}
    assert "derivatives" in topics and "integrals" in topics


def test_grade_card_updates(conn, settings):
    session = _session(conn, settings)
    card = review_service.register_card(conn, session.id, "vectors", "q9", "check", "correct")
    before = review_service.get_card(conn, card["card_id"], session.id)
    updated = review_service.grade_card(conn, card["card_id"], session.id, "incorrect")
    assert updated is not None
    assert updated["lapses"] == before["lapses"] + 1
    assert updated["reps"] == before["reps"] + 1


def test_mastery_panel_combines_confidence(conn, settings):
    from app.services import mastery

    session = _session(conn, settings)
    # Give the topic some confidence via evidence.
    mastery.record_evidence(conn, session.id, "limits", "probe", "correct")
    # And a review card for it.
    review_service.register_card(conn, session.id, "limits", "q1", "check", "correct")
    panel = review_service.mastery_panel(conn, session.id)
    topics = {t["topic"]: t for t in panel["topics"]}
    assert "limits" in topics
    assert topics["limits"]["confidence"] > 0
    assert "fading" in topics["limits"]


def test_mastery_panel_is_scoped_to_session(conn, settings):
    # Issue #1: mastery must not bleed across sessions. Evidence recorded for
    # session A must not appear in session B's mastery panel.
    a = _session(conn, settings, goal="learn algebra")
    b = _session(conn, settings, goal="learn algebra")
    mastery.record_evidence(conn, a.id, "algebra", "probe", "correct")
    mastery.record_evidence(conn, b.id, "calculus", "probe", "correct")
    panel_a = review_service.mastery_panel(conn, a.id)
    panel_b = review_service.mastery_panel(conn, b.id)
    topics_a = {t["topic"] for t in panel_a["topics"]}
    topics_b = {t["topic"] for t in panel_b["topics"]}
    assert "algebra" in topics_a
    assert "algebra" not in topics_b
    assert "calculus" in topics_b


def test_review_table_migrates_idempotently(settings):
    # init_db must succeed with the new review_cards table present.
    from app.db import init_db

    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(review_cards)")}
    conn.close()
    assert "card_json" in cols
    assert "topic" in cols


def test_lowest_confidence_topics_excludes_current(conn, settings):
    mastery.record_evidence(conn, SID, "algebra", "probe", "correct")      # 2/3
    mastery.record_evidence(conn, SID, "calculus", "probe", "idk")         # 1/3 (weakest)
    mastery.record_evidence(conn, SID, "geometry", "probe", "incorrect")   # 1/3 (weakest too)
    weak = mastery.lowest_confidence_topics(conn, SID, exclude={"algebra"}, limit=3)
    topics = [w["topic"] for w in weak]
    assert "algebra" not in topics
    assert topics[0] in ("calculus", "geometry")


def test_fresh_slow_correct_seeds_lower_stability(conn, settings):
    session = _session(conn, settings)
    fast = review_service.register_card(
        conn, session.id, "derivatives", "q1", "check", "correct", latency_ms=1000
    )
    slow = review_service.register_card(
        conn, session.id, "derivatives", "q2", "check", "correct", latency_ms=15_000
    )
    # Initial stability is seeded lower for the effortful recall.
    assert slow["stability"] < fast["stability"]
    # And the next stability-derived review comes back sooner: answering both
    # cards correctly again schedules the slow card's interval from the scaled
    # stability, so it lands at or before the fast card.
    review_service.register_card(
        conn, session.id, "derivatives", "q1", "check", "correct"
    )
    slow2 = review_service.register_card(
        conn, session.id, "derivatives", "q2", "check", "correct"
    )
    fast2 = review_service.get_card(conn, fast["card_id"], session.id)
    assert slow2["due"] <= fast2["due"]


def test_grade_card_accepts_latency(conn, settings):
    session = _session(conn, settings)
    card = review_service.register_card(
        conn, session.id, "vectors", "q9", "check", "correct"
    )
    updated = review_service.grade_card(
        conn, card["card_id"], session.id, "incorrect", latency_ms=12_000
    )
    assert updated is not None
    assert updated["lapses"] == 1
    assert updated["reps"] == 2


def test_latency_column_migrates_and_backfills(settings):
    from app.db import init_db

    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute(
        "INSERT INTO quiz_questions (id, session_id, kind, question, options_json, "
        "correct_index, status, created_at, answered_at) "
        "VALUES ('q-legacy', 's1', 'probe', 'Old Q?', '[\"a\"]', 0, 'answered', "
        "'2026-01-01T10:00:00.000Z', '2026-01-01T10:00:05.500Z')"
    )
    conn.commit()
    conn.close()
    # Second pass runs the migration/backfill over existing rows.
    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(quiz_questions)")}
    assert "latency_ms" in cols
    version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert version == 5
    row = conn.execute(
        "SELECT latency_ms FROM quiz_questions WHERE id = 'q-legacy'"
    ).fetchone()
    assert row["latency_ms"] == 5500
    conn.close()
