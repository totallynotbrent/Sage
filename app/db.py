"""SQLite persistence: idempotent schema, connection management, helpers.

A single SQLite database lives at ``DATA_DIR/sage.db`` in WAL mode. One
connection is created per request (via the ``get_conn`` dependency) and closed
when the request finishes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import Request

SCHEMA_VERSION = 1

# fmt: off
_DDL = """
CREATE TABLE IF NOT EXISTS files (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    storage_name  TEXT NOT NULL,
    mime_type     TEXT,
    size_bytes    INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    warnings      TEXT NOT NULL DEFAULT '[]',
    error         TEXT,
    num_chunks    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_files_sha256 ON files (sha256);

CREATE TABLE IF NOT EXISTS chunks (
    id            TEXT PRIMARY KEY,
    file_id       TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    text          TEXT NOT NULL,
    location_kind TEXT,
    page          INTEGER,
    slide         INTEGER,
    section       TEXT,
    start_line    INTEGER,
    end_line      INTEGER,
    char_start    INTEGER,
    char_end      INTEGER,
    UNIQUE (file_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks (file_id);

CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    title             TEXT,
    goal              TEXT NOT NULL,
    phase             TEXT NOT NULL DEFAULT 'setup',
    grounding_mode    TEXT NOT NULL DEFAULT 'grounded',
    current_node_id   TEXT,
    nodes_since_check INTEGER NOT NULL DEFAULT 0,
    plan_json         TEXT,
    file_ids_json     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    client_msg_id  TEXT,
    role           TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'text',
    content        TEXT NOT NULL,
    payload_json   TEXT,
    citations_json TEXT NOT NULL DEFAULT '[]',
    partial        INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, created_at);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    topic          TEXT,
    difficulty     INTEGER,
    question       TEXT NOT NULL,
    options_json   TEXT NOT NULL,
    correct_index  INTEGER NOT NULL,
    explanation    TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    user_choice    INTEGER,
    outcome        TEXT,
    created_at     TEXT NOT NULL,
    answered_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_quiz_session ON quiz_questions (session_id);

CREATE TABLE IF NOT EXISTS plan_nodes (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    node_key       TEXT NOT NULL,
    title          TEXT NOT NULL,
    description    TEXT,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'pending',
    position       INTEGER NOT NULL,
    children_json  TEXT,
    UNIQUE (session_id, node_key)
);
CREATE INDEX IF NOT EXISTS idx_plan_session ON plan_nodes (session_id);

CREATE TABLE IF NOT EXISTS feedback_actions (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    action      TEXT NOT NULL,
    content     TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback_actions (session_id);

CREATE TABLE IF NOT EXISTS mastery_topics (
    topic            TEXT PRIMARY KEY,
    label            TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0,
    observed_count   INTEGER NOT NULL DEFAULT 0,
    correct_count    INTEGER NOT NULL DEFAULT 0,
    idk_count        INTEGER NOT NULL DEFAULT 0,
    last_assessed_at TEXT,
    evidence_json    TEXT NOT NULL DEFAULT '[]',
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS preferences (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    depth      TEXT NOT NULL DEFAULT 'standard',
    pacing     TEXT NOT NULL DEFAULT 'normal',
    style      TEXT NOT NULL DEFAULT 'analogy-first',
    notes      TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""
# fmt: on


def init_db(db_path: Path) -> None:
    """Create the schema and set persistent pragmas. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_DDL)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "nodes_since_check" not in cols:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN nodes_since_check INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.execute(
            "INSERT OR IGNORE INTO preferences (id, updated_at) VALUES (1, '')"
        )
        conn.commit()
    finally:
        conn.close()


def open_db(db_path: Path) -> sqlite3.Connection:
    """Open a configured connection (WAL, FK enforcement, busy timeout).

    Callers own the returned connection and must close it.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, closed on completion.

    ``check_same_thread=False`` allows the connection to be used by the request's
    threadpool thread (sync dependency) and the event loop (async streaming
    generators); each connection belongs to exactly one request, so there is no
    concurrent use.

    Note: for SSE streaming routes the connection is opened by the route itself
    (see ``app/api/chat.py``) because yield-dependencies are torn down before a
    StreamingResponse body is sent.
    """
    conn = open_db(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict]:
    """Convert a cursor's rows to a list of plain dicts."""
    return [dict(row) for row in cursor.fetchall()]


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convert a single row to a dict, or None."""
    return dict(row) if row is not None else None
