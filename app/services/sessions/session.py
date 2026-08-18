from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.config import Settings
from app.db import row_to_dict, rows_to_dicts
from app.errors import NotFoundError
from app.models import Message, Session
from app.services.files import FileService
from app.services.mastery import summarize_mastery
from app.services.retrieval import Retriever
from app.services.sessions.rows import plan_node_dict, question_dict
from app.services.sessions.turn import TurnMixin
from app.util import new_id, utc_now

PHASES = ("setup", "probe", "plan", "teach", "check", "remediate", "complete")

GROUNDING_MODES: set[str] = {"strict", "grounded"}


class SessionService(TurnMixin):
    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.settings = settings
        self.files = FileService(conn, settings)
        self.retriever = Retriever()

    def create(
        self,
        goal: str,
        file_ids: list[str],
        grounding_mode: str = "grounded",
    ) -> Session:
        if grounding_mode not in GROUNDING_MODES:
            raise ValueError(f"grounding_mode must be one of {GROUNDING_MODES}")
        session_id = new_id()
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO sessions (id, title, goal, phase, grounding_mode,
                                  file_ids_json, created_at, updated_at)
            VALUES (?, ?, ?, 'setup', ?, ?, ?, ?)
            """,
            (
                session_id,
                goal[:80],
                goal,
                grounding_mode,
                json.dumps(file_ids),
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get(session_id)

    def get(self, session_id: str) -> Session:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        session = row_to_dict(row)
        if session is None:
            raise NotFoundError("session", session_id)
        return self._to_session(session)

    def list(self) -> list[Session]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [self._to_session(dict(r)) for r in rows]

    def load_full(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)

        messages = rows_to_dicts(
            self.conn.execute(
                "SELECT * FROM messages WHERE session_id = ? "
                "AND partial = 0 ORDER BY created_at",
                (session_id,),
            )
        )
        plan = [
            plan_node_dict(row)
            for row in rows_to_dicts(
                self.conn.execute(
                    "SELECT * FROM plan_nodes WHERE session_id = ? ORDER BY position, rowid",
                    (session_id,),
                )
            )
        ]
        quiz = [
            question_dict(row)
            for row in rows_to_dicts(
                self.conn.execute(
                    "SELECT * FROM quiz_questions WHERE session_id = ? "
                    "ORDER BY created_at, rowid",
                    (session_id,),
                )
            )
        ]
        mastery = rows_to_dicts(
            self.conn.execute("SELECT * FROM mastery_topics ORDER BY label")
        )
        preferences = (
            row_to_dict(
                self.conn.execute(
                    "SELECT depth, pacing, style, notes, updated_at FROM preferences WHERE id = 1"
                ).fetchone()
            )
            or {}
        )
        selected_files = []
        for file_id in session.file_ids:
            try:
                record = self.files.get(file_id)
                selected_files.append(record.model_dump())
            except NotFoundError:
                selected_files.append(
                    {"id": file_id, "status": "missing", "display_name": "(deleted)"}
                )

        return {
            "session": session.model_dump(),
            "messages": [self._message_dict(m) for m in messages],
            "plan": plan,
            "quiz": quiz,
            "mastery": mastery,
            "preferences": preferences,
            "selected_files": selected_files,
        }

    def delete(self, session_id: str) -> None:
        self.get(session_id)
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.conn.commit()

    def set_phase(self, session_id: str, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(f"unknown phase: {phase!r}")
        now = utc_now()
        self.conn.execute(
            "UPDATE sessions SET phase = ?, updated_at = ? WHERE id = ?",
            (phase, now, session_id),
        )
        self.conn.commit()

    def select_files(self, session_id: str, file_ids: list[str]) -> Session:
        self.get(session_id)
        now = utc_now()
        self.conn.execute(
            "UPDATE sessions SET file_ids_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(file_ids), now, session_id),
        )
        self.conn.commit()
        return self.get(session_id)

    def update_grounding(self, session_id: str, mode: str) -> Session:
        if mode not in GROUNDING_MODES:
            raise ValueError(f"grounding_mode must be one of {GROUNDING_MODES}")
        self.get(session_id)
        now = utc_now()
        self.conn.execute(
            "UPDATE sessions SET grounding_mode = ?, updated_at = ? WHERE id = ?",
            (mode, now, session_id),
        )
        self.conn.commit()
        return self.get(session_id)

    def _to_session(self, row: dict) -> Session:
        try:
            file_ids = json.loads(row.get("file_ids_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            file_ids = []
        return Session(
            id=row["id"],
            title=row.get("title"),
            goal=row["goal"],
            phase=row.get("phase", "setup"),
            grounding_mode=row.get("grounding_mode", "grounded"),
            current_node_id=row.get("current_node_id"),
            nodes_since_check=row.get("nodes_since_check", 0),
            file_ids=file_ids,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _message_dict(row: dict) -> Message:
        try:
            citations = json.loads(row.get("citations_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            citations = []
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            client_msg_id=row.get("client_msg_id"),
            role=row["role"],
            kind=row.get("kind", "text"),
            content=row["content"],
            citations=citations,
            partial=row.get("partial", 0),
            created_at=row["created_at"],
        )

    def _select_chunks(self, session: Session, user_text: str) -> list[dict]:
        ready_ids = self.files.get_ready_file_ids(session.file_ids)
        all_chunks = self.files.get_chunks_for_files(ready_ids)
        query = f"{session.goal}\n{user_text}"
        return self.retriever.select(
            all_chunks,
            query,
            budget=self.settings.context_chunk_budget,
            per_file_cap=3,
        )

    def _mastery_summary(self) -> str:
        return summarize_mastery(self.conn)

    def save_partial_marker(
        self, session_id: str, client_msg_id: str
    ) -> tuple[dict | None, bool]:
        existing = self.conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND client_msg_id = ?",
            (session_id, client_msg_id),
        ).fetchone()
        if existing is not None:
            return row_to_dict(existing), False

        message_id = new_id()
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO messages (id, session_id, client_msg_id, role, kind,
                                  content, citations_json, partial, created_at)
            VALUES (?, ?, ?, 'assistant', 'text', '', '[]', 1, ?)
            """,
            (message_id, session_id, client_msg_id, now),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return row_to_dict(row), True

    def persist_message(
        self,
        session_id: str,
        client_msg_id: str,
        content: str,
        citations: list[str],
    ) -> Message:
        now = utc_now()
        self.conn.execute(
            """
            UPDATE messages
            SET content = ?, citations_json = ?, partial = 0, created_at = ?
            WHERE session_id = ? AND client_msg_id = ?
            """,
            (content, json.dumps(citations), now, session_id, client_msg_id),
        )
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND client_msg_id = ?",
            (session_id, client_msg_id),
        ).fetchone()
        return self._message_dict(row_to_dict(row) or {})

    def find_message(self, session_id: str, client_msg_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND client_msg_id = ?",
            (session_id, client_msg_id),
        ).fetchone()
        return row_to_dict(row)
