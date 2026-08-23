from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.errors import ModelOutputError

_ADAPTER = Path(__file__).parents[2] / "tools" / "validate_mermaid.mjs"
_MAX_SOURCE_LENGTH = 12000
_DEFAULT_MAX_CONCURRENT_VALIDATIONS = 4

_UNSAFE_SOURCE_TOKENS = (
    "<script",
    "javascript:",
    "%%{",
    "</script",
    "click",
    "href",
    "linkstyle",
)


def _max_concurrent_validations() -> int:
    raw = os.environ.get("SAGE_MERMAID_MAX_CONCURRENCY", "")
    if raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return _DEFAULT_MAX_CONCURRENT_VALIDATIONS


_validation_slots = asyncio.Semaphore(_max_concurrent_validations())


async def validate_mermaid(source: str) -> str:
    if len(source) > _MAX_SOURCE_LENGTH:
        raise ModelOutputError(
            "The Mermaid source is too long.",
            detail={"issue_codes": ["mermaid_too_long"]},
        )
    lowered = source.lower()
    if any(token in lowered for token in _UNSAFE_SOURCE_TOKENS):
        raise ModelOutputError(
            "The Mermaid source contains unsupported directives.",
            detail={"issue_codes": ["mermaid_unsafe_source"]},
        )
    try:
        async with _validation_slots:
            result = await asyncio.to_thread(_run_adapter, source)
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        raise ModelOutputError(
            "Mermaid validation is unavailable.",
            detail={"issue_codes": ["mermaid_validator_unavailable"]},
        ) from exc
    if not result.get("ok"):
        raise ModelOutputError(
            "The Mermaid source failed syntax validation.",
            detail={"issue_codes": ["mermaid_invalid"]},
        )
    raw = result.get("diagram_type")
    if isinstance(raw, dict):
        raw = raw.get("diagramType") or raw.get("type")
    return str(raw) if raw else "unknown"


def _run_adapter(source: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["node", str(_ADAPTER)],
        input=json.dumps({"source": source}),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.strip() or "Mermaid adapter failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OSError("Mermaid adapter returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise OSError("Mermaid adapter returned an invalid result")
    return result
