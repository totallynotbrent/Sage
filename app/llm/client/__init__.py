"""Sage Ollama LLM client package (split from app/llm/client.py).

Public surface preserved from the original flat module so imports keep working:
    from app.llm.client import LLMClient, get_llm_client, reset_llm_client
    from app.llm.client import _strip_thought, _strip_leaked_calls
"""

from app.llm.client.api_deps import _strip_thought, get_llm_client, reset_llm_client
from app.llm.client.attempt import ChatAttempt
from app.llm.client.config import OllamaClientConfig
from app.llm.client.core import LLMClient, SageOllamaClient
from app.llm.client.leak_guard import _strip_leaked_calls

__all__ = [
    "ChatAttempt",
    "LLMClient",
    "OllamaClientConfig",
    "SageOllamaClient",
    "get_llm_client",
    "reset_llm_client",
    "_strip_thought",
    "_strip_leaked_calls",
]