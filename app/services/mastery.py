from __future__ import annotations

import json
import sqlite3

from app.db import row_to_dict, rows_to_dicts
from app.util import utc_now

DEFAULT_TOPIC = "general"

STATEMENT_SOURCE = "user_statement"

STATEMENT_CONFIDENCE = {"weak": 0.25, "moderate": 0.6, "strong": 0.9}


def normalize_topic(topic: str | None) -> str:
    if topic is None:
        return DEFAULT_TOPIC
    normalized = str(topic).strip().lower()
    return normalized if normalized else DEFAULT_TOPIC


def _display_label(topic: str | None) -> str:
    if topic is None:
        return "General"
    label = str(topic).strip()
    if not label:
        return "General"
    return label[0].upper() + label[1:]


def _laplace_confidence(correct_count: int, observed_count: int) -> float:
    return (correct_count + 1) / (observed_count + 2)


def record_evidence(
    conn: sqlite3.Connection,
    topic: str | None,
    source: str,
    outcome: str,
    question_id: str | None = None,
    note: str | None = None,
) -> dict:
    normalized = normalize_topic(topic)
    label = _display_label(topic)
    now = utc_now()
    row = conn.execute(
        "SELECT * FROM mastery_topics WHERE topic = ?", (normalized,)
    ).fetchone()
    if row is None:
        observed = 0
        correct = 0
        idk = 0
        evidence: list[dict] = []
        conn.execute(
            "INSERT INTO mastery_topics (topic, label, observed_count, correct_count, idk_count, last_assessed_at, evidence_json) VALUES (?, ?, 0, 0, 0, ?, '[]')",
            (normalized, label, now),
        )
    else:
        observed = row["observed_count"]
        correct = row["correct_count"]
        idk = row["idk_count"]
        try:
            evidence = json.loads(row["evidence_json"] or "[]")
        except json.JSONDecodeError:
            evidence = []

    observed += 1
    if outcome == "correct":
        correct += 1
    elif outcome == "idk":
        idk += 1

    entry: dict = {"source": source, "topic": normalized, "outcome": outcome, "at": now}
    if question_id is not None:
        entry["question_id"] = question_id
    if note:
        entry["note"] = note
    evidence.append(entry)

    confidence = _laplace_confidence(correct, observed)
    if note is not None:
        conn.execute(
            "UPDATE mastery_topics SET observed_count = ?, correct_count = ?, idk_count = ?, confidence = ?, last_assessed_at = ?, evidence_json = ?, notes = ? WHERE topic = ?",
            (observed, correct, idk, confidence, now, json.dumps(evidence), note, normalized),
        )
    else:
        conn.execute(
            "UPDATE mastery_topics SET observed_count = ?, correct_count = ?, idk_count = ?, confidence = ?, last_assessed_at = ?, evidence_json = ? WHERE topic = ?",
            (observed, correct, idk, confidence, now, json.dumps(evidence), normalized),
        )
    conn.commit()
    return row_to_dict(
        conn.execute(
            "SELECT * FROM mastery_topics WHERE topic = ?", (normalized,)
        ).fetchone()
    ) or {}


def apply_statement(
    conn: sqlite3.Connection,
    topic: str | None,
    level: str,
    note: str | None = None,
) -> dict:
    if level not in STATEMENT_CONFIDENCE:
        raise ValueError(f"level must be one of {sorted(STATEMENT_CONFIDENCE)}")
    normalized = normalize_topic(topic)
    label = _display_label(topic)
    now = utc_now()
    observed = 1
    target = STATEMENT_CONFIDENCE[level]
    correct = max(0, round((observed + 2) * target - 1))
    entry: dict = {"source": STATEMENT_SOURCE, "topic": normalized, "outcome": level, "at": now}
    if note:
        entry["note"] = note
    conn.execute(
        "INSERT INTO mastery_topics (topic, label, confidence, observed_count, correct_count, idk_count, last_assessed_at, evidence_json, notes) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?) "
        "ON CONFLICT(topic) DO UPDATE SET label = excluded.label, confidence = excluded.confidence, observed_count = excluded.observed_count, correct_count = excluded.correct_count, idk_count = 0, last_assessed_at = excluded.last_assessed_at, evidence_json = excluded.evidence_json, notes = excluded.notes",
        (
            normalized,
            label,
            _laplace_confidence(correct, observed),
            observed,
            correct,
            now,
            json.dumps([entry]),
            note,
        ),
    )
    conn.commit()
    return row_to_dict(
        conn.execute(
            "SELECT * FROM mastery_topics WHERE topic = ?", (normalized,)
        ).fetchone()
    ) or {}


def summarize_mastery(conn: sqlite3.Connection) -> str:
    rows = rows_to_dicts(
        conn.execute(
            "SELECT topic, label, confidence FROM mastery_topics "
            "WHERE confidence > 0 ORDER BY label"
        )
    )
    if not rows:
        return "No prior mastery data."
    return "; ".join(
        f"{r['label']} ({r['confidence']:.0%})" for r in rows
    )


def reset_mastery(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM mastery_topics")
    conn.commit()
