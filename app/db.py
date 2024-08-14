from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import Request

SCHEMA_VERSION = 2

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
    paired_file_id TEXT,
    subject       TEXT,
    source_path   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_files_sha256 ON files (sha256);

CREATE TABLE IF NOT EXISTS chunks (
    id            TEXT PRIMARY KEY,
    file_id       TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    text          TEXT NOT NULL,
    unicode_text  TEXT,
    environment   TEXT,
    label         TEXT,
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
    source_ref     TEXT,
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

CREATE TABLE IF NOT EXISTS review_cards (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    topic           TEXT NOT NULL,
    question_id     TEXT,
    kind            TEXT NOT NULL DEFAULT 'check',
    state           INTEGER NOT NULL DEFAULT 1,
    stability       REAL,
    difficulty      REAL,
    due             TEXT NOT NULL,
    last_review     TEXT,
    reps            INTEGER NOT NULL DEFAULT 0,
    lapses          INTEGER NOT NULL DEFAULT 0,
    card_json       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_session ON review_cards (session_id);
CREATE INDEX IF NOT EXISTS idx_review_due ON review_cards (session_id, due);

CREATE TABLE IF NOT EXISTS preferences (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    depth      TEXT NOT NULL DEFAULT 'standard',
    pacing     TEXT NOT NULL DEFAULT 'normal',
    style      TEXT NOT NULL DEFAULT 'analogy-first',
    notes      TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_sources (
    id            TEXT PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_scan_at  TEXT,
    last_error    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""
# fmt: on

_V2_COLUMNS = (
    ("files", "paired_file_id", "paired_file_id TEXT"),
    ("files", "subject", "subject TEXT"),
    ("files", "source_path", "source_path TEXT"),
    ("chunks", "unicode_text", "unicode_text TEXT"),
    ("chunks", "environment", "environment TEXT"),
    ("chunks", "label", "label TEXT"),
    ("quiz_questions", "source_ref", "source_ref TEXT"),
)


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db(db_path: Path) -> None:
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
        for table, name, ddl in _V2_COLUMNS:
            _ensure_column(conn, table, name, ddl)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_subject ON files (subject)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_source_path ON files (source_path)"
        )
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
        )
        conn.execute(
            "INSERT OR IGNORE INTO preferences (id, updated_at) VALUES (1, '')"
        )
        conn.commit()
    finally:
        conn.close()


def open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = open_db(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
