from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

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
from app.llm.tools import available_tools, execute_tool

if TYPE_CHECKING:
    from app.llm.client import LLMClient
    from app.models import Session

SUFFICIENCY_NOTICE = (
    "I could not find supporting material in the sources you selected for this "
    "session, so I have nothing source-backed to base an answer on. You can add "
    "more study files, switch to grounded-plus-knowledge mode, or ask me to "
    "explain based on general knowledge."
)

_MAX_TOOL_ITERATIONS = 6


@dataclass
class ToolContext:
    settings: Any
    session_dict: dict
    chunks: list
    mastery_summary: str
    mode: Any
    llm: Any
    validate_fn: Any = None
    mermaid_validate: Any = None
    conn: Any = None
    session_id: str = ""


def _tool_result_summary(name: str, result: dict):
    if result.get("error"):
        return f"error: {result['error']}"
    embedded = result.get("summary")
    if isinstance(embedded, str) and embedded.strip():
        return embedded
    if name == "web_search":
        return [
            {"title": entry["title"], "url": entry["url"]}
            for entry in result.get("results", [])
        ]
    if name == "record_step_actions":
        return f"{len(result.get('actions', []))} actions"
    if result.get("kind"):
        return f"{result['kind']} ok"
    return ""


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
                    "web_sources": [],
                }

                if strict_mode:
                    async for event in self._emit_sufficiency_notice(
                        session, client_msg_id, llm, cancel_event, is_disconnected
                    ):
                        yield event
                    return

                session_dict = session.model_dump()
                assistant_row = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM messages WHERE session_id = ? "
                    "AND role = 'assistant' AND partial = 0",
                    (session_id,),
                ).fetchone()
                assistant_count = int(assistant_row["n"])
                # Include the assistant's previous reply so the model remembers its own
                # check/fill-in questions when grading short answers like "error".
                last_assistant_row = self.conn.execute(
                    "SELECT content FROM messages WHERE session_id = ? "
                    "AND role = 'assistant' AND partial = 0 AND TRIM(COALESCE(content,'')) != '' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                lesson_state = {
                    "teaching_turns": assistant_count,
                    "greeting_done": assistant_count > 0,
                    "definition_taught": assistant_count > 0,
                    "last_user_text": user_text,
                    "your_previous_reply": (last_assistant_row["content"] if last_assistant_row else "")[-400:],
                }
                pending_rows = self.conn.execute(
                    "SELECT id, kind, question FROM quiz_questions "
                    "WHERE session_id = ? AND status = 'pending' "
                    "ORDER BY created_at LIMIT 10",
                    (session_id,),
                ).fetchall()
                # Conversation arc: compact digest of the full session so the model
                # always knows the story so far, even beyond the history window.
                arc_rows = self.conn.execute(
                    "SELECT role, content FROM messages "
                    "WHERE session_id = ? AND partial = 0 AND TRIM(COALESCE(content,'')) != '' "
                    "ORDER BY created_at",
                    (session_id,),
                ).fetchall()
                arc_lines = []
                for row in arc_rows:
                    role = "Learner" if row["role"] == "user" else "You"
                    content = (row["content"] or "").replace("\n", " ")
                    # first sentence-ish snippet per message keeps it compact
                    snippet = content[:160] + ("…" if len(content) > 160 else "")
                    arc_lines.append(f"{role}: {snippet}")
                lesson_state["conversation_arc"] = "\n".join(arc_lines)

                lesson_state["pending_questions"] = [
                    {
                        "id": row["id"],
                        "kind": row["kind"],
                        "question": (row["question"] or "")[:140],
                    }
                    for row in pending_rows
                ]
                messages = build_chat_messages(
                    session_dict,
                    user_text,
                    chunks,
                    self._mastery_summary(session_id),
                    mode,
                    lesson_state=lesson_state,
                    lightweight=self.settings.lightweight,
                )
                tool_ctx = ToolContext(
                    settings=self.settings,
                    session_dict=session_dict,
                    chunks=chunks,
                    mastery_summary=self._mastery_summary(session_id),
                    mode=mode,
                    llm=llm,
                    conn=self.conn,
                    session_id=session_id,
                )
                turn_tools = available_tools(self.settings, mode=mode, lightweight=self.settings.lightweight)
                stashed_actions: list[dict] = []
                web_sources: list[dict] = []
                step_actions_recorded = False
                # Track recent calls to break duplicate-call loops (model sometimes
                # repeats the same tool call with the same arguments).
                recent_calls: list[tuple[str, str]] = []

                ran_tool = False
                for _ in range(_MAX_TOOL_ITERATIONS):
                    had_tool_call = False
                    echoed = False
                    async for item in llm.stream_chat(
                        messages,
                        session_id=session_id,
                        cancel_event=cancel_event,
                        tools=turn_tools,
                    ):
                        if await is_disconnected():
                            llm.cancel_inflight(session_id)
                            raise GenerationCancelled(
                                "The client disconnected during generation."
                            )
                        if isinstance(item, str):
                            item = {"type": "delta", "delta": item}
                        event_type = item.get("type")
                        if event_type == "thinking":
                            # Model's private reasoning — surfaced for the UI's
                            # collapsible "thinking" section.
                            yield {"type": "thinking", "thinking": item.get("thinking") or ""}
                            continue
                        if event_type == "tool_call":
                            had_tool_call = True
                            ran_tool = True
                            name = str(item.get("name") or "")
                            raw_arguments = item.get("arguments")
                            arguments = (
                                raw_arguments if isinstance(raw_arguments, dict) else {}
                            )
                            tool_call_event = {
                                "type": "tool_call",
                                "name": name,
                                "arguments": arguments,
                            }
                            model_status = arguments.get("status")
                            if isinstance(model_status, str) and model_status.strip():
                                tool_call_event["status"] = model_status
                            yield tool_call_event
                            # Loop guard: same tool + same args as a previous call this turn
                            call_sig = (name, json.dumps(arguments, sort_keys=True))
                            if call_sig in recent_calls and name != "grade_answer":
                                result = {
                                    "error": "duplicate_call",
                                    "summary": f"{name} already ran; use its earlier result and continue.",
                                    "hint": "Do not repeat this call. Respond to the learner now.",
                                }
                                messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": str(item.get("id") or name),
                                        "content": json.dumps(result),
                                    }
                                )
                                had_tool_call = False  # force exit to text pass
                                continue
                            recent_calls.append(call_sig)
                            if name == "record_step_actions" and step_actions_recorded:
                                result = {
                                    "note": "actions already recorded this turn",
                                    "actions": [],
                                }
                            else:
                                result = await execute_tool(name, arguments, tool_ctx)
                                if name == "record_step_actions" and not result.get(
                                    "error"
                                ):
                                    step_actions_recorded = True
                            if not echoed:
                                messages.append(
                                    {
                                        "role": "assistant",
                                        "content": item.get("assistant_content")
                                        or None,
                                        "tool_calls": item.get("raw_tool_calls"),
                                    }
                                )
                                echoed = True
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": str(item.get("id") or name),
                                    "content": json.dumps(result),
                                }
                            )
                            if (
                                name == "record_step_actions"
                                and not result.get("note")
                                and isinstance(result.get("actions"), list)
                            ):
                                stashed_actions = result["actions"]
                            elif name == "web_search" and not result.get("error"):
                                web_sources.extend(
                                    {
                                        "title": entry["title"],
                                        "url": entry["url"],
                                    }
                                    for entry in result.get("results", [])
                                )
                            tool_event = {
                                "type": "tool_result",
                                "name": name,
                                "summary": _tool_result_summary(name, result),
                            }
                            if name == "generate_mermaid" and not result.get("error"):
                                tool_event.update(
                                    {
                                        "kind": result.get("kind"),
                                        "title": result.get("title"),
                                        "source": result.get("source"),
                                        "diagram_type": result.get("diagram_type"),
                                    }
                                )
                            # Button-first: forward full artifact payloads so the UI
                            # can render quiz/todo/latex cards in the right rail
                            # without any composer buttons or hardcoded prompts.
                            if name in ("generate_quiz", "generate_todo", "generate_latex") and not result.get("error"):
                                tool_event["artifact"] = {
                                    "kind": result.get("kind"),
                                    "title": result.get("title"),
                                    "source": result.get("source"),
                                    "latex": result.get("latex"),
                                    "questions": result.get("questions"),
                                    "items": result.get("items"),
                                    "diagram_type": result.get("diagram_type"),
                                }
                            if name == "run_probe":
                                tool_event["questions"] = result.get("questions", [])
                            if name == "start_review":
                                tool_event["review_cards"] = result.get("cards", [])
                            if name == "advance_lesson" and (
                                result.get("check_question")
                                or result.get("check_questions")
                            ):
                                tool_event["check_question"] = result.get(
                                    "check_question"
                                )
                                tool_event["check_questions"] = result.get(
                                    "check_questions"
                                )
                            if name == "build_plan" and result.get("plan_diagram"):
                                tool_event["plan_diagram"] = result["plan_diagram"]
                            yield tool_event
                            continue
                        delta = item.get("delta") or ""
                        buffer.append(delta)
                        if getattr(getattr(self, "settings", None), "streaming", False):
                            yield {"type": "delta", "delta": delta}
                        # buffered mode: text emitted after loop as one
                        # simulated-typing event (see below)
                    if not had_tool_call:
                        break


                # Defense in depth: strip any leaked call: fragments that slipped through (e.g. gemma's "call:run_probe/")
                # Bread's native /api/chat never hits this path, but Sage's model does — ensure storage is clean even if
                # the guard in app/llm/client.py regresses. Also keeps history used for next-turn context leak-free.
                import re as _re
                full_text = "".join(buffer)
                full_text = _re.sub(r"<call:\w+\b[^>]*>?", "", full_text)
                full_text = _re.sub(r"(?:(?<=\s)|(?<=^)|(?<=[\n\r\t.:;,!?)(\\\"'-]))\[?call:\w+\b/?\]?(?:\s*status\s*=\s*[\"'][^\"']*[\"'])?(?:\s*\([^)\"']*\))?", "", full_text)
                full_text = _re.sub(r"<call:\w+\b[^<]*$", "", full_text)
                # If the model ran tools but never produced a closing reply (a
                # tool-only turn — gemma sometimes stops right after the last
                # tool_result), force one no-tools completion so the learner
                # always gets a prose response instead of a dead end.
                if ran_tool and not full_text.strip():
                    fallback = await self._fallback_prose_message(messages, llm)
                    if fallback:
                        full_text = fallback
                        if not getattr(getattr(self, "settings", None), "streaming", False):
                            yield {"type": "buffered_text", "text": full_text}
                # Buffered mode (SAGE_STREAMING=false): emit the full sanitized reply
                # as a single simulated-typing event the UI animates word-by-word.
                if not getattr(self.settings, "streaming", False) and full_text:
                    yield {"type": "buffered_text", "text": full_text}
                sent_ids = {c["id"] for c in chunks}
                citations = [
                    marker
                    for marker in extract_citation_markers(full_text)
                    if marker in sent_ids
                ]
                # Skip persisting empty assistant turns (tool-only responses like
                # advance_lesson with no prose) — they add blank bubbles in the UI
                # and noise in history.
                if full_text.strip():
                    message = self.persist_message(
                        session_id, client_msg_id, full_text, citations
                    )
                    for citation in citations:
                        yield {"type": "citation", "chunk_id": citation}
                else:
                    message = None
                yield {
                    "type": "done",
                    "message_id": getattr(message, "id", None) if message else None,
                    "client_msg_id": client_msg_id,
                    "replayed": False,
                    "actions": stashed_actions,
                    "web_sources": web_sources,
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

    async def _fallback_prose_message(self, messages: list[dict], llm) -> str:
        """Force one no-tools completion when a tool-only turn left no text.

        gemma sometimes stops right after the final tool_result instead of
        writing the closing reply. Retry without tools so the learner never sees
        a dead-end blank bubble. Returns empty string if the model still yields
        nothing.
        """
        try:
            prompt = (
                "Write a brief, warm closing reply to the learner now. Summarize "
                "what just happened (the tool steps ran) in 1-3 plain sentences. "
                "Do not call any tools."
            )
            text, error_text = await llm.complete_json([*messages, {"role": "user", "content": prompt}])
            if isinstance(text, str) and text.strip():
                return text.strip()
        except Exception:
            return ""
        return ""

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
