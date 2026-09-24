from __future__ import annotations

import sqlite3

import pytest

from app.db import SCHEMA_VERSION, init_db

TABLES = {
    "files",
    "chunks",
    "sessions",
    "messages",
    "quiz_questions",
    "plan_nodes",
    "feedback_actions",
    "mastery_topics",
    "preferences",
    "watch_sources",
    "schema_version",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_schema_creates_all_tables(settings):
    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        assert TABLES <= _table_names(conn)
    finally:
        conn.close()


def test_init_is_idempotent(settings):
    init_db(settings.db_path)
    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
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




def test_init_migrates_nodes_since_check(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.db_path))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, goal TEXT NOT NULL)")
    conn.commit()
    conn.close()
    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert "nodes_since_check" in cols
    finally:
        conn.close()


def test_init_migrates_v1_db_to_v2(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.db_path))
    conn.executescript(
        """
        CREATE TABLE files (
            id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
            storage_name TEXT NOT NULL, mime_type TEXT, size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            warnings TEXT NOT NULL DEFAULT '[]', error TEXT,
            num_chunks INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE chunks (
            id TEXT PRIMARY KEY, file_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL, location_kind TEXT, page INTEGER, slide INTEGER,
            section TEXT, start_line INTEGER, end_line INTEGER,
            char_start INTEGER, char_end INTEGER, UNIQUE (file_id, chunk_index)
        );
        CREATE TABLE quiz_questions (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL,
            topic TEXT, difficulty INTEGER, question TEXT NOT NULL,
            options_json TEXT NOT NULL, correct_index INTEGER NOT NULL,
            explanation TEXT, status TEXT NOT NULL DEFAULT 'pending',
            user_choice INTEGER, outcome TEXT, created_at TEXT NOT NULL,
            answered_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    init_db(settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        assert version == 5
        files_cols = {r["name"] for r in conn.execute("PRAGMA table_info(files)")}
        for name in ("paired_file_id", "subject", "source_path"):
            assert name in files_cols
        chunks_cols = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
        for name in ("unicode_text", "environment", "label"):
            assert name in chunks_cols
        quiz_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(quiz_questions)")
        }
        assert "source_ref" in quiz_cols
        assert "confidence" in quiz_cols
        mastery_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(mastery_topics)")
        }
        assert "overconfident_count" in mastery_cols
        assert "underconfident_count" in mastery_cols
        watch = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watch_sources'"
        ).fetchone()
        assert watch is not None
        indexes = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_files_subject" in indexes
        assert "idx_files_source_path" in indexes
    finally:
        conn.close()





def raises_foreign_key():
    return pytest.raises(sqlite3.IntegrityError)


def test_unique_file_chunk_index(conn):
    conn.execute(
        """
        INSERT INTO files (id, display_name, storage_name, size_bytes, sha256,
                           created_at, updated_at)
        VALUES ('f1', 'n.md', 'f1.md', 10, 'y'*64, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.execute(
        "INSERT INTO chunks (id, file_id, chunk_index, text) VALUES ('c1', 'f1', 0, 'a')"
    )
    with raises_foreign_key():
        conn.execute(
            "INSERT INTO chunks (id, file_id, chunk_index, text) VALUES ('c2', 'f1', 0, 'b')"
        )


def test_messages_duplicate_physical_id_raises(conn):
    conn.execute(
        """
        INSERT INTO sessions (id, goal, created_at, updated_at)
        VALUES ('s', 'goal', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at) VALUES ('m', 's', 'user', 'x', '2026-01-01T00:00:00Z')"
    )
    with raises_foreign_key():
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at) VALUES ('m', 's', 'user', 'x', '2026-01-01T00:00:00Z')"
        )
