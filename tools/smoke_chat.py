#!/usr/bin/env python3
"""Manual live-endpoint smoke check for Sage.

Sends a "ping" to the configured Freebuff endpoint and prints the result.
Not part of the pytest suite — requires a running local model endpoint and a
configured API key (`.env` in the working directory or environment variables).

Usage:
    python tools/smoke_chat.py
"""

from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    from app.config import get_settings, validation_problems
    from app.llm.client import LLMClient

    settings = get_settings()
    problems = validation_problems(settings)
    if problems:
        print("Configuration problems:")
        for problem in problems:
            print(f"  - {problem}")
        print("Fix your .env file first.")
        return 2

    print(f"Endpoint: {settings.freebuff_base_url}")
    print(f"Model:    {settings.freebuff_model}")

    client = LLMClient(settings)
    print("Sending ping …")
    ok, message = await client.quick_probe()
    if ok:
        print("OK: the endpoint answered.")
        return 0

    print("FAILED:")
    print(f"  {message}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
