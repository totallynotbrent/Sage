"""Bread-parity OllamaClientConfig plus the Sage settings -> config mapping."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.config import Settings

ThinkLevel = Literal["low", "medium", "high", "max"]

@dataclass
class OllamaClientConfig:
    host: str
    hosts: list[str] = field(default_factory=list)
    api_key: str = ""
    api_keys: list[str] = field(default_factory=list)
    backends: list[tuple[str, str]] = field(default_factory=list)
    model: str = ""
    num_ctx: int = 131072
    num_threads: int = 5
    temperature: float = 0.67
    keep_alive: int = -1
    thinking: bool = False
    thinking_level: str = ""
    streaming: bool = True
    logprobs: bool = False
    top_logprobs: int = 0
    image_processing_enabled: bool = True
    debug_raw: bool = False
    max_retries: int = 3
    request_timeout: float = 120.0
    status_cache_seconds: float = 10.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "OllamaClientConfig":
        host = (getattr(settings, "api_url", "") or "").strip().rstrip("/")
        if host.endswith("/v1"):
            host = host[: -len("/v1")]
        return cls(
            host=host or "https://ollama.com",
            hosts=[],
            api_key=(getattr(settings, "api_key", "") or "").strip(),
            api_keys=[],
            backends=[],
            model=(getattr(settings, "model", "") or "").strip(),
            num_ctx=int(getattr(settings, "ollama_num_ctx", 32768) or 32768),
            num_threads=5,
            temperature=0.67,
            keep_alive=int(getattr(settings, "ollama_keep_alive", -1)),
            thinking=bool(getattr(settings, "ollama_thinking", True)),
            thinking_level="",
            streaming=bool(getattr(settings, "streaming", True)),
            logprobs=False,
            top_logprobs=0,
            image_processing_enabled=True,
            debug_raw=False,
            max_retries=3,
            request_timeout=120.0,
            status_cache_seconds=10.0,
        )

    def all_backends(self) -> list[tuple[str, str]]:
        if self.backends:
            return list(self.backends)
        return [(self.host, self.api_key)]
