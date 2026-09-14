"""Tool schema definitions (OpenAI-style function tools) and the availability filter."""
from __future__ import annotations

from typing import get_args

from app.models import TeachActionDraft

_ACTION_IDS = list(get_args(TeachActionDraft.model_fields["id"].annotation))

_KINDS = ("quiz", "todo", "latex")

_SCHEMA_HINTS = {
    "chat": '{"content":"..."}',
    "todo": '{"title":"...","items":[{"text":"...","done":false}]}',
    "quiz": '{"questions":[{"question":"...","options":["...","..."],"correct_index":0,"explanation":"...","topic":"...","difficulty":3}]}',
    "latex": '{"title":"...","latex":"\\\\documentclass{article}..."}',
}

_KNOWN_ERROR_CODES = frozenset(
    """empty_text empty_output duplicate_options correct_index_out_of_range quiz_count
    quiz_shape unsupported_output_kind duplicate_action_ids invalid_action
    script_content invalid_output invalid_json trailing_data auth rate_limit
    timeout connection bad_request upstream not_found""".split()
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
_LEARNING_TOOLS = frozenset(
    {
        "run_probe",
        "grade_answer",
        "build_plan",
        "advance_lesson",
        "run_final_quiz",
        "start_review",
    }
)


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
    _fn("generate_quiz", "Generate a quiz.", dict(_QUIZ_PROPS), ["topic"]),
    _fn("generate_todo", "Generate a study checklist.", dict(_TOPIC), ["topic"]),
    _fn("generate_latex", "Generate a LaTeX document.", dict(_TOPIC), ["topic"]),
    _fn(
        "record_step_actions",
        "Record follow-up teaching-step actions.",
        {"actions": _ACTIONS_PROP},
        ["actions"],
    ),
    _fn(
        "run_probe",
        "Start the diagnostic probe: generates adaptive multiple-choice "
        "questions mapping what the learner already knows before teaching "
        "begins. Call it ONLY when the session phase is 'setup' or 'probe' and "
        "no real teaching has started yet.",
        {},
        [],
    ),
    _fn(
        "start_review",
        "Begin spaced-repetition review of the cards due for this session. "
        "Call it when the learner is ready to review material that is due on "
        "the forgetting schedule (Sage decides when review is timely); it "
        "returns the due cards to present. If none are due it returns an "
        "empty list.",
        {},
        [],
    ),
    _fn(
        "grade_answer",
        "Grade the learner's answer to a probe or final-quiz question. "
        "question_id MUST be copied VERBATIM from the ids returned by "
        "run_probe or run_final_quiz (opaque hex strings like 9f2c…, NEVER "
        "display numbers like 1 or 2).",
        {
            "question_id": _STR,
            "choice_index": {"type": "integer"},
            "idk": {"type": "boolean", "default": False},
        },
        ["question_id"],
    ),
    _fn(
        "build_plan",
        "Reason out and persist the full lesson plan as ordered "
        "dependency-aware nodes.",
        {},
        [],
    ),
    _fn(
        "advance_lesson",
        "Advance to the next plan node after the current node is fully "
        "explained and the learner is following along. There are no "
        "intermediate check cards; just move on.",
        {},
        [],
    ),
    _fn(
        "run_final_quiz",
        "Generate the comprehensive final quiz once the lesson is "
        "essentially taught. It re-asks the diagnostic probe questions and "
        "covers the whole lesson, then returns the question cards to "
        "present. Grade the answers with grade_answer and judge whether the "
        "learner learned before completing.",
        {},
        [],
    ),
]


def available_tools(settings, mode: str | None = None, lightweight: bool = False) -> list[dict]:
    enabled = (
        {f"generate_{kind}" for kind in _KINDS}
        | {"record_step_actions"}
        | set(_LEARNING_TOOLS)
    )
    if mode != "strict" and getattr(settings, "searxng_url", ""):
        enabled.add("web_search")
    tools = [t for t in TOOL_SCHEMAS if t["function"]["name"] in enabled]
    if not lightweight:
        return tools

    def _slim(tool: dict) -> dict:
        fn = tool["function"]
        short_desc = {
            "web_search": "Search the web.",
            "generate_quiz": "Generate a quiz about a topic.",
            "generate_todo": "Generate a study checklist for a topic.",
            "generate_latex": "Generate a LaTeX document on a topic.",
            "record_step_actions": "Record follow-up teaching-step actions.",
            "run_probe": "Start the diagnostic probe before teaching.",
            "start_review": "Start spaced-repetition review of due cards.",
            "grade_answer": "Grade an answer to a probe or final-quiz question.",
            "build_plan": "Build and persist the lesson plan.",
            "advance_lesson": "Advance to the next plan node.",
            "run_final_quiz": "Generate the comprehensive final quiz.",
        }.get(fn["name"], fn["description"])
        slim_fn = dict(fn)
        slim_fn["description"] = short_desc
        props = dict(slim_fn.get("parameters", {}).get("properties", {}))
        props.pop("status", None)
        slim_fn["parameters"] = {
            "type": "object",
            "properties": props,
            "required": slim_fn.get("parameters", {}).get("required", []),
        }
        return {"type": tool["type"], "function": slim_fn}

    return [_slim(t) for t in tools]
