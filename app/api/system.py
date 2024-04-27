from __future__ import annotations

import importlib
from fastapi import APIRouter, Depends

from app.config import Settings, get_app_settings, validation_problems
from app.llm.client import LLMClient, get_llm_client
from app.models import HealthReport
from app.util import utc_now

router = APIRouter()

AUDIT_LIBS = {"pymupdf": "pymupdf", "docx": "python-docx", "pptx": "python-pptx"}


def audit_dependencies() -> list[str]:
    errors: list[str] = []
    for module_name, label in AUDIT_LIBS.items():
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - report anything that fails
            if module_name == "pymupdf":
                try:
                    importlib.import_module("fitz")
                    continue
                except Exception:  # noqa: BLE001
                    pass
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
    return errors


@router.get("/api/health", response_model=HealthReport)
async def health(
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
) -> HealthReport:
    problems = validation_problems(settings)

    endpoint_reachable: bool | None = None
    if not problems:
        try:
            endpoint_reachable, _ = await llm.quick_probe()
        except Exception:  # noqa: BLE001 - reachability is best-effort
            endpoint_reachable = False

    dependency_errors = audit_dependencies()
    model_configured = not problems

    if problems:
        status = "error"
    elif endpoint_reachable is False or dependency_errors:
        status = "degraded"
    else:
        status = "ok"

    return HealthReport(
        status=status,
        model_configured=model_configured,
        endpoint_reachable=endpoint_reachable,
        dependency_errors=dependency_errors,
        checked_at=utc_now(),
    )
