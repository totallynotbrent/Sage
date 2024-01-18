from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Any

from fastapi.responses import StreamingResponse

HEARTBEAT_SECONDS = 15.0


def sse_event(kind: str, data: dict[str, Any]) -> str:
    payload = {"type": kind}
    payload.update(data)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def heartbeat(
    gen: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[str]:
    iterator = gen.__aiter__()
    next_task = asyncio.ensure_future(anext(iterator))
    while True:
        try:
            done, _ = await asyncio.wait({next_task}, timeout=HEARTBEAT_SECONDS)
            if done:
                item = next_task.result()
                yield sse_event(item.get("type", "event"), item)
                next_task = asyncio.ensure_future(anext(iterator))
            else:
                yield ": ping\n\n"
        except StopAsyncIteration:
            break
        except Exception:
            next_task.cancel()
            raise


def sse_response(gen: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    return StreamingResponse(
        heartbeat(gen),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
