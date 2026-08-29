"""Tool dispatch: route a tool name to its handler and normalize results."""
from __future__ import annotations

from pydantic import ValidationError

from app.models import TeachActionDraft
from app.llm.tools.actions import (
    _issue_code,
    _run_advance_lesson,
    _run_build_plan,
    _run_generate,
    _run_grade_answer,
    _run_probe,
    _run_web_search,
)


def _summarize(name: str, result: dict) -> str:
    if name == "run_probe":
        return f"probe ready: {len(result.get('questions') or [])} questions"
    if name == "grade_answer":
        return f"graded {result.get('outcome')}"
    if name == "build_plan":
        return f"plan ready: {len(result.get('nodes') or [])} nodes"
    if name == "advance_lesson":
        if result.get("lesson_complete"):
            return "lesson complete"
        if not result.get("advanced"):
            return "remediation"
        title = (result.get("node") or {}).get("title")
        return f"advanced to {title}" if title else "advanced"
    return ""


async def execute_tool(name: str, arguments: dict, ctx) -> dict:
    result = await _dispatch_tool(name, arguments, ctx)
    if not result.get("error"):
        summary = _summarize(name, result)
        if summary:
            result["summary"] = summary
    return result


async def _dispatch_tool(name: str, arguments: dict, ctx) -> dict:
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
        if name == "run_probe":
            return await _run_probe(arguments, ctx)
        if name == "grade_answer":
            return await _run_grade_answer(arguments, ctx)
        if name == "build_plan":
            return await _run_build_plan(arguments, ctx)
        if name == "advance_lesson":
            return await _run_advance_lesson(arguments, ctx)
        if name.startswith("generate_"):
            return await _run_generate(name.removeprefix("generate_"), arguments, ctx)
    except Exception as exc:
        return {"error": _issue_code(exc)}
    return {"error": "unsupported_output_kind"}