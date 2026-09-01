from __future__ import annotations

import asyncio

from app.config import Settings
from app.llm.client import LLMClient


class fake_stream:
    """Mimics the async iterator returned by ollama AsyncClient.chat(stream=True)."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class fake_ollama_client:
    async def chat(self, **kwargs):
        if kwargs.get("stream"):
            return fake_stream()
        raise AssertionError("non-streaming path not expected in this test")


def test_stream_chat_treats_provider_eof_as_normal_completion():
    client = LLMClient.__new__(LLMClient)
    object.__setattr__(client, "_client", fake_ollama_client())
    object.__setattr__(client, "_settings", Settings(api_key="test-key"))
    object.__setattr__(client, "_probe_cache", None)
    object.__setattr__(client, "_inflight", {})

    async def collect_chunks():
        return [chunk async for chunk in client.stream_chat([])]

    chunks = asyncio.run(collect_chunks())

    assert chunks == []
