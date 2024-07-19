from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.config import Settings, get_app_settings
from app.db import get_conn
from app.models import Preferences, PreferencesBody
from app.services import mastery
from app.util import utc_now

router = APIRouter()

_DEFAULTS = {"depth": "standard", "pacing": "normal", "style": "analogy-first"}


def _row_to_preferences(row: dict | None) -> Preferences:
    if row is None:
        return Preferences(**_DEFAULTS, notes=None, updated_at="")
    return Preferences(**dict(row))


@router.get("/api/preferences", response_model=Preferences)
async def get_preferences(
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> Preferences:
    row = conn.execute(
        "SELECT depth, pacing, style, notes, updated_at FROM preferences WHERE id = 1"
    ).fetchone()
    return _row_to_preferences(dict(row) if row else None)


@router.patch("/api/preferences", response_model=Preferences)
async def update_preferences(
    body: PreferencesBody,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> Preferences:
    now = utc_now()
    conn.execute(
        "UPDATE preferences SET depth = COALESCE(?, depth), pacing = COALESCE(?, pacing), "
        "style = COALESCE(?, style), notes = COALESCE(?, notes), updated_at = ? WHERE id = 1",
        (body.depth, body.pacing, body.style, body.notes, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT depth, pacing, style, notes, updated_at FROM preferences WHERE id = 1"
    ).fetchone()
    return _row_to_preferences(dict(row) if row else None)


@router.delete("/api/preferences", status_code=204)
async def reset_preferences(
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> JSONResponse:
    now = utc_now()
    conn.execute(
        "UPDATE preferences SET depth = 'standard', pacing = 'normal', "
        "style = 'analogy-first', notes = NULL, updated_at = ? WHERE id = 1",
        (now,),
    )
    conn.commit()
    return JSONResponse(status_code=204, content=None)


@router.delete("/api/mastery", status_code=204)
async def reset_mastery(
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> JSONResponse:
    mastery.reset_mastery(conn)
    return JSONResponse(status_code=204, content=None)
