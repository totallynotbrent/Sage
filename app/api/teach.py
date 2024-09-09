from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.api.deps import handle_value_error, require_configured
from app.config import Settings, get_app_settings
from app.db import get_conn
from app.llm.client import LLMClient, get_llm_client
from app.services.teach import TeachService

router = APIRouter()


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
        raise handle_value_error(exc)


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
        raise handle_value_error(exc)


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
        raise handle_value_error(exc)


@router.post("/api/sessions/{session_id}/quiz/{question_id}/hint")
async def quiz_hint(
    session_id: str,
    question_id: str,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    require_configured(settings)
    service = TeachService(conn, settings)
    try:
        return await service.hint(session_id, question_id, llm)
    except ValueError as exc:
        raise handle_value_error(exc)


@router.post("/api/sessions/{session_id}/quiz/{question_id}/worked-example")
async def quiz_worked_example(
    session_id: str,
    question_id: str,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    require_configured(settings)
    service = TeachService(conn, settings)
    try:
        return await service.worked_example(session_id, question_id, llm)
    except ValueError as exc:
        raise handle_value_error(exc)


@router.post("/api/sessions/{session_id}/quiz/{question_id}/walkthrough")
async def quiz_walkthrough(
    session_id: str,
    question_id: str,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    require_configured(settings)
    service = TeachService(conn, settings)
    try:
        return await service.walkthrough(session_id, question_id, llm)
    except ValueError as exc:
        raise handle_value_error(exc)


@router.post("/api/sessions/{session_id}/quiz/{question_id}/ladder")
async def quiz_ladder(
    session_id: str,
    question_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = TeachService(conn, settings)
    try:
        return service.ladder(session_id, question_id)
    except ValueError as exc:
        raise handle_value_error(exc)


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
        raise handle_value_error(exc)


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
        raise handle_value_error(exc)
