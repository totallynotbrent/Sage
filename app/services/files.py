from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from app.config import Settings
from app.db import row_to_dict
from app.errors import (
    ConflictError,
    ExtractionError,
    FileTooLargeError,
    NotFoundError,
    StorageFullError,
    UnsupportedFormatError,
)
from app.models import FileRecord
from app.services.chunking import chunk_units
from app.services.extraction.base import ExtractionResult, get_extractor
from app.util import new_id, utc_now

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".md", ".txt", ".markdown"}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}


def sanitize_display_name(filename: str) -> str:
    name = Path(filename or "").name
    name = _CONTROL_CHARS.sub("", name).strip()
    return name or "untitled"


class FileService:

    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.settings = settings
        self.uploads_dir: Path = settings.uploads_dir
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(
        self, *, filename: str, content: bytes, content_type: str | None = None
    ) -> FileRecord:
        display_name = sanitize_display_name(filename)
        extension = Path(display_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise UnsupportedFormatError(
                f"Unsupported file format: {extension or '(none)'}. "
                f"Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            )

        if len(content) > self.settings.max_upload_mb * 1024 * 1024:
            raise FileTooLargeError(
                f"File {display_name!r} exceeds the {self.settings.max_upload_mb} MB "
                "per-file limit."
            )

        digest = hashlib.sha256(content).hexdigest()
        existing = self.conn.execute(
            "SELECT id FROM files WHERE sha256 = ?", (digest,)
        ).fetchone()
        if existing is not None:
            raise ConflictError(
                f"A file with identical content already exists in the library.",
                detail={"file_id": existing["id"], "sha256": digest},
            )

        total = self.conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM files"
        ).fetchone()["total"]
        if total + len(content) > self.settings.max_total_mb * 1024 * 1024:
            raise StorageFullError(
                f"The library would exceed the {self.settings.max_total_mb} MB "
                "total-storage limit."
            )

        file_id = new_id()
        storage_name = f"{file_id}{extension}"
        (self.uploads_dir / storage_name).write_bytes(content)

        now = utc_now()
        mime_type = content_type or _MIME_BY_EXT.get(extension, "application/octet-stream")
        self.conn.execute(
            """
            INSERT INTO files (id, display_name, storage_name, mime_type, size_bytes,
                               sha256, status, warnings, error, num_chunks, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', '[]', NULL, 0, ?, ?)
            """,
            (file_id, display_name, storage_name, mime_type, len(content),
             digest, now, now),
        )
        self.conn.commit()
        return self._ingest(file_id)

    def _ingest(self, file_id: str) -> FileRecord:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        record = row_to_dict(row)
        if record is None:
            raise NotFoundError("file", file_id)

        self.conn.execute(
            "UPDATE files SET status='pending', warnings='[]', error=NULL, "
            "num_chunks=0, updated_at=? WHERE id=?",
            (utc_now(), file_id),
        )
        self.conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        self.conn.commit()

        extension = Path(record["storage_name"]).suffix.lower()
        blob_path = self.uploads_dir / record["storage_name"]
        try:
            data = blob_path.read_bytes()
        except OSError as exc:
            return self._mark_failed(file_id, f"Could not read stored file: {exc}")

        try:
            extractor = get_extractor(extension)
            result: ExtractionResult = extractor.extract(data, filename=record["display_name"])
        except (ExtractionError, UnsupportedFormatError) as exc:
            return self._mark_failed(file_id, exc.message)

        if not result.ok:
            return self._mark_failed(file_id, result.error or "Extraction failed.")

        chunks = chunk_units(
            result.units,
            file_id=file_id,
            chunk_chars=self.settings.chunk_chars,
            overlap=self.settings.chunk_overlap,
        )
        self.conn.executemany(
            """
            INSERT INTO chunks (id, file_id, chunk_index, text, location_kind,
                                page, slide, section, start_line, end_line,
                                char_start, char_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.id,
                    file_id,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.location.kind if chunk.location else None,
                    chunk.location.page if chunk.location else None,
                    chunk.location.slide if chunk.location else None,
                    chunk.location.section if chunk.location else None,
                    chunk.location.start_line if chunk.location else None,
                    chunk.location.end_line if chunk.location else None,
                    chunk.char_start,
                    chunk.char_end,
                )
                for chunk in chunks
            ],
        )
        status = "partial" if result.warnings else "ready"
        self.conn.execute(
            "UPDATE files SET status=?, warnings=?, num_chunks=?, updated_at=? WHERE id=?",
            (status, json.dumps(result.warnings), len(chunks), utc_now(), file_id),
        )
        self.conn.commit()
        return self.get(file_id)

    def _mark_failed(self, file_id: str, error: str) -> FileRecord:
        self.conn.execute(
            "DELETE FROM chunks WHERE file_id = ?", (file_id,)
        )
        self.conn.execute(
            "UPDATE files SET status='failed', error=?, num_chunks=0, updated_at=? WHERE id=?",
            (error[:2000], utc_now(), file_id),
        )
        self.conn.commit()
        return self.get(file_id)

    def list(self) -> list[FileRecord]:
        rows = self.conn.execute(
            "SELECT * FROM files ORDER BY created_at DESC"
        ).fetchall()
        return [self._to_record(row_to_dict(r)) for r in rows]

    def get(self, file_id: str) -> FileRecord:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        record = row_to_dict(row)
        if record is None:
            raise NotFoundError("file", file_id)
        return self._to_record(record)

    def delete(self, file_id: str) -> None:
        row = self.conn.execute("SELECT storage_name FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise NotFoundError("file", file_id)
        blob = self.uploads_dir / row["storage_name"]
        try:
            blob.unlink(missing_ok=True)
        except OSError:
            pass
        self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self.conn.commit()

    def retry(self, file_id: str) -> FileRecord:
        self.get(file_id)
        return self._ingest(file_id)

    def get_ready_file_ids(self, file_ids: list[str]) -> list[str]:
        if not file_ids:
            return []
        placeholders = ",".join("?" * len(file_ids))
        rows = self.conn.execute(
            f"SELECT id FROM files WHERE id IN ({placeholders}) AND status = 'ready'",
            file_ids,
        ).fetchall()
        return [r["id"] for r in rows]

    def get_chunks_for_files(self, file_ids: list[str]) -> list[dict]:
        if not file_ids:
            return []
        placeholders = ",".join("?" * len(file_ids))
        rows = self.conn.execute(
            f"""
            SELECT c.*, f.display_name AS file_name
            FROM chunks c JOIN files f ON f.id = c.file_id
            WHERE c.file_id IN ({placeholders})
            ORDER BY c.file_id, c.chunk_index
            """,
            file_ids,
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _to_record(record: dict) -> FileRecord:
        warnings = record.get("warnings") or "[]"
        try:
            warnings_list = json.loads(warnings) if isinstance(warnings, str) else warnings
        except (json.JSONDecodeError, TypeError):
            warnings_list = []
        return FileRecord(
            id=record["id"],
            display_name=record["display_name"],
            mime_type=record.get("mime_type"),
            size_bytes=record["size_bytes"],
            sha256=record["sha256"],
            status=record.get("status", "pending"),
            warnings=warnings_list,
            error=record.get("error"),
            num_chunks=record.get("num_chunks", 0),
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )
