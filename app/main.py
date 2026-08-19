from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings, validation_problems
from app.db import init_db
from app.errors import register_exception_handlers
from app.llm.client import reset_llm_client
from app.logging_setup import setup_logging
from app.services.watcher import watcher_loop

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.brot_api_key)
    reset_llm_client()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop_event = asyncio.Event()
        task = asyncio.create_task(watcher_loop(app.state.settings, stop_event))
        yield
        stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            task.cancel()

    app = FastAPI(title="Sage", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_path)
    for problem in validation_problems(settings):
        logger.warning("Configuration problem: %s", problem)
    logger.info(
        "Sage ready: data_dir=%s model=%s", settings.data_dir, settings.brot_model
    )

    register_exception_handlers(app, secret=settings.brot_api_key)

    from app.api import (
        chat,
        files,
        learning,
        plans,
        preferences,
        sessions,
        system,
        teach,
        watch,
    )

    app.include_router(system.router)
    app.include_router(files.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(learning.router)
    app.include_router(plans.router)
    app.include_router(teach.router)
    app.include_router(preferences.router)
    app.include_router(watch.router)

    return app


app = create_app()
