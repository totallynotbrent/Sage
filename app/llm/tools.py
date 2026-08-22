from __future__ import annotations

import logging
from typing import get_args

from pydantic import ValidationError

from app.errors import ModelOutputError
from app.llm.messages import make_system_prompt
from app.llm.structured_outputs import parse_exact_json, validate_output
from app.models import TeachActionDraft

_ACTION_IDS = list(get_args(TeachActionDraft.model_fields["id"].annotation))

_KINDS = ("mermaid", "quiz", "todo", "latex")

_SCHEMA_HINTS = {
    "chat": '{"content":"..."}',
    "mermaid": '{"title":"...","source":"graph TD\\n A-->B"}',
    "todo": '{"title":"...","items":[{"text":"...","done":false}]}',
    "quiz": '{"questions":[{"question":"...","options":["...","..."],"correct_index":0,"explanation":"...","topic":"...","difficulty":3}]}',
    "latex": '{"title":"...","latex":"\\\\documentclass{article}..."}',
}

_KNOWN_ERROR_CODES = frozenset(
    """empty_text empty_output duplicate_options correct_index_out_of_range quiz_count
    quiz_shape unsupported_output_kind duplicate_action_ids invalid_action
    script_content invalid_output invalid_json trailing_data auth rate_limit
    timeout connection bad_request upstream""".split()
)

_WEB_BLOCKED_TOKENS = ("wikidiff", "redkiwiapp")

_STR = {"type": "string"}
_STATUS_PROP = {
    "status": {
        "type": "string",
        "description": (
            "Short, warm, user-facing description of what you are doing right "
            "now, shown live while the tool runs. Example: 'Searching for a "
            "reliable definition...'. Not hidden reasoning."
        ),
    }
}
_TOPIC = {"topic": _STR}
_QUIZ_PROPS = {"topic": _STR, "count": {"type": "integer", "minimum": 1, "maximum": 10}}
_ACTION_ITEM = {
    "id": {"type": "string", "enum": _ACTION_IDS},
    "label": {"type": "string", "maxLength": 60},
    "prompt": {"type": "string", "maxLength": 200},
}
_ACTIONS_PROP = {
    "type": "array",
    "minItems": 1,
    "maxItems": 6,
    "items": {
        "type": "object",
        "properties": _ACTION_ITEM,
        "required": list(_ACTION_ITEM),
    },
}


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    parameters = {
        "type": "object",
        "properties": {**properties, **_STATUS_PROP},
        "required": required,
    }
    function = {"name": name, "description": description, "parameters": parameters}
    return {"type": "function", "function": function}


TOOL_SCHEMAS = [
    _fn("web_search", "Search the web.", {"query": _STR}, ["query"]),
    _fn("generate_mermaid", "Generate a Mermaid diagram.", dict(_TOPIC), ["topic"]),
    _fn("generate_quiz", "Generate a quiz.", dict(_QUIZ_PROPS), ["topic"]),
    _fn("generate_todo", "Generate a study checklist.", dict(_TOPIC), ["topic"]),
    _fn("generate_latex", "Generate a LaTeX document.", dict(_TOPIC), ["topic"]),
    _fn(
        "record_step_actions",
        "Record follow-up teaching-step actions.",
        {"actions": _ACTIONS_PROP},
        ["actions"],
    ),
]


def available_tools(settings) -> list[dict]:
    enabled = {f"generate_{kind}" for kind in _KINDS} | {"record_step_actions"}
    if getattr(settings, "searxng_url", ""):
        enabled.add("web_search")
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in enabled]


def _issue_code(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    codes = detail.get("issue_codes") if isinstance(detail, dict) else None
    if isinstance(codes, list) and codes:
        return str(codes[0])
    errors = exc.errors() if isinstance(exc, ValidationError) else []
    if errors:
        return str(errors[0].get("type", "invalid_output"))
    message = str(exc)
    if message.startswith("Empty model output"):
        return "empty_output"
    code = message.split(":", 1)[0].strip()
    return code if code in _KNOWN_ERROR_CODES else "invalid_output"


async def execute_tool(name: str, arguments: dict, ctx) -> dict:
    arguments = arguments or {}
    try:
        if name == "web_search":
            return await _run_web_search(arguments, ctx)
        if name == "record_step_actions":
            raw_actions = arguments.get("actions")
            if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= 6:
                return {"error": "invalid_action"}
            try:
                drafts = [TeachActionDraft.model_validate(item) for item in raw_actions]
            except ValidationError:
                return {"error": "invalid_action"}
            ids = [draft.id for draft in drafts]
            if len(set(ids)) != len(ids):
                return {"error": "duplicate_action_ids"}
            return {"actions": [draft.model_dump() for draft in drafts]}
        if name.startswith("generate_"):
            return await _run_generate(name.removeprefix("generate_"), arguments, ctx)
    except Exception as exc:
        return {"error": _issue_code(exc)}
    return {"error": "unsupported_output_kind"}


def _web_entry(result: dict) -> dict:
    url = str(result.get("url") or "")
    snippet = str(result.get("snippet") or result.get("content") or "")
    return {"title": str(result.get("title") or ""), "url": url, "snippet": snippet}


async def _run_web_search(arguments: dict, ctx) -> dict:
    from app.services.web_search import search_web

    query = str(arguments.get("query") or "").strip()
    budget = min(5, ctx.settings.context_chunk_budget)
    results = await search_web(ctx.settings.searxng_url, query, max_results=budget)
    allowed = [
        result
        for result in results
        if not any(t in str(result.get("url") or "") for t in _WEB_BLOCKED_TOKENS)
    ]
    return {"results": [_web_entry(result) for result in allowed]}


async def _run_generate(kind: str, arguments: dict, ctx) -> dict:
    goal_topic = str(ctx.session_dict.get("goal") or "the session goal")
    topic = str(arguments.get("topic") or "").strip()
    if not topic:
        topic = str(getattr(ctx, "recent_user_text", "") or "").strip()
    if not topic:
        topic = goal_topic
    system = make_system_prompt(ctx.session_dict, ctx.mode, ctx.mastery_summary)
    shape = _SCHEMA_HINTS.get(kind, "{}")
    user = (
        f"Generate ONLY the JSON for output_kind={kind} about: {topic}. "
        f"Return ONLY this exact JSON shape: {shape}. Fill every field. "
        f"The topic MUST stay within the current lesson thread ({topic}). "
        "Do not choose an unrelated subject."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    validate_fn = ctx.validate_fn or validate_output
    output = None
    for attempt in range(2):
        text, error_text = await ctx.llm.complete_json(messages)
        try:
            if text is None:
                raise ValueError((error_text or "upstream").split(":", 1)[0])
            output = validate_fn(parse_exact_json(text), kind)
            break
        except (ModelOutputError, ValidationError, ValueError) as exc:
            stable_code = _issue_code(exc)
            if attempt:
                return {"error": stable_code}
            messages.append(
                {
                    "role": "assistant",
                    "content": text or f"(no usable answer: {stable_code})",
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous answer could not be used ({stable_code}). "
                        f"Return ONLY corrected JSON for output_kind={kind} about "
                        f"{topic}, EXACTLY this shape: {shape}"
                    ),
                }
            )
    if output is None:
        return {"error": "invalid_output"}
    payload = output.model_dump()
    count = arguments.get("count")
    if kind == "quiz" and isinstance(count, int) and 1 <= count <= 10:
        if len(payload["questions"]) != count:
            return {"error": "quiz_count"}
    if kind == "mermaid":
        validator = ctx.mermaid_validate
        if validator is None:
            from app.services.mermaid import validate_mermaid

            validator = validate_mermaid
        try:
            payload["diagram_type"] = await validator(payload["source"])
        except Exception as exc:
            logger = logging.getLogger("app")
            logger.warning(
                "mermaid validation failed; raw source: %r",
                payload["source"][:500],
            )
            return {
                "error": _issue_code(exc),
                "source_preview": repr(payload["source"][:400]),
            }
    payload["kind"] = kind
    return payload
