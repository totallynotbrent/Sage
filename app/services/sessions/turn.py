from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable

from app.config import validation_problems
from app.errors import (
    ConfigError,
    ConflictError,
    GenerationCancelled,
    NotFoundError,
    ProviderError,
)
from app.llm.messages import (
    build_chat_messages,
    extract_citation_markers,
    format_location,
)

try:
    from app.services.web_search import search_web
except ImportError:
    search_web = None

if TYPE_CHECKING:
    from app.llm.client import LLMClient
    from app.models import Session

SUFFICIENCY_NOTICE = (
    "I could not find supporting material in the sources you selected for this "
    "session, so I have nothing source-backed to base an answer on. You can add "
    "more study files, switch to grounded-plus-knowledge mode, or ask me to "
    "explain based on general knowledge."
)


class TurnMixin:
    async def turn(
        self,
        session_id: str,
        user_text: str,
        *,
        client_msg_id: str,
        llm: LLMClient,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[dict]:
        try:
            problems = validation_problems(self.settings)
            if problems:
                yield _error_event(ConfigError(problems))
                return

            session = self.get(session_id)

            existing = self.find_message(session_id, client_msg_id)
            if existing is not None:
                if existing["partial"] == 0:
                    yield {
                        "type": "done",
                        "message_id": existing["id"],
                        "client_msg_id": client_msg_id,
                        "replayed": True,
                    }
                    return
                if llm.is_inflight(session_id):
                    yield _error_event(
                        ConflictError(
                            "This message is already being generated for this session.",
                            detail={"client_msg_id": client_msg_id},
                        )
                    )
                    return

            cancel_event = llm.begin_inflight(session_id)
            buffer: list[str] = []
            try:
                existing_marker = self.find_message(session_id, client_msg_id)
                if existing_marker is None:
                    self.persist_user_message(session_id, user_text)
                self.save_partial_marker(session_id, client_msg_id)

                chunks = self._select_chunks(session, user_text)
                mode = session.grounding_mode
                strict_mode = mode == "strict" and not chunks

                meta_chunks = [
                    {
                        "chunk_id": c["id"],
                        "file_name": c.get("file_name") or "?",
                        "location": format_location(c),
                        "preview": (c.get("text") or "")[:120],
                    }
                    for c in chunks
                ]
                yield {
                    "type": "meta",
                    "chunks": meta_chunks,
                    "insufficient": strict_mode,
                }

                if strict_mode:
                    async for event in self._emit_sufficiency_notice(
                        session, client_msg_id, llm, cancel_event, is_disconnected
                    ):
                        yield event
                    return

                web_results = None
                if (
                    search_web is not None
                    and self.settings.searxng_url
                    and mode == "grounded"
                    and not strict_mode
                ):
                    try:
                        max_results = min(5, self.settings.context_chunk_budget)
                        if max_results < 3:
                            max_results = 3
                        web_results = await search_web(
                            self.settings.searxng_url,
                            user_text,
                            max_results=max_results,
                        )
                    except Exception:
                        logging.getLogger("app").warning(
                            "web search failed", exc_info=True
                        )
                        web_results = None

                messages = build_chat_messages(
                    session.model_dump(),
                    user_text,
                    chunks,
                    self._mastery_summary(),
                    mode,
                    web_results=web_results,
                )
                async for delta in llm.stream_chat(
                    messages,
                    session_id=session_id,
                    cancel_event=cancel_event,
                ):
                    if await is_disconnected():
                        llm.cancel_inflight(session_id)
                        raise GenerationCancelled(
                            "The client disconnected during generation."
                        )
                    buffer.append(delta)
                    yield {"type": "delta", "delta": delta}

                full_text = "".join(buffer)
                sent_ids = {c["id"] for c in chunks}
                citations = [
                    marker
                    for marker in extract_citation_markers(full_text)
                    if marker in sent_ids
                ]
                message = self.persist_message(
                    session_id, client_msg_id, full_text, citations
                )
                for citation in citations:
                    yield {"type": "citation", "chunk_id": citation}
                yield {
                    "type": "done",
                    "message_id": message.id,
                    "client_msg_id": client_msg_id,
                    "replayed": False,
                }
            except GenerationCancelled as exc:
                yield _error_event(exc)
            except ProviderError as exc:
                yield _error_event(exc)
            except Exception as exc:  # noqa: BLE001 - never crash the SSE stream
                yield _error_event(
                    ProviderError(
                        "upstream",
                        f"Unexpected error during generation: {type(exc).__name__}",
                    )
                )
            finally:
                self.persist_partial_content(session_id, client_msg_id, "".join(buffer))
                llm.end_inflight(session_id)
        finally:
            self.conn.close()

    async def _emit_sufficiency_notice(
        self,
        session: Session,
        client_msg_id: str,
        llm: LLMClient,
        cancel_event,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[dict]:
        if await is_disconnected():
            llm.cancel_inflight(session.id)
            raise GenerationCancelled("The client disconnected during generation.")
        message = self.persist_message(
            session.id, client_msg_id, SUFFICIENCY_NOTICE, []
        )
        yield {"type": "delta", "delta": SUFFICIENCY_NOTICE}
        yield {
            "type": "done",
            "message_id": message.id,
            "client_msg_id": client_msg_id,
            "replayed": False,
        }

    async def retry_last_turn(
        self,
        session_id: str,
        client_msg_id: str,
        *,
        llm: LLMClient,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[dict]:
        try:
            self.get(session_id)
            existing = self.find_message(session_id, client_msg_id)
            if existing is None:
                yield _error_event(
                    NotFoundError("message with client_msg_id", client_msg_id)
                )
                return
            if existing["partial"] == 0:
                yield {
                    "type": "done",
                    "message_id": existing["id"],
                    "client_msg_id": client_msg_id,
                    "replayed": True,
                }
                return
            last_user_text = self._last_user_message(session_id) or ""
            async for event in self.turn(
                session_id,
                last_user_text,
                client_msg_id=client_msg_id,
                llm=llm,
                is_disconnected=is_disconnected,
            ):
                yield event
        finally:
            self.conn.close()

    def _last_user_message(self, session_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT content FROM messages WHERE session_id = ? AND role = 'user' "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return row["content"] if row else None


def _error_event(exc: Exception) -> dict:
    code = getattr(exc, "code", "error")
    message = getattr(exc, "message", str(exc))
    detail = getattr(exc, "detail", None)
    retryable = getattr(exc, "retryable", False)
    return {
        "type": "error",
        "code": code,
        "message": message,
        "detail": str(detail) if detail is not None else None,
        "retryable": retryable,
    }
