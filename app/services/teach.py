from __future__ import annotations

import sqlite3

from app.config import Settings
from app.errors import ModelOutputError, NotFoundError
from app.llm.messages import make_system_prompt
from app.llm.structured import provider_error_from_text
from app.services.sessions import SessionService, plan_node_dict, question_dict
from app.util import new_id, utc_now

CHECK_EVERY_NODES = 2


# Escalating reveal ladder: each rung reveals more, keeping a stuck learner in
# the Socratic loop instead of rage-quitting (a known RAG-tutor failure mode).
LADDER_LEVELS = ("hint", "worked_example", "walkthrough")

# Ladder prompt instructions keyed by level (plain-text output, no JSON).
_LADDER_INSTRUCTIONS = {
    "hint": (
        "Give ONLY a short hint (1-2 sentences) that helps the learner arrive "
        "at the correct answer. Do not reveal the answer directly and do not "
        "return JSON."
    ),
    "worked_example": (
        "Give a concise worked example: 2-4 numbered, concrete steps that walk "
        "through how to reason toward and obtain the correct answer, WITHOUT "
        "flatly stating the answer's letter. Do not return JSON."
    ),
    "walkthrough": (
        "Give a complete, encouraging step-by-step walkthrough that fully "
        "solves the question: clearly state the correct answer, then explain "
        "it step by step, addressing any common misconception. Do not return JSON."
    ),
}


class TeachService:
    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.settings = settings
        self.sessions = SessionService(conn, settings)

    def advance(self, session_id: str, reset_check: bool = False) -> dict:
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
        session = self.sessions.get(session_id)
        if session.phase != "remediate":
            raise ValueError(f"continue requires phase 'remediate', got {session.phase!r}")
        self.sessions.set_phase(session_id, "teach")
        return self.advance(session_id, reset_check=True)

    def complete(self, session_id: str) -> dict:
        self.sessions.get(session_id)
        self.sessions.set_phase(session_id, "complete")
        return {"session": self.sessions.get(session_id).model_dump()}

    async def hint(self, session_id: str, question_id: str, llm) -> dict:
        text = await self._ladder_step(session_id, question_id, llm, "hint")
        return {"hint": text}

    async def worked_example(self, session_id: str, question_id: str, llm) -> dict:
        text = await self._ladder_step(session_id, question_id, llm, "worked_example")
        return {"worked_example": text}

    async def walkthrough(self, session_id: str, question_id: str, llm) -> dict:
        text = await self._ladder_step(session_id, question_id, llm, "walkthrough")
        return {"walkthrough": text}

    async def _ladder_step(
        self, session_id: str, question_id: str, llm, level: str
    ) -> str:
        """Generate (and cache) one rung of the reveal ladder for a question."""
        if level not in _LADDER_INSTRUCTIONS:
            raise ValueError(f"unknown ladder level {level!r}")
        cached = self.conn.execute(
            "SELECT content FROM feedback_actions "
            "WHERE session_id = ? AND question_id = ? AND action = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id, question_id, level),
        ).fetchone()
        if cached and cached["content"]:
            return cached["content"]

        session = self.sessions.get(session_id)
        question = dict(self._fetch_question(session_id, question_id))
        if question["kind"] not in ("probe", "check"):
            raise ValueError(
                f"question kind {question['kind']!r} cannot receive a ladder step"
            )
        if question["status"] != "answered":
            raise ValueError("a ladder step requires the question to be answered first")
        options = question_dict(question)["options"]
        chosen = question["user_choice"]
        chosen_text = (
            "I don't know"
            if chosen is None or chosen < 0
            else options[chosen] if 0 <= chosen < len(options) else str(chosen)
        )
        user = (
            f"{_LADDER_INSTRUCTIONS[level]}\n\n"
            f"Question: {question['question']}\n"
            f"Options: {options}\n"
            f"The learner chose: {chosen_text}\n"
            f"The correct answer is option index {question['correct_index']}."
        )
        messages = [
            {
                "role": "system",
                "content": make_system_prompt(
                    session.model_dump(),
                    session.grounding_mode,
                    self.sessions._mastery_summary(session_id),
                ),
            },
            {"role": "user", "content": user},
        ]
        text, error_text = await llm.complete_json(messages)
        if text is None:
            raise provider_error_from_text(error_text)
        step_text = text.strip()
        if not step_text:
            error = ModelOutputError(f"The model returned no {level}.")
            error.retryable = True
            raise error
        now = utc_now()
        self.conn.execute(
            "INSERT INTO feedback_actions (id, session_id, question_id, action, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), session_id, question_id, level, step_text, now),
        )
        self.conn.commit()
        return step_text

    def ladder(self, session_id: str, question_id: str) -> dict:
        """Report the reveal ladder for a question: ordered rungs + cached content."""
        self.sessions.get(session_id)
        row = dict(self._fetch_question(session_id, question_id))
        if row["kind"] not in ("probe", "check"):
            raise ValueError(
                f"question kind {row['kind']!r} cannot have a reveal ladder"
            )
        cached = {
            r["action"]: r["content"]
            for r in self.conn.execute(
                "SELECT action, content FROM feedback_actions "
                "WHERE session_id = ? AND question_id = ? AND action IN (?, ?, ?)",
                (session_id, question_id, *LADDER_LEVELS),
            )
        }
        rungs = []
        for level in LADDER_LEVELS:
            rungs.append(
                {"level": level, "generated": level in cached, "content": cached.get(level)}
            )
        # Reveal is the static final rung (answer + explanation), always available
        # once answered.
        reveal_ready = row["status"] == "answered"
        if reveal_ready:
            options = question_dict(row)["options"]
            correct_index = row["correct_index"]
            rungs.append(
                {
                    "level": "reveal",
                    "generated": True,
                    "content": options[correct_index]
                    if 0 <= correct_index < len(options)
                    else None,
                }
            )
        return {
            "question_id": question_id,
            "answered": row["status"] == "answered",
            "ladder": rungs,
        }

    def reveal(self, session_id: str, question_id: str) -> dict:
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
