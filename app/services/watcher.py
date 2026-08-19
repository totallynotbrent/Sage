from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.db import open_db
from app.errors import NotFoundError
from app.services.files import ALLOWED_EXTENSIONS, FileService
from app.util import new_id, utc_now

logger = logging.getLogger("app")

SCAN_MIN_INTERVAL_SECONDS = 30

_scan_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _get_scan_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _scan_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _scan_locks[loop] = lock
    return lock


def sync_watch_sources(conn: sqlite3.Connection, settings: Settings) -> list[dict]:
    now = utc_now()
    for path in settings.watch_dirs:
        conn.execute(
            "INSERT OR IGNORE INTO watch_sources "
            "(id, path, enabled, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
            (new_id(), str(Path(path).resolve()), now, now),
        )
    conn.commit()
    return list_watch_sources(conn)


def list_watch_sources(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM watch_sources WHERE enabled = 1 ORDER BY created_at, rowid"
    ).fetchall()
    return [dict(row) for row in rows]


def delete_watch_source(conn: sqlite3.Connection, watch_id: str) -> None:
    cursor = conn.execute(
        "UPDATE watch_sources SET enabled = 0, updated_at = ? WHERE id = ?",
        (utc_now(), watch_id),
    )
    conn.commit()
    if cursor.rowcount == 0:
        raise NotFoundError("watch source", watch_id)


def _scan_cutoff() -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(seconds=SCAN_MIN_INTERVAL_SECONDS))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def claim_scan(conn: sqlite3.Connection, watch_id: str) -> bool:
    cursor = conn.execute(
        "UPDATE watch_sources SET last_scan_at = ? "
        "WHERE id = ? AND enabled = 1 "
        "AND (last_scan_at IS NULL OR last_scan_at < ?)",
        (utc_now(), watch_id, _scan_cutoff()),
    )
    conn.commit()
    return cursor.rowcount == 1


def rate_limited_summary() -> dict:
    return {
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "warnings": [],
        "rate_limited": True,
    }


async def run_scan_once(
    conn: sqlite3.Connection, settings: Settings, watch_id: str
) -> dict:
    if not claim_scan(conn, watch_id):
        return rate_limited_summary()
    async with _get_scan_lock():
        return await asyncio.to_thread(scan_once, conn, settings, watch_id)


def scan_once(conn: sqlite3.Connection, settings: Settings, watch_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM watch_sources WHERE id = ?", (watch_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("watch source", watch_id)
    root = Path(row["path"])
    service = FileService(conn, settings)
    summary: dict = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}
    warnings: list[str] = []

    if not root.is_dir():
        summary["errors"] += 1
        warnings.append(f"watch root does not exist: {root}")
        return _finish_scan(conn, watch_id, summary, warnings)

    resolved_root = root.resolve()
    if root.is_symlink():
        summary["errors"] += 1
        warnings.append(f"watch root is a symlink: {root}")
        return _finish_scan(conn, watch_id, summary, warnings)

    if settings.watch_dirs:
        in_scope = False
        for base in settings.watch_dirs:
            base_resolved = Path(base).resolve()
            if base_resolved == Path(base_resolved.anchor):
                continue
            try:
                common = os.path.commonpath([str(resolved_root), str(base_resolved)])
            except ValueError:
                continue
            if common == str(base_resolved):
                in_scope = True
                break
        if not in_scope:
            summary["errors"] += 1
            warnings.append(
                f"watch root is outside the configured watch directories: "
                f"{resolved_root}"
            )
            return _finish_scan(conn, watch_id, summary, warnings)

    prefix = str(resolved_root) + os.sep
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    known = {
        item["source_path"]: dict(item)
        for item in conn.execute(
            "SELECT id, source_path, sha256 FROM files "
            "WHERE source_path IS NOT NULL AND source_path LIKE ? ESCAPE '\\'",
            (escaped + "%",),
        ).fetchall()
    }
    limit_bytes = settings.max_upload_mb * 1024 * 1024

    for dirpath, dirnames, filenames in os.walk(resolved_root, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.is_symlink():
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            abs_path = str(path)
            try:
                if path.stat().st_size > limit_bytes:
                    warnings.append(
                        f"skipping oversized file (>{settings.max_upload_mb} MB): "
                        f"{abs_path}"
                    )
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                summary["errors"] += 1
                warnings.append(f"could not read {abs_path}: {exc}")
                continue
            relative = path.relative_to(resolved_root)
            subject = relative.parts[0] if len(relative.parts) > 1 else None
            existing = known.pop(abs_path, None)
            try:
                if existing is None:
                    service.ingest_from_disk(abs_path, subject, root=str(resolved_root))
                    summary["added"] += 1
                elif existing["sha256"] != digest:
                    service.replace_from_disk(
                        abs_path, existing["id"], subject, root=str(resolved_root)
                    )
                    summary["updated"] += 1
                else:
                    summary["skipped"] += 1
            except Exception as exc:  # noqa: BLE001 - keep scanning on one bad file
                summary["errors"] += 1
                warnings.append(f"ingest failed for {abs_path}: {exc}")

    for missing_path in sorted(known):
        warnings.append(f"file missing from disk (kept in library): {missing_path}")

    return _finish_scan(conn, watch_id, summary, warnings)


def _finish_scan(
    conn: sqlite3.Connection,
    watch_id: str,
    summary: dict,
    warnings: list[str],
) -> dict:
    conn.execute(
        "UPDATE watch_sources SET last_scan_at = ?, last_error = ?, updated_at = ? "
        "WHERE id = ?",
        (
            utc_now(),
            json.dumps(warnings) if warnings else None,
            utc_now(),
            watch_id,
        ),
    )
    conn.commit()
    return {**summary, "warnings": warnings}


def _scan_iteration(settings: Settings) -> None:
    conn = open_db(settings.db_path)
    try:
        sync_watch_sources(conn, settings)
        for source in list_watch_sources(conn):
            if claim_scan(conn, source["id"]):
                scan_once(conn, settings, source["id"])
    finally:
        conn.close()


async def _run_iteration(settings: Settings) -> None:
    async with _get_scan_lock():
        await asyncio.to_thread(_scan_iteration, settings)


async def watcher_loop(settings: Settings, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.watch_scan_seconds
            )
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(
                _run_iteration(settings), timeout=settings.watch_scan_seconds
            )
        except asyncio.TimeoutError:
            logger.warning(
                "watch scan iteration exceeded %ss; continuing",
                settings.watch_scan_seconds,
            )
        except Exception:  # noqa: BLE001 - never crash the app from the watcher
            logger.exception("watch scan iteration failed")
