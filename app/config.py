from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_KEY = "replace-with-your-local-key"


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    BROT_base_url: str = "http://127.0.0.1:8877/v1"
    BROT_api_key: str = ""
    BROT_model: str = "deepseek/deepseek-v4-pro"

    host: str = "0.0.0.0"
    port: int = 8000

    data_dir: Path = Path.home() / ".local" / "share" / "sage"

    max_upload_mb: int = 30
    max_total_mb: int = 500
    chunk_chars: int = 1500
    chunk_overlap: int = 200
    context_chunk_budget: int = 8

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sage.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def validation_problems(settings: Settings) -> list[str]:
    problems: list[str] = []

    key = (settings.BROT_api_key or "").strip()
    if not key:
        problems.append(
            "BROT_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
    elif key == PLACEHOLDER_KEY:
        problems.append(
            "BROT_API_KEY still has the placeholder value; replace it with "
            "your local BROT key."
        )

    url = settings.BROT_base_url.strip()
    parsed = urlparse(url)
    if not url:
        problems.append("BROT_BASE_URL is empty.")
    elif parsed.scheme not in ("http", "https") or not parsed.netloc:
        problems.append(
            f"BROT_BASE_URL is not a valid http(s) URL: {url!r}"
        )

    return problems
