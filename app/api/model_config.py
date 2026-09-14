from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from app.config import Settings, get_app_settings
from app.db import get_conn
from app.llm.client import reset_llm_client
from app.models import ModelConfig, ModelConfigBody
from app.util import utc_now

router = APIRouter()


def _effective(settings: Settings, conn: sqlite3.Connection) -> ModelConfig:
    row = conn.execute("SELECT model, num_ctx, lightweight, thinking FROM preferences WHERE id = 1").fetchone()
    model = str(getattr(settings, "model", "") or "")
    num_ctx = int(getattr(settings, "ollama_num_ctx", 16384) or 16384)
    lightweight = bool(getattr(settings, "lightweight", False))
    thinking = bool(getattr(settings, "ollama_thinking", True))
    if row is not None:
        if row["model"]:
            model = row["model"]
        if row["num_ctx"]:
            num_ctx = int(row["num_ctx"])
        if row["lightweight"] is not None:
            lightweight = bool(row["lightweight"])
        if row["thinking"] is not None:
            thinking = bool(row["thinking"])
    return ModelConfig(model=model, num_ctx=num_ctx, lightweight=lightweight, thinking=thinking)


@router.get("/api/model-config", response_model=ModelConfig)
async def get_model_config(
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> ModelConfig:
    return _effective(settings, conn)


@router.patch("/api/model-config", response_model=ModelConfig)
async def update_model_config(
    body: ModelConfigBody,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_app_settings),
) -> ModelConfig:
    if body.model is not None:
        model = body.model.strip()
        settings.model = model
        conn.execute(
            "UPDATE preferences SET model = ?, updated_at = ? WHERE id = 1",
            (model, utc_now()),
        )
        conn.commit()
    if body.num_ctx is not None:
        num_ctx = max(1024, int(body.num_ctx))
        settings.ollama_num_ctx = num_ctx
        conn.execute(
            "UPDATE preferences SET num_ctx = ?, updated_at = ? WHERE id = 1",
            (num_ctx, utc_now()),
        )
        conn.commit()
    if body.lightweight is not None:
        settings.lightweight = bool(body.lightweight)
        conn.execute(
            "UPDATE preferences SET lightweight = ?, updated_at = ? WHERE id = 1",
            (int(body.lightweight), utc_now()),
        )
        conn.commit()
    if body.thinking is not None:
        settings.ollama_thinking = bool(body.thinking)
        conn.execute(
            "UPDATE preferences SET thinking = ?, updated_at = ? WHERE id = 1",
            (int(body.thinking), utc_now()),
        )
        conn.commit()
    request.app.state.settings = settings
    reset_llm_client(request.app)
    return _effective(settings, conn)