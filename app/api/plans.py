from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_app_settings, validation_problems
from app.db import get_conn
from app.errors import ConfigError
from app.llm.client import LLMClient, get_llm_client
from app.models import ExpandPlanBody, PlanNodeActionBody, ReorderPlanBody
from app.services.plans import PlansService

router = APIRouter()


def _require_configured(settings: Settings) -> None:
    problems = validation_problems(settings)
    if problems:
        raise ConfigError(problems)


def _handle_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/api/sessions/{session_id}/plan")
async def generate_plan(
    session_id: str,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    _require_configured(settings)
    return await PlansService(conn, settings).generate_plan(session_id, llm)


@router.post("/api/sessions/{session_id}/plan/approve")
async def approve_plan(
    session_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = PlansService(conn, settings)
    try:
        return service.approve(session_id)
    except ValueError as exc:
        raise _handle_value_error(exc)


@router.post("/api/sessions/{session_id}/plan/reorder")
async def reorder_plan(
    session_id: str,
    body: ReorderPlanBody,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = PlansService(conn, settings)
    try:
        return service.reorder(session_id, body.node_keys)
    except ValueError as exc:
        raise _handle_value_error(exc)


@router.post("/api/sessions/{session_id}/plan/skip")
async def skip_plan_node(
    session_id: str,
    body: PlanNodeActionBody,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = PlansService(conn, settings)
    try:
        return service.skip_node(session_id, body.node_key)
    except ValueError as exc:
        raise _handle_value_error(exc)


@router.post("/api/sessions/{session_id}/plan/expand")
async def expand_plan(
    session_id: str,
    body: ExpandPlanBody,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    _require_configured(settings)
    service = PlansService(conn, settings)
    try:
        return await service.expand(session_id, body.node_key, body.detail, llm)
    except ValueError as exc:
        raise _handle_value_error(exc)


@router.post("/api/sessions/{session_id}/plan/regenerate")
async def regenerate_plan(
    session_id: str,
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_app_settings),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    _require_configured(settings)
    return await PlansService(conn, settings).regenerate(session_id, llm)


@router.post("/api/sessions/{session_id}/plan/select")
async def select_plan_node(
    session_id: str,
    body: PlanNodeActionBody,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = PlansService(conn, settings)
    try:
        return service.select_node(session_id, body.node_key)
    except ValueError as exc:
        raise _handle_value_error(exc)
