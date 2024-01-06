"""Application factory: wiring, startup validation, static serving.

The same FastAPI process serves the JSON/SSE API and the static frontend
(same-origin, no proxy, no build step). Static files are mounted at ``/`` and
uploads live under ``DATA_DIR/uploads`` — never inside ``static/``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings, validation_problems
from app.db import init_db
from app.errors import register_exception_handlers
from app.llm.client import reset_llm_client
from app.logging_setup import setup_logging

logger = logging.getLogger("app")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the Sage application.

    ``settings`` is optional for tests; the process singleton is used otherwise.
    Startup validation is non-fatal: configuration problems are logged and
    surfaced via /api/health instead of preventing the app from booting.
    """
    settings = settings or get_settings()
    setup_logging(settings.freebuff_api_key)
    reset_llm_client()

    app = FastAPI(title="Sage", version="0.1.0")
    app.state.settings = settings

    # Startup validation (non-fatal).
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_path)
    for problem in validation_problems(settings):
        logger.warning("Configuration problem: %s", problem)
    logger.info(
        "Sage ready: data_dir=%s model=%s", settings.data_dir, settings.freebuff_model
    )

    register_exception_handlers(app, secret=settings.freebuff_api_key)

    from app.api import chat, files, sessions, system

    app.include_router(system.router)
    app.include_router(files.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)

    # Static frontend (mounted last so API routes win).
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()
