from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import threading
import time

import pytest

from app.config import Settings
from app.db import init_db
from app.llm.client import get_llm_client
from app.services import watcher
from app.services.watcher import (
    claim_scan,
    delete_watch_source,
    list_watch_sources,
    scan_once,
    sync_watch_sources,
    watcher_loop,
)
from tests.fakes.fake_llm import FakeLLM

_TEX_SOURCE = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\section{First}\n"
    "\\begin{theorem}\n"
    "If $f$ is continuous then $\\int_a^b f(x)\\,dx$ exists.\n"
    "\\end{theorem}\n"
    "\\end{document}\n"
)

_TEX_CHANGED = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\section{First}\n"
    "\\begin{theorem}\n"
    "A changed statement about $\\alpha$.\n"
    "\\end{theorem}\n"
    "\\end{document}\n"
)

_TEX_OTHER = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\section{First}\n"
    "\\begin{theorem}\n"
    "A distinct statement about $\\beta$.\n"
    "\\end{theorem}\n"
    "\\end{document}\n"
)


def _watcher_settings(settings: Settings, root) -> Settings:
    return settings.model_copy(update={"watch_dirs": [str(root)]})


def _single_source(conn, watch_settings: Settings) -> dict:
    sync_watch_sources(conn, watch_settings)
    sources = list_watch_sources(conn)
    assert len(sources) == 1
    return sources[0]


def test_scan_adds_new_files(conn, settings, tmp_path):
    pytest.importorskip("pylatexenc")
    root = tmp_path / "notes"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    (subject_dir / "calc.tex").write_text(_TEX_SOURCE, encoding="utf-8")

    source = _single_source(conn, _watcher_settings(settings, root))
    summary = scan_once(conn, _watcher_settings(settings, root), source["id"])
    assert summary["added"] == 1
    assert summary["updated"] == 0
    assert summary["skipped"] == 0
    assert summary["errors"] == 0

    row = conn.execute("SELECT * FROM files").fetchone()
    assert row["subject"] == "calculus"
    assert row["source_path"].endswith("calc.tex")
    assert row["status"] == "ready"
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] > 0


def test_scan_skips_unchanged(conn, settings, tmp_path):
    pytest.importorskip("pylatexenc")
    root = tmp_path / "notes"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    (subject_dir / "calc.tex").write_text(_TEX_SOURCE, encoding="utf-8")

    watch_settings = _watcher_settings(settings, root)
    source = _single_source(conn, watch_settings)
    scan_once(conn, watch_settings, source["id"])
    summary = scan_once(conn, watch_settings, source["id"])
    assert summary["added"] == 0
    assert summary["skipped"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 1


def test_scan_reingests_changed_without_dangling(conn, settings, tmp_path):
    pytest.importorskip("pylatexenc")
    root = tmp_path / "notes"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    tex_path = subject_dir / "calc.tex"
    tex_path.write_text(_TEX_SOURCE, encoding="utf-8")

    watch_settings = _watcher_settings(settings, root)
    source = _single_source(conn, watch_settings)
    scan_once(conn, watch_settings, source["id"])
    tex_path.write_text(_TEX_CHANGED, encoding="utf-8")

    summary = scan_once(conn, watch_settings, source["id"])
    assert summary["updated"] == 1
    assert summary["skipped"] == 0

    rows = conn.execute("SELECT id FROM files").fetchall()
    assert len(rows) == 1
    current_id = rows[0]["id"]
    chunk_files = conn.execute("SELECT DISTINCT file_id FROM chunks").fetchall()
    assert [c["file_id"] for c in chunk_files] == [current_id]


def test_missing_from_disk_keeps_file_with_warning(conn, settings, tmp_path):
    pytest.importorskip("pylatexenc")
    root = tmp_path / "notes"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    tex_path = subject_dir / "calc.tex"
    tex_path.write_text(_TEX_SOURCE, encoding="utf-8")

    watch_settings = _watcher_settings(settings, root)
    source = _single_source(conn, watch_settings)
    scan_once(conn, watch_settings, source["id"])
    tex_path.unlink()

    summary = scan_once(conn, watch_settings, source["id"])
    assert summary["skipped"] == 0
    assert any("missing from disk" in w for w in summary["warnings"])
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 1


def test_root_level_file_has_no_subject(conn, settings, tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "plain.md").write_text("# x\ncontent", encoding="utf-8")

    watch_settings = _watcher_settings(settings, root)
    source = _single_source(conn, watch_settings)
    scan_once(conn, watch_settings, source["id"])
    row = conn.execute("SELECT subject FROM files").fetchone()
    assert row["subject"] is None


def test_delete_watch_source_soft_disables_row(conn, settings, tmp_path):
    source = _single_source(conn, _watcher_settings(settings, tmp_path / "notes"))
    delete_watch_source(conn, source["id"])
    assert list_watch_sources(conn) == []
    row = conn.execute(
        "SELECT enabled, last_scan_at FROM watch_sources WHERE id = ?",
        (source["id"],),
    ).fetchone()
    assert row["enabled"] == 0
    assert row["last_scan_at"] == source["last_scan_at"]


def test_claim_scan_is_atomic_and_rate_limited(conn, settings, tmp_path):
    source = _single_source(conn, _watcher_settings(settings, tmp_path / "notes"))
    assert claim_scan(conn, source["id"]) is True
    assert claim_scan(conn, source["id"]) is False
    conn.execute(
        "UPDATE watch_sources SET last_scan_at = NULL WHERE id = ?", (source["id"],)
    )
    conn.commit()
    assert claim_scan(conn, source["id"]) is True


def test_claim_scan_skips_disabled_source(conn, settings, tmp_path):
    source = _single_source(conn, _watcher_settings(settings, tmp_path / "notes"))
    delete_watch_source(conn, source["id"])
    assert claim_scan(conn, source["id"]) is False


def test_watch_api_round_trip(tmp_path):
    pytest.importorskip("pylatexenc")
    from fastapi.testclient import TestClient

    from app.main import create_app

    root = tmp_path / "watched"
    subject_dir = root / "algebra"
    subject_dir.mkdir(parents=True)
    (subject_dir / "notes.tex").write_text(_TEX_SOURCE, encoding="utf-8")

    app_settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        max_upload_mb=30,
        max_total_mb=500,
        chunk_chars=100,
        chunk_overlap=20,
        context_chunk_budget=8,
        watch_dirs=[str(root)],
        watch_scan_seconds=3600,
    )
    app = create_app(app_settings)
    with TestClient(app) as client:
        created = client.post("/api/watch", json={"path": str(root)})
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["scan"]["added"] == 1
        watch_id = body["source"]["id"]

        listing = client.get("/api/watch").json()
        assert any(s["id"] == watch_id for s in listing)

        scanned = client.post("/api/watch/scan").json()
        assert scanned["sources"][0]["scan"]["rate_limited"] is True

        files_all = client.get("/api/files").json()
        assert len(files_all) == 1
        assert files_all[0]["subject"] == "algebra"
        assert files_all[0]["source_path"].endswith("notes.tex")

        filtered = client.get("/api/files", params={"subject": "algebra"}).json()
        assert len(filtered) == 1
        assert client.get("/api/files", params={"subject": "nope"}).json() == []

        deleted = client.delete(f"/api/watch/{watch_id}")
        assert deleted.status_code == 204
        gone = client.get("/api/watch").json()
        assert all(s["id"] != watch_id for s in gone)

        readded = client.post("/api/watch", json={"path": str(root)})
        assert readded.status_code == 200, readded.text
        assert readded.json()["source"]["id"] == watch_id
        assert readded.json()["scan"]["rate_limited"] is True


def test_watch_api_rejects_empty_path(client):
    response = client.post("/api/watch", json={"path": "   "})
    assert response.status_code == 400
    assert "must not be empty" in response.json()["error"]["message"]


def test_watch_api_rejects_no_watch_dirs(client, tmp_path):
    root = tmp_path / "watched"
    root.mkdir()
    response = client.post("/api/watch", json={"path": str(root)})
    assert response.status_code == 400
    assert "SAGE_WATCH_DIRS" in response.json()["error"]["message"]


def test_watch_api_rejects_missing_directory(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    root = tmp_path / "watched"
    root.mkdir()
    app_settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        watch_dirs=[str(root)],
        watch_scan_seconds=3600,
    )
    app = create_app(app_settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/watch", json={"path": str(root / "does-not-exist")}
        )
        assert response.status_code == 400
        assert "not an existing directory" in response.json()["error"]["message"]


def test_watch_api_rejects_file_path(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    root = tmp_path / "watched"
    root.mkdir()
    target = root / "not-a-dir.txt"
    target.write_text("x", encoding="utf-8")
    app_settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        watch_dirs=[str(root)],
        watch_scan_seconds=3600,
    )
    app = create_app(app_settings)
    with TestClient(app) as client:
        response = client.post("/api/watch", json={"path": str(target)})
        assert response.status_code == 400
        assert "not an existing directory" in response.json()["error"]["message"]


def test_watch_api_rejects_outside_scope(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    app_settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        watch_dirs=[str(base)],
        watch_scan_seconds=3600,
    )
    app = create_app(app_settings)
    with TestClient(app) as client:
        response = client.post("/api/watch", json={"path": str(outside)})
        assert response.status_code == 400
        assert (
            "outside the configured watch directories"
            in response.json()["error"]["message"]
        )


def test_watch_api_rejects_filesystem_root(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    root = tmp_path / "watched"
    root.mkdir()
    app_settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        watch_dirs=[str(root)],
        watch_scan_seconds=3600,
    )
    app = create_app(app_settings)
    with TestClient(app) as client:
        response = client.post("/api/watch", json={"path": "/"})
        assert response.status_code == 400
        assert "filesystem root" in response.json()["error"]["message"]


def test_watch_api_aliased_paths_collapse(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    root = tmp_path / "watched"
    root.mkdir()
    app_settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        watch_dirs=[str(tmp_path)],
        watch_scan_seconds=3600,
    )
    app = create_app(app_settings)
    with TestClient(app) as client:
        first = client.post("/api/watch", json={"path": str(root)})
        assert first.status_code == 200, first.text
        first_id = first.json()["source"]["id"]

        alias = client.post("/api/watch", json={"path": str(root) + os.sep})
        assert alias.status_code == 200, alias.text
        assert alias.json()["source"]["id"] == first_id

        dot_alias = client.post("/api/watch", json={"path": str(root) + "/."})
        assert dot_alias.status_code == 200, dot_alias.text
        assert dot_alias.json()["source"]["id"] == first_id

        listing = client.get("/api/watch").json()
        matching = [s for s in listing if s["id"] == first_id]
        assert len(matching) == 1


def test_watch_api_readd_same_path_is_rate_limited(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    root = tmp_path / "watched"
    root.mkdir()
    (root / "plain.txt").write_text("hello watch", encoding="utf-8")

    app_settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        max_upload_mb=30,
        max_total_mb=500,
        watch_dirs=[str(root)],
        watch_scan_seconds=3600,
    )
    app = create_app(app_settings)
    with TestClient(app) as client:
        first = client.post("/api/watch", json={"path": str(root)})
        assert first.status_code == 200, first.text
        assert first.json()["scan"]["added"] == 1
        assert first.json()["scan"].get("rate_limited") is not True

        second = client.post("/api/watch", json={"path": str(root)})
        assert second.status_code == 200
        assert second.json()["scan"]["rate_limited"] is True


def test_scan_skips_symlinked_files(conn, settings, tmp_path):
    pytest.importorskip("pylatexenc")
    root = tmp_path / "notes"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    (subject_dir / "calc.tex").write_text(_TEX_SOURCE, encoding="utf-8")
    secret = tmp_path / "secret_data.txt"
    secret.write_text("SECRET=value", encoding="utf-8")
    try:
        (subject_dir / "sneaky.txt").symlink_to(secret)
    except OSError:
        pytest.skip("symlinks not permitted in this environment")

    watch_settings = _watcher_settings(settings, root)
    source = _single_source(conn, watch_settings)
    summary = scan_once(conn, watch_settings, source["id"])
    assert summary["added"] == 1
    assert summary["errors"] == 0
    rows = conn.execute("SELECT source_path FROM files").fetchall()
    assert len(rows) == 1
    assert all("sneaky" not in r["source_path"] for r in rows)


def test_scan_does_not_follow_directory_symlinks(conn, settings, tmp_path):
    pytest.importorskip("pylatexenc")
    root = tmp_path / "notes"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    (subject_dir / "calc.tex").write_text(_TEX_SOURCE, encoding="utf-8")
    try:
        (root / "loop").symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted in this environment")

    watch_settings = _watcher_settings(settings, root)
    source = _single_source(conn, watch_settings)
    summary = scan_once(conn, watch_settings, source["id"])
    assert summary["added"] == 1
    assert summary["errors"] == 0


def test_scan_rejects_root_swapped_for_out_of_scope_symlink(conn, settings, tmp_path):
    pytest.importorskip("pylatexenc")
    root = tmp_path / "notes"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    (subject_dir / "calc.tex").write_text(_TEX_SOURCE, encoding="utf-8")

    watch_settings = _watcher_settings(settings, root)
    source = _single_source(conn, watch_settings)
    first = scan_once(conn, watch_settings, source["id"])
    assert first["added"] == 1
    assert first["errors"] == 0

    outside = tmp_path / "outside"
    leaked_dir = outside / "secret"
    leaked_dir.mkdir(parents=True)
    (leaked_dir / "leaked.tex").write_text(_TEX_OTHER, encoding="utf-8")

    shutil.rmtree(root)
    try:
        root.symlink_to(leaked_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted in this environment")

    summary = scan_once(conn, watch_settings, source["id"])
    assert summary["added"] == 0
    assert summary["errors"] == 1
    assert any(
        "symlink" in w or "outside the configured watch directories" in w
        for w in summary["warnings"]
    )

    rows = conn.execute("SELECT source_path FROM files").fetchall()
    assert len(rows) == 1
    assert all("leaked" not in r["source_path"] for r in rows)
    row = conn.execute(
        "SELECT last_error FROM watch_sources WHERE id = ?", (source["id"],)
    ).fetchone()
    assert row["last_error"] is not None


def test_scan_skips_oversized_file(conn, settings, tmp_path):
    root = tmp_path / "notes"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    (subject_dir / "big.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    small = settings.model_copy(update={"max_upload_mb": 1})
    watch_settings = _watcher_settings(small, root)
    source = _single_source(conn, watch_settings)
    summary = scan_once(conn, watch_settings, source["id"])
    assert summary["added"] == 0
    assert summary["errors"] == 0
    assert any("oversized" in w for w in summary["warnings"])
    assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 0


def test_scan_reingest_failure_keeps_old_row(conn, settings, tmp_path):
    pytest.importorskip("pylatexenc")
    root = tmp_path / "notes"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    tex_path = subject_dir / "calc.tex"
    tex_path.write_text(_TEX_SOURCE, encoding="utf-8")

    watch_settings = _watcher_settings(settings, root)
    source = _single_source(conn, watch_settings)
    scan_once(conn, watch_settings, source["id"])
    old_id = conn.execute("SELECT id FROM files").fetchone()["id"]
    old_digest = conn.execute("SELECT sha256 FROM files").fetchone()["sha256"]

    (root / "a-other.md").write_text(_TEX_CHANGED, encoding="utf-8")
    tex_path.write_text(_TEX_CHANGED, encoding="utf-8")

    summary = scan_once(conn, watch_settings, source["id"])
    assert summary["errors"] == 1
    assert summary["updated"] == 0
    assert any("ingest failed" in w for w in summary["warnings"])

    kept = conn.execute("SELECT * FROM files WHERE id = ?", (old_id,)).fetchone()
    assert kept is not None
    assert kept["sha256"] == old_digest
    assert kept["status"] == "ready"
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE file_id = ?", (old_id,)
        ).fetchone()["n"]
        > 0
    )
    source_row = conn.execute(
        "SELECT last_error FROM watch_sources WHERE id = ?", (source["id"],)
    ).fetchone()
    assert source_row["last_error"] is not None


def test_watch_root_with_underscore_matches_only_own_subtree(conn, settings, tmp_path):
    pytest.importorskip("pylatexenc")
    root_a = tmp_path / "notes_v2"
    root_b = tmp_path / "notes_v2_extra"
    sub_a = root_a / "calculus"
    sub_b = root_b / "algebra"
    sub_a.mkdir(parents=True)
    sub_b.mkdir(parents=True)
    (sub_a / "a.tex").write_text(_TEX_SOURCE, encoding="utf-8")
    (sub_b / "b.tex").write_text(_TEX_OTHER, encoding="utf-8")

    watch_a = _watcher_settings(settings, root_a)
    source_a = _single_source(conn, watch_a)
    scan_a = scan_once(conn, watch_a, source_a["id"])
    assert scan_a["added"] == 1
    assert scan_a["warnings"] == []

    watch_b = _watcher_settings(settings, root_b)
    sync_watch_sources(conn, watch_b)
    source_b = next(s for s in list_watch_sources(conn) if s["path"] == str(root_b))
    scan_b = scan_once(conn, watch_b, source_b["id"])
    assert scan_b["added"] == 1

    (sub_b / "b.tex").unlink()
    scan_a2 = scan_once(conn, watch_a, source_a["id"])
    assert not any("missing from disk" in w for w in scan_a2["warnings"])


def test_watcher_loop_runs_a_scan_then_stops(tmp_path):
    pytest.importorskip("pylatexenc")
    root = tmp_path / "watched"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    (subject_dir / "calc.tex").write_text(_TEX_SOURCE, encoding="utf-8")

    settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        watch_dirs=[str(root)],
        watch_scan_seconds=1,
    )

    async def run() -> None:
        init_db(settings.db_path)
        stop_event = asyncio.Event()
        task = asyncio.create_task(watcher_loop(settings, stop_event))
        await asyncio.sleep(1.6)
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(run())
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"] == 1
    finally:
        conn.close()


def _async_app(tmp_path, root, watch_scan_seconds=3600):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app_settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        watch_dirs=[str(root)],
        watch_scan_seconds=watch_scan_seconds,
    )
    app = create_app(app_settings)
    app.dependency_overrides[get_llm_client] = lambda: FakeLLM()
    return app


def test_watch_api_delete_readd_stays_rate_limited(tmp_path):
    from fastapi.testclient import TestClient

    root = tmp_path / "watched"
    root.mkdir()
    (root / "plain.txt").write_text("hello watch", encoding="utf-8")

    app = _async_app(tmp_path, root)
    with TestClient(app) as client:
        first = client.post("/api/watch", json={"path": str(root)})
        assert first.status_code == 200, first.text
        assert first.json()["scan"]["added"] == 1
        watch_id = first.json()["source"]["id"]

        deleted = client.delete(f"/api/watch/{watch_id}")
        assert deleted.status_code == 204

        readded = client.post("/api/watch", json={"path": str(root)})
        assert readded.status_code == 200, readded.text
        assert readded.json()["source"]["id"] == watch_id
        assert readded.json()["scan"]["rate_limited"] is True


@pytest.mark.asyncio
async def test_watch_scan_does_not_block_health(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    root = tmp_path / "watched"
    subject_dir = root / "calculus"
    subject_dir.mkdir(parents=True)
    (subject_dir / "calc.tex").write_text(_TEX_SOURCE, encoding="utf-8")

    real_scan = watcher.scan_once

    def slow_scan(conn, settings, watch_id):
        time.sleep(0.3)
        return real_scan(conn, settings, watch_id)

    monkeypatch.setattr(watcher, "scan_once", slow_scan)
    app = _async_app(tmp_path, root)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        scan_task = asyncio.create_task(
            client.post("/api/watch", json={"path": str(root)})
        )
        await asyncio.sleep(0.05)
        started = time.monotonic()
        health = await client.get("/api/health")
        elapsed = time.monotonic() - started
        scan = await scan_task
    assert health.status_code == 200
    assert elapsed < 0.2
    assert scan.status_code == 200
    assert scan.json()["scan"]["added"] == 1


@pytest.mark.asyncio
async def test_watch_overlapping_scans_serialize(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    root_a = tmp_path / "notes_a"
    root_b = tmp_path / "notes_b"
    sub_a = root_a / "calculus"
    sub_b = root_b / "algebra"
    sub_a.mkdir(parents=True)
    sub_b.mkdir(parents=True)
    (sub_a / "a.tex").write_text(_TEX_SOURCE, encoding="utf-8")
    (sub_b / "b.tex").write_text(_TEX_OTHER, encoding="utf-8")

    state = {"active": 0, "max_active": 0}
    real_scan = watcher.scan_once

    def tracking_scan(conn, settings, watch_id):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        try:
            time.sleep(0.1)
            return real_scan(conn, settings, watch_id)
        finally:
            state["active"] -= 1

    monkeypatch.setattr(watcher, "scan_once", tracking_scan)
    app_settings = Settings(
        data_dir=tmp_path / "data",
        api_key="test-key",
        api_url="http://127.0.0.1:9/v1",
        watch_dirs=[str(root_a), str(root_b)],
        watch_scan_seconds=3600,
    )
    from app.main import create_app

    app = create_app(app_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        results = await asyncio.gather(
            client.post("/api/watch", json={"path": str(root_a)}),
            client.post("/api/watch", json={"path": str(root_b)}),
        )
    assert all(r.status_code == 200 for r in results)
    assert state["max_active"] == 1
    assert state["active"] == 0


@pytest.mark.asyncio
async def test_watch_concurrent_double_post_no_double_scan(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    root = tmp_path / "watched"
    root.mkdir()
    (root / "plain.txt").write_text("hello watch", encoding="utf-8")

    scan_calls = 0
    real_scan = watcher.scan_once

    def counting_scan(conn, settings, watch_id):
        nonlocal scan_calls
        scan_calls += 1
        return real_scan(conn, settings, watch_id)

    monkeypatch.setattr(watcher, "scan_once", counting_scan)
    app = _async_app(tmp_path, root)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        results = await asyncio.gather(
            client.post("/api/watch", json={"path": str(root)}),
            client.post("/api/watch", json={"path": str(root)}),
        )
    assert all(r.status_code == 200 for r in results)
    assert scan_calls == 1
    scans = [r.json()["scan"] for r in results]
    assert sum(1 for s in scans if s.get("rate_limited") is True) == 1
    assert sum(1 for s in scans if "rate_limited" not in s) == 1
