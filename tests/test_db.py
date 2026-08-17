"""Database schema: idempotency, WAL, cascade deletes, schema version."""

from __future__ import annotations

import sqlite3

from app.db import SCHEMA_VERSION, init_db

TABLES = {
    "files", "chunks", "sessions", "messages", "quiz_questions",
    "plan_nodes", "mastery_topics", "preferences", "schema_version",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_all_tables(settings):
    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    try:
        assert TABLES <= _table_names(conn)
    finally:
        conn.close()


def test_init_is_idempotent(settings):
    init_db(settings.db_path)
    init_db(settings.db_path)  # second run must not raise
    conn = sqlite3.connect(str(settings.db_path))
    try:
        assert TABLES <= _table_names(conn)
    finally:
        conn.close()


def test_wal_journal_mode(settings):
    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
    finally:
        conn.close()


def test_schema_version(settings):
    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_preferences_seeded(settings):
    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    try:
        row = conn.execute("SELECT id, depth FROM preferences WHERE id=1").fetchone()
        assert row is not None
        assert row["depth"] == "standard"
    finally:
        conn.close()


def test_file_delete_cascades_chunks(conn):
    conn.execute(
        """
        INSERT INTO files (id, display_name, storage_name, size_bytes, sha256,
                           created_at, updated_at)
        VALUES ('f1', 'n.md', 'f1.md', 10, 'x'*64, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO chunks (id, file_id, chunk_index, text)
        VALUES ('f1:0:0', 'f1', 0, 'hello')
        """
    )
    conn.execute("DELETE FROM files WHERE id='f1'")
    remaining = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    assert remaining == 0


def test_foreign_keys_enforced(conn):
    with pytest_raises_foreign_key():
        conn.execute(
            "INSERT INTO chunks (id, file_id, chunk_index, text) VALUES ('c1', 'ghost', 0, 'x')"
        )


def pytest_raises_foreign_key():
    import pytest

    return pytest.raises(sqlite3.IntegrityError)


def test_unique_file_chunk_index(conn):
    conn.execute(
        """
        INSERT INTO files (id, display_name, storage_name, size_bytes, sha256,
                           created_at, updated_at)
        VALUES ('f1', 'n.md', 'f1.md', 10, 'y'*64, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.execute("INSERT INTO chunks (id, file_id, chunk_index, text) VALUES ('c1', 'f1', 0, 'a')")
    with pytest_raises_foreign_key():
        conn.execute("INSERT INTO chunks (id, file_id, chunk_index, text) VALUES ('c2', 'f1', 0, 'b')")


def test_messages_duplicate_physical_id_raises(conn):
    conn.execute(
        """
        INSERT INTO sessions (id, goal, created_at, updated_at)
        VALUES ('s', 'goal', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content) VALUES ('m', 's', 'user', 'x')"
    )
    with pytest_raises_foreign_key():
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content) VALUES ('m', 's', 'user', 'x')"
        )
