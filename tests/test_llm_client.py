from __future__ import annotations

import asyncio

from app.config import Settings
from app.llm.client import LLMClient


class fake_stream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class fake_completions:
    async def create(self, **kwargs):
        return fake_stream()


class fake_chat:
    completions = fake_completions()


class fake_client:
    chat = fake_chat()


def test_stream_chat_treats_provider_eof_as_normal_completion():
    client = LLMClient.__new__(LLMClient)
    object.__setattr__(client, "_client", fake_client())
    object.__setattr__(client, "_settings", Settings(brot_api_key="test-key"))

    async def collect_chunks():
        return [chunk async for chunk in client.stream_chat([])]

    chunks = asyncio.run(collect_chunks())

    assert chunks == []
