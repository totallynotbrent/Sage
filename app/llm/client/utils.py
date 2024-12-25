"""Helper utilities shared across the Ollama client (SDK value coercion, unsupported-feature detection)."""
from __future__ import annotations

import json
from typing import Any

UNSUPPORTED_TOOL_HINTS = ("tool", "function", "does not support tools")
UNSUPPORTED_THINK_HINTS = ("think", "thinking")
UNSUPPORTED_IMAGE_HINTS = ("image", "vision", "multimodal")
UNSUPPORTED_FORMAT_HINTS = (
    "format",
    "json schema",
    "structured output",
    "structured outputs",
    "does not support json",
)
UNSUPPORTED_LOGPROBS_HINTS = ("logprob", "log probabilities", "top_logprobs")
RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_logprobs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:128]:
        dumped = _dump_sdk_value(item)
        if isinstance(dumped, dict):
            result.append(dumped)
    return result


def _dump_sdk_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {str(key): _dump_sdk_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump_sdk_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _dump_sdk_value(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return value


def _compact_model_details(value: Any) -> dict[str, Any]:
    dumped = _dump_sdk_value(value)
    if not isinstance(dumped, dict):
        return {"value": dumped}
    result: dict[str, Any] = {}
    for key in ("name", "modified_at", "size", "digest", "details", "capabilities"):
        if key in dumped:
            result[key] = dumped[key]
    details = result.get("details")
    if isinstance(details, dict):
        result["details"] = {
            key: details[key]
            for key in (
                "parent_model",
                "format",
                "family",
                "families",
                "parameter_size",
                "quantization_level",
            )
            if key in details
        }
    return result


def _parse_arguments(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except json.JSONDecodeError:
        return {}


def _detect_unsupported_feature(message: str) -> str | None:
    lowered = message.lower()
    if any(hint in lowered for hint in UNSUPPORTED_TOOL_HINTS):
        return "tools"
    if any(hint in lowered for hint in UNSUPPORTED_THINK_HINTS):
        return "think"
    if any(hint in lowered for hint in UNSUPPORTED_IMAGE_HINTS):
        return "images"
    if any(hint in lowered for hint in UNSUPPORTED_FORMAT_HINTS):
        return "format"
    if any(hint in lowered for hint in UNSUPPORTED_LOGPROBS_HINTS):
        return "logprobs"
    return None


class _FeatureUnsupportedError(Exception):
    def __init__(self, feature: str, message: str) -> None:
        super().__init__(message)
        self.feature = feature

