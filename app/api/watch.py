from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.config import Settings, get_app_settings
from app.db import get_conn
from app.models import WatchBody
from app.services.watcher import (
    delete_watch_source,
    list_watch_sources,
    run_scan_once,
    sync_watch_sources,
)
from app.util import new_id, utc_now

router = APIRouter()


def _resolve_in_scope(candidate: str, settings: Settings) -> Path:
    resolved = Path(candidate).resolve()
    if resolved == Path(resolved.anchor):
        raise HTTPException(
            status_code=400,
            detail=f"path is a filesystem root: {resolved}",
        )
    for base in settings.watch_dirs:
        base_resolved = Path(base).resolve()
        if base_resolved == Path(base_resolved.anchor):
            continue
        try:
            common = os.path.commonpath([str(resolved), str(base_resolved)])
        except ValueError:
            continue
        if common == str(base_resolved):
            return resolved
    raise HTTPException(
        status_code=400,
        detail=f"path is outside the configured watch directories: {resolved}",
    )


@router.get("/api/watch")
async def list_watch(
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> list[dict]:
    sync_watch_sources(conn, settings)
    return list_watch_sources(conn)


@router.post("/api/watch")
async def add_watch(
    body: WatchBody,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    path = body.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="path must not be empty")
    if not settings.watch_dirs:
        raise HTTPException(
            status_code=400,
            detail="no watch directories configured; set SAGE_WATCH_DIRS in the "
            "server environment",
        )
    resolved = _resolve_in_scope(path, settings)
    if not resolved.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"path is not an existing directory: {resolved}",
        )
    now = utc_now()
    conn.execute(
        "INSERT INTO watch_sources (id, path, enabled, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET enabled = 1, updated_at = excluded.updated_at",
        (new_id(), str(resolved), now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM watch_sources WHERE path = ?", (str(resolved),)
    ).fetchone()
    source = dict(row) if row is not None else {}
    scan = await run_scan_once(conn, settings, source["id"])
    return {"source": source, "scan": scan}


@router.post("/api/watch/scan")
async def scan_all(
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    sync_watch_sources(conn, settings)
    results = []
    for source in list_watch_sources(conn):
        scan = await run_scan_once(conn, settings, source["id"])
        results.append(
            {
                "id": source["id"],
                "path": source["path"],
                "scan": scan,
            }
        )
    return {"sources": results}


@router.delete("/api/watch/{watch_id}", status_code=204)
async def remove_watch(
    watch_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
) -> JSONResponse:
    delete_watch_source(conn, watch_id)
    return JSONResponse(status_code=204, content=None)
