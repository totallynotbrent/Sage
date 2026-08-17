"""Logging configuration with API-key redaction.

Sage never logs the API key or full document contents. A ``RedactingFilter``
regex-replaces the configured key value in every log record, and document
content is never passed to the logger in the first place (only ids, counts,
and sizes are).
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

APP_LOGGER_NAME = "app"


def mask_secret(text: str | None, secret: str) -> str | None:
    """Replace every occurrence of ``secret`` in ``text`` with ``***``."""
    if text is None or not secret:
        return text
    return text.replace(secret, "***")


class RedactingFilter(logging.Filter):
    """A logging filter that redacts configured secrets from log records."""

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


def setup_logging(secret: str = "") -> None:
    """Configure the root and ``app`` loggers once.

    Safe to call multiple times; reconfiguration is idempotent.
    """
    root = logging.getLogger()
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Keep the filter installed exactly once per logger.
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    if not any(isinstance(f, RedactingFilter) for f in app_logger.filters):
        app_logger.addFilter(RedactingFilter([secret]))
    root.setLevel(logging.INFO)
