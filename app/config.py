from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_KEY = "replace-with-your-local-key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    brot_base_url: str = Field(
        default="https://ollama.com/v1", validation_alias="BROT_BASE_URL"
    )
    brot_api_key: str = Field(default="", validation_alias="BROT_API_KEY")
    brot_model: str = Field(default="gemma4:31b-cloud", validation_alias="BROT_MODEL")
    searxng_url: str = Field(default="", validation_alias="SEARXNG_URL")

    host: str = "0.0.0.0"
    port: int = 8000

    data_dir: Path = Path.home() / ".local" / "share" / "sage"

    max_upload_mb: int = 30
    max_total_mb: int = 500
    chunk_chars: int = 1500
    chunk_overlap: int = 200
    context_chunk_budget: int = 8

    watch_dirs: list[str] = Field(
        default_factory=list, validation_alias="SAGE_WATCH_DIRS"
    )
    watch_scan_seconds: int = Field(
        default=300, validation_alias="SAGE_WATCH_SCAN_SECONDS"
    )

    @model_validator(mode="after")
    def _normalize_urls(self):
        raw = (self.brot_base_url or "").strip()
        if raw:
            parsed = urlparse(raw)
            host = (parsed.hostname or "").lower()
            path = parsed.path or ""
            stripped = path.rstrip("/")
            if host == "ollama.com" and stripped == "":
                base = raw.rstrip("/")
                if not base.lower().endswith("/v1"):
                    self.brot_base_url = base + "/v1"
                else:
                    self.brot_base_url = base
            else:
                if raw.endswith("/") and not raw.rstrip("/").lower().endswith("/v1"):
                    self.brot_base_url = raw.rstrip("/")
                elif raw.endswith("/") and raw.rstrip("/").lower().endswith("/v1"):
                    self.brot_base_url = raw.rstrip("/")
                else:
                    self.brot_base_url = raw
        if self.searxng_url:
            self.searxng_url = self.searxng_url.strip().rstrip("/")
        return self

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

    key = (settings.brot_api_key or "").strip()
    if not key:
        msg = "BROT_API_KEY / OLLAMA_API_KEY is not set. Copy .env.example to .env and fill it in."
        parsed = urlparse(settings.brot_base_url.strip())
        if (parsed.hostname or "").lower() == "ollama.com":
            msg += " Get a key at https://ollama.com/settings/keys."
        problems.append(msg)
    elif key == PLACEHOLDER_KEY:
        problems.append(
            "BROT_API_KEY / OLLAMA_API_KEY still has the placeholder value; replace it with "
            "your Ollama key from https://ollama.com/settings/keys."
        )

    url = settings.brot_base_url.strip()
    parsed = urlparse(url)
    if not url:
        problems.append("BROT_BASE_URL is empty.")
    elif parsed.scheme not in ("http", "https") or not parsed.netloc:
        problems.append(f"BROT_BASE_URL is not a valid http(s) URL: {url!r}")

    return problems
