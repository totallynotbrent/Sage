from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.api.deps import handle_value_error, require_configured
from app.config import Settings, get_app_settings
from app.db import get_conn
from app.llm.client import LLMClient, get_llm_client
from app.models import (
    CheckBody,
    LearnerQuestionsBody,
    NotesQuizBody,
    ProbeBody,
    QuizAnswerBody,
)
from app.services.learning import LearningService

router = APIRouter()


@router.post("/api/sessions/{session_id}/probe")
async def generate_probe(
    session_id: str,
    body: ProbeBody,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    require_configured(settings)
    return await LearningService(conn, settings).generate_probe(session_id, llm)


@router.post("/api/sessions/{session_id}/check")
async def generate_check(
    session_id: str,
    body: CheckBody,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    require_configured(settings)
    return await LearningService(conn, settings).generate_check(session_id, llm)


@router.post("/api/sessions/{session_id}/notes-quiz")
async def generate_notes_quiz(
    session_id: str,
    body: NotesQuizBody,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    require_configured(settings)
    service = LearningService(conn, settings)
    try:
        return await service.generate_notes_quiz(
            session_id, llm, count=body.count, subject=body.subject
        )
    except ValueError as exc:
        raise handle_value_error(exc)


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
        return service.answer_quiz(
            session_id,
            question_id,
            body.choice_index,
            body.idk,
            body.confidence,
            body.latency_ms,
        )
    except ValueError as exc:
        raise handle_value_error(exc)


@router.post("/api/sessions/{session_id}/learner-questions")
async def review_learner_questions(
    session_id: str,
    body: LearnerQuestionsBody,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    require_configured(settings)
    return await LearningService(conn, settings).review_learner_questions(
        session_id, llm, body.questions
    )
