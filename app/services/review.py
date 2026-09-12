from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler

from app.db import rows_to_dicts
from app.services import mastery
from app.util import new_id, utc_now

# Map Sage quiz outcomes to FSRS ratings.
OUTCOME_TO_RATING = {
    "correct": Rating.Good,   # recalled it, schedule out
    "incorrect": Rating.Again,  # wrong, review again soon
    "idk": Rating.Again,       # didn't know, review again soon
}

# A slow-but-correct first recall is weaker than a fast one: scale the seeded
# initial stability down so the card returns sooner (fading retrieval effort).
SLOW_CORRECT_STABILITY_SCALE = 0.6
_MIN_SEED_STABILITY_DAYS = 1.0

_SCHEDULER = Scheduler()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    # Accept ISO strings (with or without trailing Z)
    text = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _to_card(row: dict) -> Card:
    """Rebuild an fsrs.Card from a stored review_cards row.

    card_json stores full ISO strings; fsrs.Card.from_dict parses them itself.
    """
    data = json.loads(row["card_json"])
    return Card.from_dict(data)


def _row_to_api(row: dict) -> dict:
    due = _parse(row["due"])
    retrievability = None
    if row["last_review"] is not None and due is not None:
        try:
            retrievability = round(_SCHEDULER.get_card_retrievability(_to_card(row)), 3)
        except Exception:
            retrievability = None
    return {
        "card_id": row["id"],
        "session_id": row["session_id"],
        "topic": row["topic"],
        "question_id": row["question_id"],
        "kind": row["kind"],
        "state": row["state"],
        "stability": row["stability"],
        "difficulty": row["difficulty"],
        "due": row["due"],
        "last_review": row["last_review"],
        "reps": row["reps"],
        "lapses": row["lapses"],
        "retrievability": retrievability,
    }


def register_card(
    conn: sqlite3.Connection,
    session_id: str,
    topic: str,
    question_id: str | None,
    kind: str,
    outcome: str,
    latency_ms: int | None = None,
) -> dict | None:
    """Create or update an FSRS review card for a graded question.

    The first time a question is answered we create a fresh card; subsequent
    answers review (reschedule) the same card. Returns the stored row dict.
    """
    normalized = (topic or "general").strip().lower()
    existing = conn.execute(
        "SELECT * FROM review_cards WHERE session_id = ? AND question_id = ?",
        (session_id, question_id),
    ).fetchone()

    now_dt = _now_dt()
    now_iso = utc_now()
    rating = OUTCOME_TO_RATING.get(outcome, Rating.Again)

    if existing:
        card = _to_card(dict(existing))
        card, _log = _SCHEDULER.review_card(
            card, rating=rating, review_datetime=now_dt
        )
        due_iso = card.due.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        last_review_iso = (
            card.last_review.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if card.last_review
            else now_iso
        )
        reps = existing["reps"] + 1
        lapses = existing["lapses"] + (1 if outcome in ("incorrect", "idk") else 0)
        conn.execute(
            "UPDATE review_cards SET state = ?, stability = ?, difficulty = ?, due = ?, "
            "last_review = ?, reps = ?, lapses = ?, card_json = ?, updated_at = ? "
            "WHERE id = ?",
            (
                int(card.state),
                card.stability,
                card.difficulty,
                due_iso,
                last_review_iso,
                reps,
                lapses,
                json.dumps(card.to_dict()),
                now_iso,
                existing["id"],
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM review_cards WHERE id = ?", (existing["id"],)
        ).fetchone()
        return _row_to_api(dict(row))

    # Fresh card.
    card = Card()
    card, _log = _SCHEDULER.review_card(card, rating=rating, review_datetime=now_dt)
    if outcome == "correct" and mastery.is_slow_answer(latency_ms):
        card.stability = max(
            card.stability * SLOW_CORRECT_STABILITY_SCALE, _MIN_SEED_STABILITY_DAYS
        )
    due_iso = card.due.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    card_id = new_id()
    lapses = 1 if outcome in ("incorrect", "idk") else 0
    conn.execute(
        "INSERT INTO review_cards "
        "(id, session_id, topic, question_id, kind, state, stability, difficulty, due, "
        "last_review, reps, lapses, card_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            card_id,
            session_id,
            normalized,
            question_id,
            kind,
            int(card.state),
            card.stability,
            card.difficulty,
            due_iso,
            now_iso,
            1,
            lapses,
            json.dumps(card.to_dict()),
            now_iso,
            now_iso,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM review_cards WHERE id = ?", (card_id,)
    ).fetchone()
    return _row_to_api(dict(row)) if row else None


def due_cards(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    # Cards due now or within a small grace window count as due (covers FSRS
    # scheduling "Again" reviews ~1 min out and minor clock skew).
    cutoff = (_now_dt() + timedelta(minutes=2)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    rows = rows_to_dicts(
        conn.execute(
            "SELECT * FROM review_cards WHERE session_id = ? AND due <= ? "
            "ORDER BY due ASC, reps ASC",
            (session_id, cutoff),
        )
    )
    return [_row_to_api(r) for r in rows]


def all_cards(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = rows_to_dicts(
        conn.execute(
            "SELECT * FROM review_cards WHERE session_id = ? ORDER BY due ASC",
            (session_id,),
        )
    )
    return [_row_to_api(r) for r in rows]


def get_card(conn: sqlite3.Connection, card_id: str, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM review_cards WHERE id = ? AND session_id = ?",
        (card_id, session_id),
    ).fetchone()
    return _row_to_api(dict(row)) if row else None


def grade_card(
    conn: sqlite3.Connection,
    card_id: str,
    session_id: str,
    outcome: str,
    latency_ms: int | None = None,
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM review_cards WHERE id = ? AND session_id = ?",
        (card_id, session_id),
    ).fetchone()
    if row is None:
        return None
    return register_card(
        conn,
        session_id=session_id,
        topic=row["topic"],
        question_id=row["question_id"],
        kind=row["kind"],
        outcome=outcome,
        latency_ms=latency_ms,
    )


def review_status(conn: sqlite3.Connection, session_id: str) -> dict:
    now_iso = utc_now()
    cutoff = _due_cutoff_iso()
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM review_cards WHERE session_id = ?",
        (session_id,),
    ).fetchone()["n"]
    due = conn.execute(
        "SELECT COUNT(*) AS n FROM review_cards WHERE session_id = ? AND due <= ?",
        (session_id, cutoff),
    ).fetchone()["n"]
    # Topic-level rollup for the panel.
    rows = rows_to_dicts(
        conn.execute(
            "SELECT topic, COUNT(*) AS n, MIN(due) AS next_due "
            "FROM review_cards WHERE session_id = ? GROUP BY topic ORDER BY topic",
            (session_id,),
        )
    )
    topics = [
        {
            "topic": r["topic"],
            "cards": r["n"],
            "next_due": r["next_due"],
            "due": bool(r["next_due"] and r["next_due"] <= cutoff),
        }
        for r in rows
    ]
    return {
        "total_cards": total,
        "due_cards": due,
        "topics": topics,
    }


# Cards due within this many minutes of now count as "due" (covers FSRS
# scheduling "Again" reviews ~1 min out and minor clock skew).
DUE_GRACE_MINUTES = 2


def _due_cutoff_iso() -> str:
    return (_now_dt() + timedelta(minutes=DUE_GRACE_MINUTES)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


# Below this retrievability a topic is considered "fading" and worth a review.
FADE_THRESHOLD = 0.6

# If a topic hasn't been assessed in this many days and has no active cards,
# treat it as fading too (memory without reinforcement decays).
STALE_DAYS = 14


def mastery_panel(conn: sqlite3.Connection, session_id: str) -> dict:
    """Per-topic confidence + estimated retention for the right-rail panel."""
    now = _now_dt()
    cutoff = _due_cutoff_iso()

    # Confidence per topic.
    mt_rows = rows_to_dicts(
        conn.execute(
            "SELECT topic, label, confidence, last_assessed_at FROM mastery_topics "
            "WHERE session_id = ? AND confidence > 0 ORDER BY label",
            (session_id,),
        )
    )
    # FSRS retrievability per topic, and whether any card is due.
    review_rows = rows_to_dicts(
        conn.execute(
            "SELECT topic, due, card_json FROM review_cards WHERE session_id = ?",
            (session_id,),
        )
    )
    topic_review: dict[str, dict] = {}
    for r in review_rows:
        t = r["topic"]
        bucket = topic_review.setdefault(
            t, {"due": False, "retrievability": None}
        )
        if r["due"] and r["due"] <= cutoff:
            bucket["due"] = True
        try:
            retr = _SCHEDULER.get_card_retrievability(_to_card(r))
            bucket["retrievability"] = (
                retr
                if bucket["retrievability"] is None
                else max(bucket["retrievability"], retr)
            )
        except Exception:
            pass

    topics = []
    for r in mt_rows:
        topic = r["topic"]
        label = r["label"]
        confidence = float(r["confidence"] or 0)
        retr = topic_review.get(topic, {}).get("retrievability")
        is_due = topic_review.get(topic, {}).get("due", False)
        # Fading heuristic: low live retrievability, or stale assessment with no
        # active scheduling keeping it fresh.
        stale = False
        last = _parse(r["last_assessed_at"])
        if last is not None:
            stale = (now - last).days > STALE_DAYS
        fading = is_due or (retr is not None and retr < FADE_THRESHOLD) or (
            retr is None and stale
        )
        topics.append(
            {
                "topic": topic,
                "label": label,
                "confidence": round(confidence, 3),
                "retrievability": round(retr, 3) if retr is not None else None,
                "fading": bool(fading),
                "due": bool(is_due),
            }
        )
    return {"topics": topics}
