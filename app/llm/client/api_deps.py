"""FastAPI dependency wiring for the Sage LLM client."""
from fastapi import Request as _Request

from app.llm.client.core import SageOllamaClient

def get_llm_client(request: _Request) -> SageOllamaClient:
    client = getattr(request.app.state, "llm_client", None)
    if client is None:
        client = SageOllamaClient(request.app.state.settings)
        request.app.state.llm_client = client
    return client


def reset_llm_client(app=None) -> None:
    if app is not None:
        app.state.llm_client = None

# Compat for tests that import the old helper (bread's client has no _strip_thought;
# sage kept it. Re-export here so tests still pass.)
import re as _compat_re

def _strip_thought(text: str) -> str:
    if not text:
        return text
    text = _compat_re.sub(r"<\|channel\|>thought.*?(?:channel\|>|<\|channel\|>)", "", text, flags=_compat_re.DOTALL)
    text = _compat_re.sub(r"<\|think\|>.*?(?:channel\|>|<\|channel\|>)", "", text, flags=_compat_re.DOTALL)
    return text
