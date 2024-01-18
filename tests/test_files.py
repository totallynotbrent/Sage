from __future__ import annotations

import sqlite3
import time

import pytest

from app.config import Settings
from app.errors import (
    ConflictError,
    FileTooLargeError,
    NotFoundError,
    StorageFullError,
    UnsupportedFormatError,
)
from app.services.files import FileService


def _service(conn: sqlite3.Connection, settings: Settings) -> FileService:
    return FileService(conn, settings)


def test_save_and_get(conn, settings):
    record = _service(conn, settings).save_upload(
        filename="notes.md", content=b"# Hello\n\nWorld.", content_type="text/markdown"
    )
    assert record.status == "ready"
    assert record.display_name == "notes.md"
    assert record.num_chunks == 1
    assert len(record.sha256) == 64

    fetched = _service(conn, settings).get(record.id)
    assert fetched.id == record.id


def test_save_then_list_orders_by_recency(conn, settings):
    service = _service(conn, settings)
    first = service.save_upload(filename="a.md", content=b"# a\ncontent about alpha", content_type="text/markdown")
    time.sleep(0.02)
    second = service.save_upload(filename="b.txt", content=b"content about beta", content_type="text/plain")
    ids = [r.id for r in service.list()]
    assert ids[0] == second.id
    assert ids[1] == first.id


def test_duplicate_content_raises_conflict(conn, settings):
    service = _service(conn, settings)
    service.save_upload(filename="a.md", content=b"identical content", content_type="text/markdown")
    with pytest.raises(ConflictError) as exc_info:
        service.save_upload(filename="b.md", content=b"identical content", content_type="text/markdown")
    assert "already exists" in exc_info.value.message
    detail = exc_info.value.detail
    assert isinstance(detail, dict) and detail.get("file_id")


def test_unsupported_extension_raises(conn, settings):
    service = _service(conn, settings)
    with pytest.raises(UnsupportedFormatError):
        service.save_upload(filename="virus.exe", content=b"x", content_type="application/octet-stream")
    with pytest.raises(UnsupportedFormatError):
        service.save_upload(filename="noextension", content=b"x", content_type="text/plain")


def test_upload_too_large_raises(conn, settings):
    small = settings.model_copy(update={"max_upload_mb": 1})
    service = _service(conn, small)
    with pytest.raises(FileTooLargeError):
        service.save_upload(
            filename="big.txt", content=b"x" * (1 * 1024 * 1024 + 1), content_type="text/plain"
        )


def test_total_storage_limit_raises(conn, settings):
    limited = settings.model_copy(update={"max_upload_mb": 20, "max_total_mb": 15})
    service = _service(conn, limited)
    payload = b"y" * (10 * 1024 * 1024)
    service.save_upload(filename="one.txt", content=payload, content_type="text/plain")
    with pytest.raises(StorageFullError):
        service.save_upload(
            filename="two.txt", content=b"z" * (10 * 1024 * 1024), content_type="text/plain"
        )


def test_storage_name_is_safe(conn, settings):
    service = _service(conn, settings)
    record = service.save_upload(
        filename="../../evil/..\\name.txt", content=b"safe", content_type="text/plain"
    )
    assert record.display_name == "name.txt"
    blob = settings.uploads_dir / f"{record.id}.txt"
    assert blob.exists()
    assert blob.read_bytes() == b"safe"
    assert list(settings.data_dir.glob("..*")) == []
    assert not (settings.data_dir / "evil").exists()


def test_delete_removes_blob_and_cascade(conn, settings):
    service = _service(conn, settings)
    record = service.save_upload(filename="d.md", content=b"# d\ncontent", content_type="text/markdown")
    chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE file_id=?", (record.id,)).fetchone()["n"]
    assert chunks >= 1
    service.delete(record.id)
    assert not (settings.uploads_dir / f"{record.id}.md").exists()
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE file_id=?", (record.id,)).fetchone()["n"] == 0
    with pytest.raises(NotFoundError):
        service.get(record.id)


def test_failed_extraction_status(conn, settings):
    service = _service(conn, settings)
    record = service.save_upload(filename="empty.txt", content=b"", content_type="text/plain")
    assert record.status == "failed"
    assert record.error
    assert record.num_chunks == 0


def test_retry_refreshes_status(conn, settings):
    service = _service(conn, settings)
    record = service.save_upload(filename="ok.md", content=b"# ok\ncontent here", content_type="text/markdown")
    assert record.status == "ready"
    retried = service.retry(record.id)
    assert retried.status == "ready"
    assert retried.num_chunks == record.num_chunks
