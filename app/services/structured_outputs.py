from __future__ import annotations

import logging
import sqlite3

from app.config import Settings
from app.errors import ModelOutputError
from app.llm.messages import build_chat_messages
from app.llm.structured_outputs import request_output
from app.models import (
    ChatOutputContent,
    ChatOutputEnvelope,
    LatexOutputContent,
    LatexOutputEnvelope,
    MermaidOutputContent,
    MermaidOutputEnvelope,
    QuizOutputContent,
    QuizOutputEnvelope,
    StructuredOutputRequest,
    StructuredQuizQuestion,
    TeachOutputContent,
    TeachOutputEnvelope,
    TodoItem,
    TodoOutputContent,
    TodoOutputEnvelope,
    ValidationMetadata,
)
from app.services.mermaid import validate_mermaid
from app.services.sessions import SessionService
from app.util import new_id

try:
    from app.services.web_search import search_web
except ImportError:
    search_web = None


class StructuredOutputService:
    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.sessions = SessionService(conn, settings)

    async def generate(self, session_id: str, request: StructuredOutputRequest, llm):
        session = self.sessions.get(session_id)
        chunks = self.sessions._select_chunks(session, request.prompt)
        if session.grounding_mode == "strict" and not chunks:
            error = ModelOutputError(
                "No source material is available to answer from in strict mode.",
                detail={"issue_codes": ["insufficient_context"]},
            )
            error.retryable = True
            raise error
        web_results = None
        if (
            search_web is not None
            and self.sessions.settings.searxng_url
            and session.grounding_mode == "grounded"
        ):
            try:
                max_results = min(5, self.sessions.settings.context_chunk_budget)
                web_results = await search_web(
                    self.sessions.settings.searxng_url,
                    request.prompt,
                    max_results=max_results,
                )
            except Exception:
                logging.getLogger("app").warning("web search failed", exc_info=True)
                web_results = None
        messages = build_chat_messages(
            session.model_dump(),
            self._prompt(request),
            chunks,
            self.sessions._mastery_summary(session_id),
            session.grounding_mode,
            web_results=web_results,
            lightweight=self.sessions.settings.lightweight,
        )
        validator = self._validate_mermaid if request.output_kind == "mermaid" else None
        draft, attempts, diagram_type = await request_output(
            llm, messages, request, semantic_validator=validator
        )
        content, kind = await self._build_content(
            draft, request.output_kind, diagram_type
        )
        citations = [chunk["id"] for chunk in chunks]
        return self._envelope(
            session_id,
            kind,
            content,
            citations,
            attempts,
        )

    @staticmethod
    async def _validate_mermaid(draft) -> str:
        return await validate_mermaid(draft.source)

    @staticmethod
    def _prompt(request: StructuredOutputRequest) -> str:
        schemas = {
            "chat": '{"content":"..."}',
            "mermaid": '{"title":"...","source":"graph TD\\n A-->B"}',
            "todo": '{"title":"...","items":[{"text":"...","done":false}]}',
            "quiz": '{"questions":[{"question":"...","options":["...","..."],"correct_index":0,"explanation":"...","topic":"...","difficulty":3}]}',
            "teach": '{"content":"...","latex_blocks":["$$...$$"],"actions":[{"id":"continue","label":"Continue","prompt":"..."}]}',
            "latex": '{"title":"...","latex":"..."}',
        }
        return (
            f"Create a {request.output_kind} artifact for this learner request. "
            f"For a quiz, return exactly {request.count} questions. "
            f"Return ONLY JSON matching this shape: {schemas[request.output_kind]}. "
            "Do not include IDs, positions, citations, or metadata. "
            f"Learner request: {request.prompt}"
        )

    async def _build_content(
        self, draft, output_kind: str, diagram_type: str | None = None
    ):
        if output_kind == "chat":
            return ChatOutputContent(content=draft.content), "chat"
        if output_kind == "mermaid":
            return (
                MermaidOutputContent(
                    title=draft.title,
                    source=draft.source,
                    diagram_type=diagram_type or "unknown",
                ),
                "mermaid",
            )
        if output_kind == "todo":
            items = [
                TodoItem(id=new_id(), position=index, text=item.text, done=item.done)
                for index, item in enumerate(draft.items)
            ]
            return TodoOutputContent(title=draft.title, items=items), "todo"
        if output_kind == "quiz":
            questions = [
                StructuredQuizQuestion(
                    id=new_id(),
                    position=index,
                    question=item.question,
                    options=item.options,
                    correct_index=item.correct_index,
                    explanation=item.explanation,
                    topic=item.topic,
                    difficulty=item.difficulty,
                )
                for index, item in enumerate(draft.questions)
            ]
            return QuizOutputContent(questions=questions), "quiz"
        if output_kind == "teach":
            return (
                TeachOutputContent(
                    content=draft.content,
                    latex_blocks=list(draft.latex_blocks),
                    actions=list(draft.actions),
                ),
                "teach",
            )
        if output_kind == "latex":
            return LatexOutputContent(title=draft.title, latex=draft.latex), "latex"
        raise ModelOutputError("Unsupported structured output kind.")

    @staticmethod
    def _envelope(session_id, kind, content, citations, attempts):
        common = {
            "schema_version": "1",
            "output_id": new_id(),
            "session_id": session_id,
            "kind": kind,
            "content": content,
            "citations": citations,
            "validation": ValidationMetadata(attempts=attempts),
        }
        envelope_types = {
            "chat": ChatOutputEnvelope,
            "mermaid": MermaidOutputEnvelope,
            "todo": TodoOutputEnvelope,
            "quiz": QuizOutputEnvelope,
            "teach": TeachOutputEnvelope,
            "latex": LatexOutputEnvelope,
        }
        return envelope_types[kind](**common)
