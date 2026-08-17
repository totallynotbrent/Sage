"""Small shared helpers: ISO-8601 timestamps and id generation."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> str:
    """Return the current time as an ISO-8601 UTC timestamp with milliseconds."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def new_id() -> str:
    """Return a new random lowercase-hex id (32 chars, URL-safe)."""
    return uuid4().hex
