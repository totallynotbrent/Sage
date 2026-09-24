from __future__ import annotations

import sqlite3

import pytest

from tests.conftest import upload_txt


def _chunk_ids(settings, file_id: str) -> list[str]:
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, text FROM chunks WHERE file_id=? ORDER BY chunk_index",
            (file_id,),
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def test_upload_list_detail(client):
    record = upload_txt(
        client, "notes.md", "# Title\n\nBody about biology cells.", "text/markdown"
    )
    assert record["status"] == "ready"
    assert record["num_chunks"] >= 1
    assert len(record["warnings"]) == 0

    listing = client.get("/api/files").json()
    assert [f["id"] for f in listing] == [record["id"]]

    detail = client.get(f"/api/files/{record['id']}").json()
    assert detail["display_name"] == "notes.md"
    assert detail["size_bytes"] > 0


def test_upload_multiple_files(client):
    response = client.post(
        "/api/files",
        files=[
            ("files", ("a.md", b"# a\ncontent about alpha", "text/markdown")),
            ("files", ("b.txt", b"content about beta", "text/plain")),
        ],
    )
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 2
    assert {r["status"] for r in records} == {"ready"}


def test_upload_duplicate_returns_409(client):
    upload_txt(client, "a.md", "same content", "text/markdown")
    response = client.post(
        "/api/files",
        files={"files": ("b.md", b"same content", "text/markdown")},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert "file_id" in body["error"]["detail"]


def test_upload_unsupported_returns_415(client):
    response = client.post(
        "/api/files",
        files={"files": ("bad.exe", b"x", "application/octet-stream")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_format"


def test_upload_doc_marker_filename_returns_400(client):
    response = client.post(
        "/api/files",
        files={"files": ("[DOC]notes.txt", b"content", "text/plain")},
    )
    assert response.status_code == 400
    assert "[DOC]" in response.json()["error"]["message"]
    response = client.post(
        "/api/files",
        files={"files": ("[/doc]notes.md", b"content", "text/markdown")},
    )
    assert response.status_code == 400


def test_upload_empty_file_fails_extraction(client):
    record = upload_txt(client, "empty.txt", "")
    assert record["status"] == "failed"
    assert record["error"]
    assert record["num_chunks"] == 0


def test_get_missing_file_404(client):
    response = client.get("/api/files/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_delete_and_detail_404(client):
    record = upload_txt(client, "gone.md", "# gone\ncontent", "text/markdown")
    response = client.delete(f"/api/files/{record['id']}")
    assert response.status_code == 204
    assert client.get(f"/api/files/{record['id']}").status_code == 404


def test_retry_failed_file(client):
    record = upload_txt(client, "empty.txt", "")
    assert record["status"] == "failed"
    response = client.post(f"/api/files/{record['id']}/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"


def test_retry_missing_404(client):
    assert client.post("/api/files/nope/retry").status_code == 404


def test_excerpts_roundtrip(client, settings):
    text = "# Reading\n\nA mitochondria produces energy for the cell. This is the key fact."
    record = upload_txt(client, "bio.md", text, "text/markdown")
    chunk_ids = _chunk_ids(settings, record["id"])
    assert chunk_ids

    response = client.get(
        f"/api/files/{record['id']}/excerpts", params={"chunk": chunk_ids[0]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chunk_id"] == chunk_ids[0]
    assert body["file_name"] == "bio.md"
    assert body["location"]
    assert "mitochondria" in body["text"]
    assert "context_around" in body


def test_excerpts_unknown_chunk_404(client, settings):
    record = upload_txt(client, "n.txt", "plain content", "text/plain")
    response = client.get(
        f"/api/files/{record['id']}/excerpts", params={"chunk": "nope"}
    )
    assert response.status_code == 404


def test_excerpts_list_mode_without_chunk(client, settings):
    # the home viewer requests excerpts with no chunk id; the endpoint lists
    # every chunk in order instead of 404ing
    text = (
        "# Heading\n\nFirst section body text with enough words to chunk. "
        "Second section body text with more words so a second chunk exists "
        "and the listing has multiple entries to page through."
    )
    record = upload_txt(client, "multi.md", text, "text/markdown")
    response = client.get(f"/api/files/{record['id']}/excerpts")
    assert response.status_code == 200
    body = response.json()
    excerpts = body["excerpts"]
    assert excerpts, "expected at least one chunk"
    indices = [e["chunk_index"] for e in excerpts]
    assert indices == sorted(indices)
    for e in excerpts:
        assert e["chunk_id"]
        assert e["location"]
        assert e["text"]


def test_excerpts_list_mode_unknown_file_404(client):
    assert client.get("/api/files/nope/excerpts").status_code == 404


def test_failed_file_has_no_chunks_and_not_retrievable(client, settings):
    record = upload_txt(client, "empty.txt", "")
    assert record["status"] == "failed"
    assert _chunk_ids(settings, record["id"]) == []
    response = client.get(
        f"/api/files/{record['id']}/excerpts", params={"chunk": "any:0:0"}
    )
    assert response.status_code == 404


def test_health_reports_ok_with_fake(app, client, override_llm):
    override_llm.probe_result = (True, "ok")
    report = client.get("/api/health").json()
    assert report["status"] == "ok"
    assert report["model_configured"] is True
    assert report["endpoint_reachable"] is True
    assert report["dependency_errors"] == []


def test_pdf_download_returns_original_bytes(client):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "download me")
    blob = doc.tobytes()
    record = client.post(
        "/api/files", files={"files": ("note.pdf", blob, "application/pdf")}
    ).json()[0]
    r = client.get(f"/api/files/{record['id']}/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content == blob
