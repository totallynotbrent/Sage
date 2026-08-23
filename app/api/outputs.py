from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.api.deps import require_configured
from app.config import Settings, get_app_settings
from app.db import get_conn
from app.llm.client import LLMClient, get_llm_client
from app.models import StructuredOutputRequest, StructuredOutputResponse
from app.services.structured_outputs import StructuredOutputService

router = APIRouter()


@router.post(
    "/api/sessions/{session_id}/outputs",
    response_model=StructuredOutputResponse,
)
async def generate_output(
    session_id: str,
    body: StructuredOutputRequest,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
):
    require_configured(settings)
    return await StructuredOutputService(conn, settings).generate(session_id, body, llm)
