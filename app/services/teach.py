from __future__ import annotations

import sqlite3

from app.config import Settings
from app.errors import ModelOutputError, NotFoundError
from app.llm.messages import make_system_prompt
from app.llm.structured import provider_error_from_text
from app.services.sessions import SessionService, plan_node_dict, question_dict
from app.util import new_id, utc_now

CHECK_EVERY_NODES = 2


class TeachService:
    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.settings = settings
        self.sessions = SessionService(conn, settings)

    def advance(self, session_id: str, reset_check: bool = False) -> dict:
        """Mark the current node done and move to the next pending node.

        Requires the teach phase. ``reset_check`` zeroes the check cadence
        counter (used after a check or remediation); otherwise it is
        incremented. Returns session, the new current node (or None when the
        plan is finished), and whether a check is now due.
        """
        session = self.sessions.get(session_id)
        if session.phase != "teach":
            raise ValueError(f"advance requires phase 'teach', got {session.phase!r}")
        now = utc_now()
        if session.current_node_id:
            self.conn.execute(
                "UPDATE plan_nodes SET status = 'done' WHERE id = ? AND session_id = ? "
                "AND status IN ('pending', 'current')",
                (session.current_node_id, session_id),
            )
        next_row = self.conn.execute(
            "SELECT * FROM plan_nodes WHERE session_id = ? AND status = 'pending' "
            "ORDER BY position, rowid LIMIT 1",
            (session_id,),
        ).fetchone()
        nodes_since_check = 0 if reset_check else session.nodes_since_check + 1
        if next_row is None:
            self.conn.execute(
                "UPDATE sessions SET phase = 'complete', current_node_id = NULL, "
                "nodes_since_check = ?, updated_at = ? WHERE id = ?",
                (nodes_since_check, now, session_id),
            )
            self.conn.commit()
            return {
                "session": self.sessions.get(session_id).model_dump(),
                "node": None,
                "check_due": False,
            }
        node = dict(next_row)
        self.conn.execute(
            "UPDATE plan_nodes SET status = 'current' WHERE id = ? AND session_id = ?",
            (node["id"], session_id),
        )
        self.conn.execute(
            "UPDATE sessions SET current_node_id = ?, nodes_since_check = ?, "
            "updated_at = ? WHERE id = ?",
            (node["id"], nodes_since_check, now, session_id),
        )
        self.conn.commit()
        node["status"] = "current"
        updated = self.sessions.get(session_id)
        return {
            "session": updated.model_dump(),
            "node": plan_node_dict(node),
            "check_due": updated.phase == "teach" and updated.nodes_since_check >= CHECK_EVERY_NODES,
        }

    def continue_after_remediate(self, session_id: str) -> dict:
        """Leave remediation and resume teaching (resets the check counter)."""
        session = self.sessions.get(session_id)
        if session.phase != "remediate":
            raise ValueError(f"continue requires phase 'remediate', got {session.phase!r}")
        self.sessions.set_phase(session_id, "teach")
        return self.advance(session_id, reset_check=True)

    def complete(self, session_id: str) -> dict:
        """Manually end the session."""
        self.sessions.get(session_id)
        self.sessions.set_phase(session_id, "complete")
        return {"session": self.sessions.get(session_id).model_dump()}

    async def hint(self, session_id: str, question_id: str, llm) -> dict:
        """Ask the model for a short hint for an answered question."""
        session = self.sessions.get(session_id)
        row = self._fetch_question(session_id, question_id)
        question = dict(row)
        if question["kind"] not in ("probe", "check"):
            raise ValueError(f"question kind {question['kind']!r} cannot receive a hint")
        if question["status"] != "answered":
            raise ValueError("a hint requires the question to be answered first")
        options = question_dict(question)["options"]
        chosen = question["user_choice"]
        chosen_text = "I don't know" if chosen is None or chosen < 0 else options[chosen] if 0 <= chosen < len(options) else str(chosen)
        user = (
            "Give ONLY a short hint (1-2 sentences) that helps the learner arrive "
            "at the correct answer. Do not reveal the answer directly and do not "
            "return JSON.\n\n"
            f"Question: {question['question']}\n"
            f"Options: {options}\n"
            f"The learner chose: {chosen_text}\n"
            f"The correct answer is option index {question['correct_index']}."
        )
        messages = [
            {
                "role": "system",
                "content": make_system_prompt(
                    session.model_dump(), session.grounding_mode, self.sessions._mastery_summary()
                ),
            },
            {"role": "user", "content": user},
        ]
        text, error_text = await llm.complete_json(messages)
        if text is None:
            raise provider_error_from_text(error_text)
        hint_text = text.strip()
        if not hint_text:
            error = ModelOutputError("The model returned no hint.")
            error.retryable = True
            raise error
        now = utc_now()
        self.conn.execute(
            "INSERT INTO feedback_actions (id, session_id, question_id, action, content, created_at) "
            "VALUES (?, ?, ?, 'hint', ?, ?)",
            (new_id(), session_id, question_id, hint_text, now),
        )
        self.conn.commit()
        return {"hint": hint_text}

    def reveal(self, session_id: str, question_id: str) -> dict:
        """Return the correct option and explanation for an answered question."""
        self.sessions.get(session_id)
        row = self._fetch_question(session_id, question_id)
        question = dict(row)
        if question["status"] != "answered":
            raise ValueError("reveal requires the question to be answered first")
        options = question_dict(question)["options"]
        correct_index = question["correct_index"]
        correct_option = options[correct_index] if 0 <= correct_index < len(options) else None
        now = utc_now()
        self.conn.execute(
            "INSERT INTO feedback_actions (id, session_id, question_id, action, content, created_at) "
            "VALUES (?, ?, ?, 'reveal', ?, ?)",
            (new_id(), session_id, question_id, correct_option, now),
        )
        self.conn.commit()
        return {
            "question_id": question_id,
            "correct_index": correct_index,
            "correct_option": correct_option,
            "explanation": question["explanation"],
        }

    def skip_quiz(self, session_id: str, question_id: str) -> dict:
        """Skip a check question and return to the plan phase."""
        self.sessions.get(session_id)
        row = self._fetch_question(session_id, question_id)
        question = dict(row)
        if question["kind"] != "check":
            raise ValueError("only check questions can be skipped")
        now = utc_now()
        self.conn.execute(
            "UPDATE quiz_questions SET status = 'skipped' WHERE id = ?", (question_id,)
        )
        self.conn.execute(
            "UPDATE sessions SET phase = 'plan', nodes_since_check = 0, updated_at = ? "
            "WHERE id = ?",
            (now, session_id),
        )
        self.conn.commit()
        return {"session": self.sessions.get(session_id).model_dump()}

    def _fetch_question(self, session_id: str, question_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM quiz_questions WHERE id = ? AND session_id = ?",
            (question_id, session_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("question", question_id)
        return dict(row)
