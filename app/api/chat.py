from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from app.config import Settings, get_app_settings, validation_problems
from app.db import open_db
from app.errors import ConfigError
from app.llm.client import LLMClient, get_llm_client
from app.models import RetryBody, TurnBody
from app.services.sessions import SessionService
from app.sse import sse_response

router = APIRouter()


def _require_configured(settings: Settings) -> None:
    problems = validation_problems(settings)
    if problems:
        raise ConfigError(problems)


@router.post("/api/sessions/{session_id}/turns")
async def stream_turn(
    session_id: str,
    body: TurnBody,
    request: Request,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
):
    _require_configured(settings)
    conn = open_db(settings.db_path)
    service = SessionService(conn, settings)
    try:
        service.get(session_id)
    except Exception:
        conn.close()
        raise

    gen = service.turn(
        session_id,
        body.message,
        client_msg_id=body.client_msg_id,
        llm=llm,
        is_disconnected=lambda: request.is_disconnected(),
    )
    return sse_response(gen)


@router.post("/api/sessions/{session_id}/stop")
async def stop_generation(
    session_id: str,
    llm: LLMClient = Depends(get_llm_client),
) -> dict:
    llm.cancel_inflight(session_id)
    return {"ok": True}


@router.post("/api/sessions/{session_id}/retry")
async def retry_turn(
    session_id: str,
    body: RetryBody,
    request: Request,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
):
    _require_configured(settings)
    conn = open_db(settings.db_path)
    service = SessionService(conn, settings)
    try:
        service.get(session_id)
    except Exception:
        conn.close()
        raise

    gen = service.retry_last_turn(
        session_id,
        body.client_msg_id,
        llm=llm,
        is_disconnected=lambda: request.is_disconnected(),
    )
    return sse_response(gen)
