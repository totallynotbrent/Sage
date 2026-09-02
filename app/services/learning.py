from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from app.config import Settings
from app.db import rows_to_dicts
from app.errors import ModelOutputError, NotFoundError
from app.llm.notes import request_notes_questions
from app.llm.structured import request_learner_review, request_questions
from app.models import QuizQuestionInput, Session
from app.services import mastery
from app.services.sessions import SessionService, question_dict
from app.services.teach import TeachService
from app.util import new_id, utc_now

IDK_OPTION = "I don't know"


def _timestamp_gap_ms(created_at: str | None, answered_at: str | None) -> int | None:
    """Server-side latency fallback: the created_at→answered_at gap.

    Used when no UI latency came through (e.g. latency badge dropped in the
    model relay), so retrieval effort still reaches mastery scheduling.
    """
    if not created_at or not answered_at:
        return None
    try:
        start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(answered_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int(round((end - start).total_seconds() * 1000)))


class LearningService:
    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.settings = settings
        self.sessions = SessionService(conn, settings)
        self.teach = TeachService(conn, settings)

    def _adaptive_question_count(
        self, session_id: str, topic: str, *, probe: bool = False
    ) -> int:
        """Pick how many questions to ask from recent performance.

        Acing (last 2 outcomes correct on this topic with decent confidence) keeps
        checks light (1) and probes diagnostic (2). Stalling (a recent miss or low
        confidence) drills harder with up to 3 mixed questions.
        """
        recent = self.conn.execute(
            "SELECT outcome, latency_ms FROM quiz_questions "
            "WHERE session_id = ? AND kind IN ('probe','check') AND outcome IS NOT NULL "
            "AND answered_at IS NOT NULL ORDER BY answered_at DESC, rowid DESC LIMIT 2",
            (session_id,),
        ).fetchall()
        outcomes = [r["outcome"] for r in recent]
        confidence = mastery.topic_confidence(self.conn, topic)
        miss = any(o in ("incorrect", "idk") for o in outcomes)
        acing = (
            len(outcomes) == 2
            and not miss
            and confidence is not None
            and confidence >= 0.6
            and all(
                not mastery.is_slow_answer(r["latency_ms"]) for r in recent
            )
        )
        stalling = miss or (confidence is not None and confidence < 0.35)
        if stalling:
            return 3
        if acing:
            return 1
        return 2

    async def generate_probe(self, session_id: str, llm) -> dict:
        session = self.sessions.get(session_id)
        existing = self._pending_questions(session_id, "probe")
        if existing:
            return {"session": session.model_dump(), "questions": existing}
        self._delete_pending_questions(session_id, "probe")
        chunks = self.sessions._select_chunks(session, "adaptive probe questions")
        count = self._adaptive_question_count(session_id, session.goal, probe=True)
        questions = await request_questions(
            llm,
            session=session.model_dump(),
            chunks=chunks,
            mastery_summary=self.sessions._mastery_summary(),
            mode=session.grounding_mode,
            count=count,
            focus=session.goal,
        )
        if not questions:
            error = ModelOutputError("The model returned no usable probe questions.")
            error.retryable = True
            raise error
        for question in questions:
            self._insert_question(session_id, "probe", question)
        self.sessions.set_phase(session_id, "probe")
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "questions": self._pending_questions(session_id, "probe"),
        }

    async def generate_check(self, session_id: str, llm) -> dict:
        session = self.sessions.get(session_id)
        existing = self._pending_questions(session_id, "check")
        if existing:
            return {"session": session.model_dump(), "questions": existing}
        self._delete_pending_questions(session_id, "check")
        topic = self._current_node_title(session) or session.goal
        # Interleaved practice: roughly every 3rd check, pull a prior weak topic
        # instead of the current node so retrieval is mixed (beats blocking).
        interleaved = None
        prior_checks = self.conn.execute(
            "SELECT COUNT(*) AS n FROM quiz_questions "
            "WHERE session_id = ? AND kind = 'check' AND status = 'answered'",
            (session_id,),
        ).fetchone()["n"]
        if prior_checks > 0 and prior_checks % 3 == 0:
            weak = mastery.lowest_confidence_topics(
                self.conn, exclude={mastery.normalize_topic(topic)}, limit=1
            )
            if weak:
                interleaved = weak[0]["label"]
        focus_topic = interleaved or topic
        chunks = self.sessions._select_chunks(session, "check question")
        count = self._adaptive_question_count(session_id, focus_topic)
        questions = await request_questions(
            llm,
            session=session.model_dump(),
            chunks=chunks,
            mastery_summary=self.sessions._mastery_summary(),
            mode=session.grounding_mode,
            count=count,
            focus=focus_topic,
        )
        if not questions:
            error = ModelOutputError("The model returned no usable check question.")
            error.retryable = True
            raise error
        # Keep the established contract: check questions are tracked under the
        # (possibly interleaved) focus topic, not whatever the model echoed back.
        for question in questions:
            question.topic = interleaved or topic
            self._insert_question(session_id, "check", question)
        self.sessions.set_phase(session_id, "check")
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "questions": self._pending_questions(session_id, "check"),
        }

    async def generate_pretest(self, session_id: str, llm) -> dict:
        """Fire one diagnostic question on the current node before teaching it.

        A wrong guess primes encoding of the explanation that follows
        (pretesting effect). The session phase is left untouched so the
        teach/check flow proceeds normally around the pending card.
        """
        session = self.sessions.get(session_id)
        existing = self._pending_questions(session_id, "pretest")
        if existing:
            return {"session": session.model_dump(), "questions": existing}
        self._delete_pending_questions(session_id, "pretest")
        topic = self._current_node_title(session) or session.goal
        chunks = self.sessions._select_chunks(session, "pretest question")
        questions = await request_questions(
            llm,
            session=session.model_dump(),
            chunks=chunks,
            mastery_summary=self.sessions._mastery_summary(),
            mode=session.grounding_mode,
            count=1,
            focus=topic,
        )
        if not questions:
            error = ModelOutputError("The model returned no usable pretest question.")
            error.retryable = True
            raise error
        # A pretest is defined as exactly one diagnostic question; never let a
        # verbose model dump extras into the pending queue.
        questions = questions[:1]
        for question in questions:
            question.topic = topic
            self._insert_question(session_id, "pretest", question)
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "questions": self._pending_questions(session_id, "pretest"),
        }

    async def review_learner_questions(
        self, session_id: str, llm, questions: list[str]
    ) -> dict:
        """'You ask the questions' mode: Sage answers the learner's own questions.

        The learner writes two questions over what they just studied; Sage answers
        each and grades whether it hits the key facts (coverage hit/partial/miss),
        then we persist the review rows for the record.
        """
        session = self.sessions.get(session_id)
        topic = self._current_node_title(session) or session.goal
        chunks = self.sessions._select_chunks(session, "learner questions review")
        review = await request_learner_review(
            llm,
            session=session.model_dump(),
            chunks=chunks,
            mastery_summary=self.sessions._mastery_summary(),
            mode=session.grounding_mode,
            questions=questions,
            topic=topic,
        )
        now = utc_now()
        for entry in review:
            self.conn.execute(
                "INSERT INTO feedback_actions (id, session_id, question_id, action, content, created_at) "
                "VALUES (?, ?, ?, 'learner_question', ?, ?)",
                (new_id(), session_id, "learner", json.dumps(entry), now),
            )
        self.conn.commit()
        return {"questions": review}

    async def generate_notes_quiz(
        self, session_id: str, llm, count: int = 3, subject: str | None = None
    ) -> dict:
        if count < 1 or count > 10:
            raise ValueError("count must be between 1 and 10")
        session = self.sessions.get(session_id)
        existing = self._pending_questions(session_id, "notes")
        if existing:
            return {"session": session.model_dump(), "questions": existing}
        self._delete_pending_questions(session_id, "notes")
        seed_chunks = self._notes_seed_chunks(session, subject)
        if not seed_chunks:
            error = ModelOutputError(
                "No notes chunks with structured environments are available "
                "for this session."
            )
            error.retryable = True
            raise error
        questions = await request_notes_questions(
            llm,
            session=session.model_dump(),
            seed_chunks=seed_chunks,
            count=count,
            subject=subject,
        )
        if len(questions) < count:
            error = ModelOutputError("The model returned no usable notes questions.")
            error.retryable = True
            raise error
        for question in questions:
            self._insert_question(
                session_id,
                "notes",
                QuizQuestionInput(
                    question=question["question"],
                    options=question["options"],
                    correct_index=question["correct_index"],
                    explanation=question.get("explanation"),
                    topic=question.get("topic"),
                    difficulty=question.get("difficulty", 3),
                ),
                source_ref=json.dumps(question["source_ref"]),
            )
        return {
            "session": self.sessions.get(session_id).model_dump(),
            "questions": self._pending_questions(session_id, "notes"),
        }

    def answer_quiz(
        self,
        session_id: str,
        question_id: str,
        choice_index: int | None = None,
        idk: bool = False,
        confidence: str | None = None,
        latency_ms: int | None = None,
    ) -> dict:
        if confidence is not None and confidence not in mastery.CONFIDENCE_LEVELS:
            raise ValueError(
                f"confidence must be one of {sorted(mastery.CONFIDENCE_LEVELS)}"
            )
        self.sessions.get(session_id)
        row = self.conn.execute(
            "SELECT * FROM quiz_questions WHERE id = ? AND session_id = ?",
            (question_id, session_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("question", question_id)
        question = dict(row)
        if question["kind"] not in ("probe", "check", "notes"):
            raise ValueError(f"question kind {question['kind']!r} cannot be answered")
        if question["status"] == "skipped":
            raise ValueError("this question was skipped and cannot be answered")
        try:
            options = json.loads(question["options_json"] or "[]")
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                "stored question options are corrupted and cannot be graded"
            ) from exc
        idk_index = len(options) - 1

        if not idk and (
            choice_index is None or choice_index < 0 or choice_index >= len(options)
        ):
            raise ValueError(
                "choice_index is required and must be within the options range"
            )
        if idk or choice_index == idk_index:
            choice = -1
            outcome = "idk"
        else:
            choice = choice_index
            outcome = "correct" if choice == question["correct_index"] else "incorrect"

        if question["status"] == "answered":
            if question["outcome"] == "correct":
                if question["user_choice"] == choice:
                    return self._answer_response(session_id, question, outcome)
                raise ValueError("question already answered correctly")
            if question["user_choice"] == choice:
                return self._answer_response(session_id, question, question["outcome"])

        now = utc_now()
        latency_ms = mastery.coerce_latency_ms(latency_ms)
        if latency_ms is None:
            latency_ms = _timestamp_gap_ms(question["created_at"], now)
        claimed = self.conn.execute(
            "UPDATE quiz_questions SET status = 'answered', user_choice = ?, outcome = ?, "
            "answered_at = ?, confidence = ?, latency_ms = ? "
            "WHERE id = ? AND session_id = ? AND status IS ? AND outcome IS ?",
            (
                choice,
                outcome,
                now,
                confidence,
                latency_ms,
                question_id,
                session_id,
                question["status"],
                question["outcome"],
            ),
        )
        self.conn.commit()
        if claimed.rowcount == 0:
            fresh = self.conn.execute(
                "SELECT * FROM quiz_questions WHERE id = ? AND session_id = ?",
                (question_id, session_id),
            ).fetchone()
            if fresh is None:
                raise NotFoundError("question", question_id)
            return self._answer_response(
                session_id, dict(fresh), dict(fresh)["outcome"]
            )

        question.update(
            {
                "status": "answered",
                "user_choice": choice,
                "outcome": outcome,
                "answered_at": now,
                "confidence": confidence,
                "latency_ms": latency_ms,
            }
        )
        source = (
            question["kind"]
            if question["kind"] in ("probe", "check", "notes")
            else "check"
        )
        mastery.record_evidence(
            self.conn,
            question["topic"],
            source,
            outcome,
            question_id=question_id,
            confidence=confidence,
            latency_ms=latency_ms,
        )
        # Register / reschedule an FSRS review card so material is spaced over time.
        try:
            from app.services import review as review_service

            review_service.register_card(
                self.conn,
                session_id=session_id,
                topic=question["topic"],
                question_id=question_id,
                kind=question["kind"],
                outcome=outcome,
                latency_ms=latency_ms,
            )
        except Exception:
            # Review scheduling is best-effort; never block grading on it.
            self.conn.rollback()
        if question["kind"] == "check":
            phase = self.sessions.get(session_id).phase
            if outcome in ("incorrect", "idk"):
                if phase == "check":
                    self.sessions.set_phase(session_id, "remediate")
                return self._answer_response(session_id, question, outcome)
            if phase not in ("check", "remediate"):
                return self._answer_response(session_id, question, outcome)
            self.sessions.set_phase(session_id, "teach")
            advanced = self.teach.advance(session_id, reset_check=True)
            return self._answer_response(
                session_id,
                question,
                outcome,
                next_node=advanced["node"],
                check_due=advanced["check_due"],
                session_dict=advanced["session"],
            )
        return self._answer_response(session_id, question, outcome)

    def _answer_response(
        self,
        session_id: str,
        question: dict,
        outcome: str,
        *,
        next_node: dict | None = None,
        check_due: bool = False,
        session_dict: dict | None = None,
    ) -> dict:
        pending = self.conn.execute(
            "SELECT COUNT(*) AS n FROM quiz_questions "
            "WHERE session_id = ? AND kind = 'probe' AND status = 'pending'",
            (session_id,),
        ).fetchone()["n"]
        return {
            "session": session_dict or self.sessions.get(session_id).model_dump(),
            "result": {
                "question_id": question["id"],
                "outcome": outcome,
                "correct_index": question["correct_index"],
                "explanation": question["explanation"],
                "probe_complete": pending == 0,
                "retry_allowed": outcome in ("incorrect", "idk"),
                "next_node": next_node,
                "check_due": check_due,
            },
        }

    def _pending_questions(self, session_id: str, kind: str) -> list[dict]:
        rows = rows_to_dicts(
            self.conn.execute(
                "SELECT * FROM quiz_questions WHERE session_id = ? AND kind = ? AND status = 'pending' "
                "ORDER BY created_at, rowid",
                (session_id, kind),
            )
        )
        return [question_dict(row) for row in rows]

    def _delete_pending_questions(self, session_id: str, kind: str) -> None:
        self.conn.execute(
            "DELETE FROM quiz_questions WHERE session_id = ? AND kind = ? AND status = 'pending'",
            (session_id, kind),
        )
        self.conn.commit()

    def _notes_seed_chunks(self, session: Session, subject: str | None) -> list[dict]:
        ready_ids = self.sessions.files.get_ready_file_ids(session.file_ids)
        ready_ids = self.sessions.files.expand_pairings(ready_ids)
        chunks = self.sessions.files.get_chunks_for_files(ready_ids)
        return [
            chunk
            for chunk in chunks
            if chunk.get("environment") is not None
            and (subject is None or chunk.get("subject") == subject)
        ]

    def _insert_question(
        self,
        session_id: str,
        kind: str,
        question: QuizQuestionInput,
        source_ref: str | None = None,
    ) -> str:
        question_id = new_id()
        now = utc_now()
        options = list(question.options) + [IDK_OPTION]
        self.conn.execute(
            "INSERT INTO quiz_questions (id, session_id, kind, topic, difficulty, question, options_json, correct_index, explanation, source_ref, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                question_id,
                session_id,
                kind,
                question.topic,
                question.difficulty,
                question.question,
                json.dumps(options),
                question.correct_index,
                question.explanation,
                source_ref,
                now,
            ),
        )
        self.conn.commit()
        return question_id

    def _current_node_title(self, session: Session) -> str | None:
        node_id = session.current_node_id
        if not node_id:
            return None
        row = self.conn.execute(
            "SELECT title FROM plan_nodes WHERE id = ? AND session_id = ?",
            (node_id, session.id),
        ).fetchone()
        return row["title"] if row else None
