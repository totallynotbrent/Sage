from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocket

from app.config import Settings, get_settings, validation_problems
from app.db import init_db
from app.errors import register_exception_handlers
from app.llm.client import reset_llm_client
from app.logging_setup import setup_logging
from app.api.auth import router as auth_router
from app.security import AuthMiddleware
from app.services.watcher import watcher_loop

logger = logging.getLogger("app")


class _WebsocketSafeStatic(StaticFiles):
    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            return await self._reject(scope, receive, send)
        return await super().__call__(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send):
        ws = WebSocket(scope=scope, receive=receive, send=send)
        await ws.close(code=1008)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.api_key, data_dir=settings.data_dir)
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
    app.add_middleware(AuthMiddleware, password=settings.sage_password)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_path)
    for problem in validation_problems(settings):
        logger.warning("Configuration problem: %s", problem)
    logger.info(
        "Sage ready: data_dir=%s model=%s", settings.data_dir, settings.model
    )

    register_exception_handlers(app, secret=settings.api_key)

    from app.api import (
        chat,
        files,
        learning,
        outputs,
        plans,
        preferences,
        review,
        sessions,
        system,
        teach,
        watch,
    )

    app.include_router(auth_router)
    app.include_router(system.router)
    app.include_router(files.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(outputs.router)
    app.include_router(learning.router)
    app.include_router(review.router)
    app.include_router(plans.router)
    app.include_router(teach.router)
    app.include_router(preferences.router)
    app.include_router(watch.router)

    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():

        @app.get("/", include_in_schema=False)
        async def serve_ui():
            return FileResponse(str(static_dir / "index.html"))

        @app.get("/login", include_in_schema=False)
        async def serve_login():
            return FileResponse(str(static_dir / "login.html"))

        app.mount("/", _WebsocketSafeStatic(directory=str(static_dir)), name="static")

    return app


app = create_app()
