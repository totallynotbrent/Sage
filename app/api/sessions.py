from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import Settings, get_app_settings
from app.db import get_conn
from app.llm.client import LLMClient, get_llm_client
from app.models import GroundingMode, Session, SessionCreate
from app.services.sessions import SessionService

router = APIRouter()


class SelectFilesBody(BaseModel):
    file_ids: list[str] = Field(default_factory=list)


class PatchSessionBody(BaseModel):
    grounding_mode: GroundingMode


@router.post("/api/sessions", response_model=Session)
async def create_session(
    body: SessionCreate,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> Session:
    return SessionService(conn, settings).create(
        goal=body.goal, file_ids=body.file_ids, grounding_mode=body.grounding_mode
    )


@router.get("/api/sessions", response_model=list[Session])
async def list_sessions(
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> list[Session]:
    return SessionService(conn, settings).list()


@router.get("/api/sessions/{session_id}")
async def get_session(
    session_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    return SessionService(conn, settings).load_full(session_id)


@router.post("/api/sessions/{session_id}/files", response_model=Session)
async def select_session_files(
    session_id: str,
    body: SelectFilesBody,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> Session:
    return SessionService(conn, settings).select_files(session_id, body.file_ids)


@router.patch("/api/sessions/{session_id}", response_model=Session)
async def patch_session(
    session_id: str,
    body: PatchSessionBody,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> Session:
    return SessionService(conn, settings).update_grounding(
        session_id, body.grounding_mode
    )


@router.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    llm: LLMClient = Depends(get_llm_client),
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> JSONResponse:
    llm.cancel_inflight(session_id)
    SessionService(conn, settings).delete(session_id)
    return JSONResponse(status_code=204, content=None)
