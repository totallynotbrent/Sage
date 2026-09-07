from __future__ import annotations

import hashlib
import json
import os
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

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".md", ".txt", ".markdown", ".tex"}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".tex": "text/x-tex",
}


def sanitize_display_name(filename: str) -> str:
    # Treat backslashes as separators too so Windows-style traversal
    # ("..\\name.txt") cannot smuggle path components into display names.
    name = Path(str(filename or "").replace("\\", "/")).name
    name = _CONTROL_CHARS.sub("", name).strip()
    if re.search(r"\[/?doc\]", filename or "", flags=re.IGNORECASE):
        raise ValueError("file names may not contain [DOC] or [/DOC] markers")
    return name or "untitled"


class FileService:
    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.settings = settings
        self.uploads_dir: Path = settings.uploads_dir
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None = None,
        source_path: str | None = None,
        subject: str | None = None,
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
                "A file with identical content already exists in the library.",
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
        mime_type = content_type or _MIME_BY_EXT.get(
            extension, "application/octet-stream"
        )
        self.conn.execute(
            """
            INSERT INTO files (id, display_name, storage_name, mime_type, size_bytes,
                               sha256, status, warnings, error, num_chunks,
                               paired_file_id, subject, source_path,
                               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', '[]', NULL, 0, NULL, ?, ?, ?, ?)
            """,
            (
                file_id,
                display_name,
                storage_name,
                mime_type,
                len(content),
                digest,
                subject,
                source_path,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self._ingest(file_id)

    def _ingest(self, file_id: str) -> FileRecord:
        row = self.conn.execute(
            "SELECT * FROM files WHERE id = ?", (file_id,)
        ).fetchone()
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
            result: ExtractionResult = extractor.extract(
                data, filename=record["display_name"]
            )
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
            INSERT INTO chunks (id, file_id, chunk_index, text, unicode_text,
                                environment, label, location_kind, page, slide,
                                section, start_line, end_line, char_start, char_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.id,
                    file_id,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.unicode_text,
                    chunk.location.environment if chunk.location else None,
                    chunk.location.label if chunk.location else None,
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
            "UPDATE files SET status=?, warnings=?, num_chunks=?, outline=?, updated_at=? WHERE id=?",
            (
                status,
                json.dumps(result.warnings),
                len(chunks),
                json.dumps(result.outline or []),
                utc_now(),
                file_id,
            ),
        )
        self.conn.commit()
        self._pair(file_id)
        return self.get(file_id)

    def _pair(self, file_id: str) -> None:
        row = self.conn.execute(
            "SELECT display_name FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        if row is None:
            return
        display_name = row["display_name"]
        stem = Path(display_name).stem
        own_suffix = Path(display_name).suffix.lower()
        candidates = self.conn.execute(
            "SELECT id, display_name FROM files WHERE id != ? ORDER BY rowid",
            (file_id,),
        ).fetchall()
        match_id: str | None = None
        for candidate in candidates:
            other_name = candidate["display_name"]
            if (
                Path(other_name).stem == stem
                and Path(other_name).suffix.lower() != own_suffix
            ):
                match_id = candidate["id"]
                break
        if match_id is not None:
            self.conn.execute(
                "UPDATE files SET paired_file_id = ? WHERE id = ?",
                (match_id, file_id),
            )
            self.conn.execute(
                "UPDATE files SET paired_file_id = ? WHERE id = ?",
                (file_id, match_id),
            )
        else:
            self.conn.execute(
                "UPDATE files SET paired_file_id = NULL WHERE id = ?", (file_id,)
            )
        self.conn.commit()

    def _mark_failed(self, file_id: str, error: str) -> FileRecord:
        self.conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        self.conn.execute(
            "UPDATE files SET status='failed', error=?, num_chunks=0, updated_at=? WHERE id=?",
            (error[:2000], utc_now(), file_id),
        )
        self.conn.commit()
        self._pair(file_id)
        return self.get(file_id)

    def list(self, subject: str | None = None) -> list[FileRecord]:
        if subject is not None:
            rows = self.conn.execute(
                "SELECT * FROM files WHERE subject = ? ORDER BY created_at DESC",
                (subject,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM files ORDER BY created_at DESC"
            ).fetchall()
        return [self._to_record(row_to_dict(r)) for r in rows]

    def get(self, file_id: str) -> FileRecord:
        row = self.conn.execute(
            "SELECT * FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        record = row_to_dict(row)
        if record is None:
            raise NotFoundError("file", file_id)
        return self._to_record(record)

    def get_content(self, file_id: str) -> dict:
        """The extracted (chunked) text of an ingested file, in chunk order.

        This is the same text Sage retrieves from to teach — good for a
        source-viewer tab. Raises NotFoundError if the file is unknown.
        """
        record = self.get(file_id)
        rows = self.conn.execute(
            "SELECT text FROM chunks WHERE file_id = ? ORDER BY chunk_index",
            (file_id,),
        ).fetchall()
        text = "\n\n".join(r["text"] for r in rows if r["text"]).strip()
        return {
            "id": record.id,
            "display_name": record.display_name,
            "mime_type": record.mime_type,
            "size_bytes": record.size_bytes,
            "num_chunks": record.num_chunks,
            "text": text,
        }

    def download(self, file_id: str):
        """The original uploaded bytes, for rendering the source file."""

        row = self.conn.execute(
            "SELECT storage_name, mime_type, display_name FROM files WHERE id = ?",
            (file_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("file", file_id)
        blob = self.uploads_dir / row["storage_name"]
        if not blob.is_file():
            raise NotFoundError("file blob", file_id)
        return blob, row["mime_type"] or "application/octet-stream", row["display_name"]

    def delete(self, file_id: str) -> None:
        row = self.conn.execute(
            "SELECT storage_name, paired_file_id FROM files WHERE id = ?",
            (file_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("file", file_id)
        if row["paired_file_id"]:
            self.conn.execute(
                "UPDATE files SET paired_file_id = NULL WHERE id = ?",
                (row["paired_file_id"],),
            )
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

    def ingest_from_disk(
        self, path: str, subject: str | None = None, root: str | None = None
    ) -> FileRecord:
        candidate = Path(path)
        if candidate.is_symlink():
            raise ValueError(f"refusing to ingest symlink: {path}")
        abs_path = str(candidate.resolve())
        blob = Path(abs_path)
        if blob.is_symlink():
            raise ValueError(f"refusing to ingest symlink target: {path}")
        if not blob.is_file():
            raise NotFoundError("file on disk", path)
        if root is not None:
            resolved_root = str(Path(root).resolve())
            try:
                common = os.path.commonpath([abs_path, resolved_root])
            except ValueError:
                common = ""
            if common != resolved_root:
                raise ValueError(f"file resolves outside watched root: {path}")
        if blob.stat().st_size > self.settings.max_upload_mb * 1024 * 1024:
            raise FileTooLargeError(
                f"File {blob.name!r} exceeds the {self.settings.max_upload_mb} MB "
                "per-file limit."
            )
        content = blob.read_bytes()
        return self.save_upload(
            filename=blob.name,
            content=content,
            content_type=_MIME_BY_EXT.get(blob.suffix.lower()),
            source_path=abs_path,
            subject=subject,
        )

    def replace_from_disk(
        self,
        path: str,
        old_file_id: str,
        subject: str | None = None,
        root: str | None = None,
    ) -> FileRecord:
        record = self.ingest_from_disk(path, subject, root=root)
        self.delete(old_file_id)
        self._pair(record.id)
        return record

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
            SELECT c.*, f.display_name AS file_name, f.subject AS subject,
                   fp.display_name AS pair_display_name
            FROM chunks c
            JOIN files f ON f.id = c.file_id
            LEFT JOIN files fp ON fp.id = f.paired_file_id AND fp.status = 'ready'
            WHERE c.file_id IN ({placeholders})
            ORDER BY c.file_id, c.chunk_index
            """,
            file_ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def expand_pairings(self, file_ids: list[str]) -> list[str]:
        if not file_ids:
            return []
        placeholders = ",".join("?" * len(file_ids))
        rows = self.conn.execute(
            f"""
            SELECT paired_file_id FROM files
            WHERE id IN ({placeholders})
              AND paired_file_id IS NOT NULL
              AND paired_file_id IN (SELECT id FROM files WHERE status = 'ready')
            """,
            file_ids,
        ).fetchall()
        expanded = list(file_ids)
        for row in rows:
            pair_id = row["paired_file_id"]
            if pair_id not in expanded:
                expanded.append(pair_id)
        return expanded

    def get_subjects(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT subject FROM files "
            "WHERE subject IS NOT NULL AND subject != '' ORDER BY subject"
        ).fetchall()
        return [r["subject"] for r in rows]

    @staticmethod
    def _to_record(record: dict) -> FileRecord:
        warnings = record.get("warnings") or "[]"
        try:
            warnings_list = (
                json.loads(warnings) if isinstance(warnings, str) else warnings
            )
        except (json.JSONDecodeError, TypeError):
            warnings_list = []
        outline = record.get("outline") or "[]"
        try:
            outline_list = json.loads(outline) if isinstance(outline, str) else outline
        except (json.JSONDecodeError, TypeError):
            outline_list = []
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
            paired_file_id=record.get("paired_file_id"),
            outline=outline_list or None,
            subject=record.get("subject"),
            source_path=record.get("source_path"),
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )
