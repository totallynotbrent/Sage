from __future__ import annotations

import re
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
    limit: int | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> list[Session]:
    sessions = SessionService(conn, settings).list()
    if limit is not None and limit > 0:
        sessions = sessions[:limit]
    return sessions


@router.post("/api/sessions/bulk-delete")
async def bulk_delete_sessions(
    payload: dict,
    llm: LLMClient = Depends(get_llm_client),
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    """Delete many sessions at once. Body: {"ids": [...]} for specific ones,
    or {"keep": [...]} to delete everything EXCEPT the given ids."""
    ids = payload.get("ids")
    keep = payload.get("keep")
    service = SessionService(conn, settings)
    all_ids = [s.id for s in service.list()]
    if isinstance(keep, list):
        to_delete = [i for i in all_ids if i not in keep]
    elif isinstance(ids, list):
        to_delete = [i for i in ids if i in all_ids]
    else:
        from fastapi import HTTPException
        raise HTTPException(422, "provide 'ids' or 'keep'")
    deleted = 0
    for sid in to_delete:
        try:
            llm.cancel_inflight(sid)
            service.delete(sid)
            deleted += 1
        except Exception:
            pass
    return {"deleted": deleted}


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


def _title_excerpt(chunks: list[dict], budget: int = 3000) -> str:
    # first chunk of each file, labelled and capped, so the title reflects the subject
    seen: set[str] = set()
    parts: list[str] = []
    used = 0
    for chunk in chunks:
        file_name = chunk.get("file_name") or ""
        if file_name in seen:
            continue
        seen.add(file_name)
        section = (chunk.get("section") or "").strip()
        text = (chunk.get("unicode_text") or chunk.get("text") or "").strip()
        if section:
            text = f"{section}: {text}" if text else section
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)[:1500]
        parts.append(f"[{file_name}] {text}")
        used += len(text) + len(file_name)
        if used >= budget:
            break
    return "\n".join(parts)


@router.post("/api/sessions/{session_id}/title", response_model=Session)
async def generate_session_title(
    session_id: str,
    llm: LLMClient = Depends(get_llm_client),
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> Session:
    service = SessionService(conn, settings)
    session = service.get(session_id)
    excerpt = _title_excerpt(service.files.get_chunks_for_files(session.file_ids))
    user_content = f"Goal: {session.goal[:2000]}"
    if excerpt:
        user_content += f"\n\nDocument content:\n{excerpt}"
    prompt = [
        {
            "role": "system",
            "content": (
                "Create one concise session title, 3 to 7 words, based on the "
                "actual content of the uploaded documents, not the file name. "
                "Return only the title. "
                "Do not include secrets, credentials, personal data, or quotation marks."
            ),
        },
        {"role": "user", "content": user_content},
    ]
    generated, _ = await llm.complete_json(prompt, max_tokens=32, temperature=0.2)
    title = generated or session.goal
    title = re.sub(
        r"(?i)(?:api[_ -]?key|secret|token|password|authorization)\s*[:=]\s*\S+",
        "",
        title,
    )
    title = re.sub(r"\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b", "", title)
    title = re.sub(r"[\r\n\t]+", " ", title)
    title = title.strip().strip("\"'`")
    title = re.sub(r"\s+", " ", title)
    return service.update_title(session_id, title)


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
