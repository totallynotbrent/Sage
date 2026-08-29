from __future__ import annotations

import sqlite3

from app.services import review as review_service
from app.services import mastery
from app.services.sessions import SessionService


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
    mastery.record_evidence(conn, "limits", "probe", "correct")
    # And a review card for it.
    review_service.register_card(conn, session.id, "limits", "q1", "check", "correct")
    panel = review_service.mastery_panel(conn, session.id)
    topics = {t["topic"]: t for t in panel["topics"]}
    assert "limits" in topics
    assert topics["limits"]["confidence"] > 0
    assert "fading" in topics["limits"]


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
    mastery.record_evidence(conn, "algebra", "probe", "correct")      # 2/3
    mastery.record_evidence(conn, "calculus", "probe", "idk")         # 1/3 (weakest)
    mastery.record_evidence(conn, "geometry", "probe", "incorrect")   # 1/3 (weakest too)
    weak = mastery.lowest_confidence_topics(conn, exclude={"algebra"}, limit=3)
    topics = [w["topic"] for w in weak]
    assert "algebra" not in topics
    assert topics[0] in ("calculus", "geometry")
