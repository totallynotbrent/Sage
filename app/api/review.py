from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import handle_value_error
from app.config import Settings, get_app_settings
from app.db import get_conn, row_to_dict
from app.services import review as review_service
from app.services.sessions.rows import question_dict

router = APIRouter()


@router.post("/api/sessions/{session_id}/mastery")
async def mastery_panel(
    session_id: str,
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return review_service.mastery_panel(conn, session_id)


@router.post("/api/sessions/{session_id}/review/status")
async def review_status(
    session_id: str,
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return review_service.review_status(conn, session_id)


@router.post("/api/sessions/{session_id}/review/due")
async def review_due(
    session_id: str,
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    cards = review_service.due_cards(conn, session_id)
    out = []
    for card in cards:
        item: dict = dict(card)
        if card.get("question_id"):
            q = row_to_dict(
                conn.execute(
                    "SELECT * FROM quiz_questions WHERE id = ?",
                    (card["question_id"],),
                ).fetchone()
            )
            if q:
                item["question"] = question_dict(q)
        out.append(item)
    return {"cards": out, "due_count": len(out)}


@router.post("/api/sessions/{session_id}/review/{card_id}/grade")
async def grade_review(
    session_id: str,
    card_id: str,
    body: dict,
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    outcome = (body or {}).get("outcome")
    if outcome not in ("correct", "incorrect", "idk"):
        raise HTTPException(
            status_code=400,
            detail="outcome must be one of: correct, incorrect, idk",
        )
    updated = review_service.grade_card(conn, card_id, session_id, outcome)
    if updated is None:
        raise HTTPException(status_code=404, detail="review card not found")
    status = review_service.review_status(conn, session_id)
    return {"card": updated, "status": status}
