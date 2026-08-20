from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from app.errors import ModelOutputError
from app.models import (
    ChatOutputDraft,
    MermaidOutputDraft,
    QuizOutputDraft,
    StructuredOutputRequest,
    TodoOutputDraft,
)

_FENCE_RE = re.compile(r"^```(?:json)?\r?\n([\s\S]*?)\r?\n```$", re.IGNORECASE)
_MAX_REPAIR_ISSUES = 8


def parse_exact_json(text: str) -> Any:
    cleaned = text.lstrip("\ufeff").strip()
    if not cleaned:
        raise ModelOutputError("Empty model output; expected one JSON value.")
    match = _FENCE_RE.fullmatch(cleaned)
    if match:
        cleaned = match.group(1).strip()
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(cleaned)
    except json.JSONDecodeError as exc:
        raise ModelOutputError(
            "Model output was not valid JSON.",
            detail={"issue_codes": ["invalid_json"]},
        ) from exc
    if cleaned[end:].strip():
        raise ModelOutputError(
            "Model output contained trailing data.",
            detail={"issue_codes": ["trailing_data"]},
        )
    return value


def _issue_codes(exc: ValidationError) -> list[str]:
    codes: list[str] = []
    for error in exc.errors()[:_MAX_REPAIR_ISSUES]:
        code = str(error.get("type", "invalid"))
        if code not in codes:
            codes.append(code)
    return codes or ["invalid_output"]


def _normalize_source(source: str) -> str:
    source = source.strip().replace("\r\n", "\n").replace("\r", "\n")
    match = re.fullmatch(r"```(?:mermaid)?\n([\s\S]*?)\n```", source, re.IGNORECASE)
    return match.group(1).strip() if match else source


def validate_output(raw: Any, output_kind: str):
    if output_kind == "chat":
        draft = ChatOutputDraft.model_validate(raw)
        content = draft.content.strip()
        if not content:
            raise ValueError("empty_text")
        return draft.model_copy(update={"content": content})
    if output_kind == "mermaid":
        draft = MermaidOutputDraft.model_validate(raw)
        title = draft.title.strip()
        source = _normalize_source(draft.source)
        if not title or not source:
            raise ValueError("empty_text")
        return draft.model_copy(update={"title": title, "source": source})
    if output_kind == "todo":
        draft = TodoOutputDraft.model_validate(raw)
        title = draft.title.strip()
        items = [
            item.model_copy(update={"text": item.text.strip()}) for item in draft.items
        ]
        if not title or any(not item.text for item in items):
            raise ValueError("empty_text")
        return draft.model_copy(update={"title": title, "items": items})
    if output_kind == "quiz":
        draft = QuizOutputDraft.model_validate(raw)
        questions = []
        for question in draft.questions:
            options = [option.strip() for option in question.options]
            if not question.question.strip() or any(not option for option in options):
                raise ValueError("empty_text")
            if len(set(options)) != len(options):
                raise ValueError("duplicate_options")
            if question.correct_index >= len(options):
                raise ValueError("correct_index_out_of_range")
            questions.append(
                question.model_copy(
                    update={"question": question.question.strip(), "options": options}
                )
            )
        return draft.model_copy(update={"questions": questions})
    raise ValueError("unsupported_output_kind")


_VALUE_ERROR_CODES = {
    "empty_text": "empty_text",
    "duplicate_options": "duplicate_options",
    "correct_index_out_of_range": "correct_index_out_of_range",
    "quiz_count": "quiz_count",
    "quiz_shape": "quiz_shape",
    "unsupported_output_kind": "unsupported_output_kind",
}


def _coded_issue_codes(exc: ModelOutputError) -> list[str]:
    detail = exc.detail
    if isinstance(detail, dict):
        codes = detail.get("issue_codes")
        if isinstance(codes, list):
            return [code for code in codes if isinstance(code, str)]
    return []


def _has_issue_code(exc: Exception, code: str) -> bool:
    detail = getattr(exc, "detail", None)
    if not isinstance(detail, dict):
        return False
    codes = detail.get("issue_codes")
    return isinstance(codes, list) and code in codes


def _validation_detail(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ModelOutputError):
        return {"issue_codes": _coded_issue_codes(exc) or ["invalid_output"]}
    if isinstance(exc, ValidationError):
        return {"issue_codes": _issue_codes(exc)}
    return {"issue_codes": [_VALUE_ERROR_CODES.get(str(exc), "invalid_output")]}


def _repair_prompt(output_kind: str, error: Exception) -> str:
    detail = json.dumps(_validation_detail(error), separators=(",", ":"))
    return (
        f"Return only one JSON value for output_kind={output_kind}. Correct the prior "
        f"output using these validation issues: {detail}. Do not add commentary, "
        "Markdown fences, unknown fields, IDs, citations, or metadata."
    )


async def request_output(
    llm,
    messages: list[dict],
    request: StructuredOutputRequest,
    *,
    semantic_validator: Callable[[Any], Awaitable[str]] | None = None,
):
    current_messages = list(messages)
    last_error: Exception | None = None
    diagram_type: str | None = None
    for attempt in range(1, 3):
        text, error_text = await llm.complete_json(current_messages)
        if text is None:
            from app.llm.structured import provider_error_from_text

            raise provider_error_from_text(error_text)
        try:
            output = validate_output(parse_exact_json(text), request.output_kind)
            if request.output_kind == "quiz":
                if not isinstance(output, QuizOutputDraft):
                    raise ValueError("quiz_shape")
                if len(output.questions) != request.count:
                    raise ValueError("quiz_count")
            if semantic_validator is not None:
                diagram_type = await semantic_validator(output)
            return output, attempt, diagram_type
        except ModelOutputError as exc:
            if _has_issue_code(exc, "mermaid_validator_unavailable"):
                raise
            last_error = exc
            if attempt == 2:
                break
            current_messages.extend(
                [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": _repair_prompt(request.output_kind, exc),
                    },
                ]
            )
        except (ValidationError, ValueError) as exc:
            last_error = exc
            if attempt == 2:
                break
            current_messages.extend(
                [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": _repair_prompt(request.output_kind, exc),
                    },
                ]
            )
    error = ModelOutputError(
        "The model output did not satisfy the structured output contract.",
        detail={
            "kind": request.output_kind,
            **_validation_detail(last_error or ValueError()),
        },
    )
    error.retryable = True
    raise error
