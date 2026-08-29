from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse

from app.api.deps import handle_value_error
from app.config import Settings, get_app_settings
from app.db import get_conn, row_to_dict
from app.errors import NotFoundError
from app.llm.messages import format_location
from app.models import FileRecord
from app.services.files import FileService

router = APIRouter()


@router.post("/api/files", response_model=list[FileRecord])
async def upload_files(
    files: list[UploadFile] = File(...),
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> list[FileRecord]:
    service = FileService(conn, settings)
    results: list[FileRecord] = []
    for upload in files:
        content = await upload.read()
        try:
            results.append(
                service.save_upload(
                    filename=upload.filename or "",
                    content=content,
                    content_type=upload.content_type or "",
                )
            )
        except ValueError as exc:
            raise handle_value_error(exc)
    return results


@router.get("/api/files", response_model=list[FileRecord])
async def list_files(
    subject: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> list[FileRecord]:
    return FileService(conn, settings).list(subject=subject)


@router.get("/api/files/{file_id}", response_model=FileRecord)
async def get_file(
    file_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> FileRecord:
    return FileService(conn, settings).get(file_id)


@router.get("/api/files/{file_id}/content")
async def get_file_content(
    file_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    return FileService(conn, settings).get_content(file_id)


@router.get("/api/files/{file_id}/excerpts")
async def get_excerpts(
    file_id: str,
    chunk: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    row = conn.execute(
        """
        SELECT c.*, f.display_name AS file_name
        FROM chunks c JOIN files f ON f.id = c.file_id
        WHERE c.id = ? AND c.file_id = ?
        """,
        (chunk or "", file_id),
    ).fetchone()
    if row is None:
        raise NotFoundError("chunk", chunk or "(none)")
    chunk_row = dict(row)

    neighbors = conn.execute(
        """
        SELECT c.id, c.text, c.chunk_index FROM chunks c
        WHERE c.file_id = ? AND c.chunk_index IN (?, ?)
        ORDER BY c.chunk_index
        """,
        (file_id, chunk_row["chunk_index"] - 1, chunk_row["chunk_index"] + 1),
    ).fetchall()
    context_around = {"before": None, "after": None}
    for neighbor in neighbors:
        if neighbor["chunk_index"] < chunk_row["chunk_index"]:
            context_around["before"] = neighbor["text"]
        else:
            context_around["after"] = neighbor["text"]

    return {
        "chunk_id": chunk_row["id"],
        "file_name": chunk_row["file_name"],
        "location": format_location(chunk_row),
        "text": chunk_row["text"],
        "context_around": context_around,
    }


@router.delete("/api/files/{file_id}", status_code=204)
async def delete_file(
    file_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> JSONResponse:
    FileService(conn, settings).delete(file_id)
    return JSONResponse(status_code=204, content=None)


@router.post("/api/files/{file_id}/retry", response_model=FileRecord)
async def retry_file(
    file_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> FileRecord:
    return FileService(conn, settings).retry(file_id)
