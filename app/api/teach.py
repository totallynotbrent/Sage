from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_app_settings, validation_problems
from app.db import get_conn
from app.errors import ConfigError
from app.llm.client import LLMClient, get_llm_client
from app.services.teach import TeachService

router = APIRouter()


def _require_configured(settings: Settings) -> None:
    problems = validation_problems(settings)
    if problems:
        raise ConfigError(problems)


def _handle_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/api/sessions/{session_id}/advance")
async def advance(
    session_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = TeachService(conn, settings)
    try:
        return service.advance(session_id)
    except ValueError as exc:
        raise _handle_value_error(exc)


@router.post("/api/sessions/{session_id}/continue")
async def continue_after_remediate(
    session_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = TeachService(conn, settings)
    try:
        return service.continue_after_remediate(session_id)
    except ValueError as exc:
        raise _handle_value_error(exc)


@router.post("/api/sessions/{session_id}/complete")
async def complete_session(
    session_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = TeachService(conn, settings)
    try:
        return service.complete(session_id)
    except ValueError as exc:
        raise _handle_value_error(exc)


@router.post("/api/sessions/{session_id}/quiz/{question_id}/hint")
async def quiz_hint(
    session_id: str,
    question_id: str,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    _require_configured(settings)
    service = TeachService(conn, settings)
    try:
        return await service.hint(session_id, question_id, llm)
    except ValueError as exc:
        raise _handle_value_error(exc)


@router.post("/api/sessions/{session_id}/quiz/{question_id}/reveal")
async def quiz_reveal(
    session_id: str,
    question_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = TeachService(conn, settings)
    try:
        return service.reveal(session_id, question_id)
    except ValueError as exc:
        raise _handle_value_error(exc)


@router.post("/api/sessions/{session_id}/quiz/{question_id}/skip")
async def quiz_skip(
    session_id: str,
    question_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = TeachService(conn, settings)
    try:
        return service.skip_quiz(session_id, question_id)
    except ValueError as exc:
        raise _handle_value_error(exc)
