from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_app_settings, validation_problems
from app.db import get_conn
from app.errors import ConfigError
from app.llm.client import LLMClient, get_llm_client
from app.models import CheckBody, ProbeBody, QuizAnswerBody
from app.services.learning import LearningService

router = APIRouter()


def _require_configured(settings: Settings) -> None:
    problems = validation_problems(settings)
    if problems:
        raise ConfigError(problems)


@router.post("/api/sessions/{session_id}/probe")
async def generate_probe(
    session_id: str,
    body: ProbeBody,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    _require_configured(settings)
    return await LearningService(conn, settings).generate_probe(session_id, llm)


@router.post("/api/sessions/{session_id}/check")
async def generate_check(
    session_id: str,
    body: CheckBody,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    _require_configured(settings)
    return await LearningService(conn, settings).generate_check(session_id, llm)


@router.post("/api/sessions/{session_id}/quiz/{question_id}/answer")
async def answer_quiz(
    session_id: str,
    question_id: str,
    body: QuizAnswerBody,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = LearningService(conn, settings)
    try:
        return service.answer_quiz(session_id, question_id, body.choice_index, body.idk)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
