"""Application configuration via pydantic-settings.

Settings are read from environment variables and an optional ``.env`` file in
the current working directory. Configuration problems are non-fatal at startup:
``/api/health`` reports them and chat routes reject with ``ConfigError``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The placeholder value shipped in ``.env.example``; treated as "not configured".
PLACEHOLDER_KEY = "replace-with-your-local-key"


class Settings(BaseSettings):
    """All runtime settings for Sage."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Model endpoint (OpenAI-compatible).
    freebuff_base_url: str = "http://127.0.0.1:8877/v1"
    freebuff_api_key: str = ""
    freebuff_model: str = "deepseek/deepseek-v4-pro"

    # Network bind. Defaults keep the app LAN-reachable.
    host: str = "0.0.0.0"
    port: int = 8000

    # Storage.
    data_dir: Path = Path.home() / ".local" / "share" / "sage"

    # Limits.
    max_upload_mb: int = 30
    max_total_mb: int = 500
    chunk_chars: int = 1500
    chunk_overlap: int = 200
    context_chunk_budget: int = 8

    @property
    def db_path(self) -> Path:
        """SQLite database file under the data directory."""
        return self.data_dir / "sage.db"

    @property
    def uploads_dir(self) -> Path:
        """Uploaded-blob directory — never served by the API, always under the data directory."""
        return self.data_dir / "uploads"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached)."""
    return Settings()


def get_app_settings(request: Request) -> Settings:
    """FastAPI dependency: the settings bound to the current app instance.

    Routes must use this (not ``get_settings``) so that apps created with
    explicit settings in tests behave identically to the uvicorn-served app.
    """
    return request.app.state.settings


def validation_problems(settings: Settings) -> list[str]:
    """Return a list of human-readable configuration problems.

    An empty list means the configuration is usable. Individual problems are
    non-fatal at startup but block chat routes and are surfaced by health.
    """
    problems: list[str] = []

    key = (settings.freebuff_api_key or "").strip()
    if not key:
        problems.append(
            "FREEBUFF_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
    elif key == PLACEHOLDER_KEY:
        problems.append(
            "FREEBUFF_API_KEY still has the placeholder value; replace it with "
            "your local Freebuff key."
        )

    url = settings.freebuff_base_url.strip()
    parsed = urlparse(url)
    if not url:
        problems.append("FREEBUFF_BASE_URL is empty.")
    elif parsed.scheme not in ("http", "https") or not parsed.netloc:
        problems.append(
            f"FREEBUFF_BASE_URL is not a valid http(s) URL: {url!r}"
        )

    return problems
