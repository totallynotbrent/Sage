from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path
from typing import Iterable

APP_LOGGER_NAME = "app"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_file_path(data_dir: Path) -> Path:
    return data_dir / "logs" / "sage.log"


def mask_secret(text: str | None, secret: str) -> str | None:
    if text is None or not secret:
        return text
    return text.replace(secret, "***")


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._patterns = [
            re.compile(re.escape(s)) for s in secrets if s and len(s) >= 4
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in self._patterns:
            message = pattern.sub("***", message)
        record.msg = message
        record.args = ()
        return True


def setup_logging(secret: str = "", data_dir: Path | None = None) -> None:
    root = logging.getLogger()
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    app_logger = logging.getLogger(APP_LOGGER_NAME)
    if not any(isinstance(f, RedactingFilter) for f in app_logger.filters):
        app_logger.addFilter(RedactingFilter([secret]))
    root.setLevel(logging.INFO)

    if data_dir is None:
        return
    file_handler = _file_handler(data_dir)
    app_logger.addHandler(file_handler)
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.setLevel(logging.ERROR)
    uvicorn_error.addHandler(file_handler)


def _file_handler(data_dir: Path) -> logging.handlers.RotatingFileHandler:
    path = log_file_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = [
        h
        for h in logging.getLogger(APP_LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
        and h.baseFilename == os.fspath(path.absolute())
    ]
    if existing:
        return existing[0]
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler
