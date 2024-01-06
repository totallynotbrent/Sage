"""Server-sent-events helpers for streaming turns.

SSE over POST: each event is a single ``data: {json}\n\n`` line; the event
"type" lives inside the JSON payload so the frontend just parses each line.
A heartbeat keeps proxies/clients from idling out during long local-model
generations.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Any

from fastapi.responses import StreamingResponse

#: Heartbeat interval while the model is generating.
HEARTBEAT_SECONDS = 15.0


def sse_event(kind: str, data: dict[str, Any]) -> str:
    """Format one event as a JSON ``data:`` line."""
    payload = {"type": kind}
    payload.update(data)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def heartbeat(
    gen: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[str]:
    """Wrap an event-dict generator, emitting ``: ping`` comments while idle."""
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
    """Build the SSE StreamingResponse for a turn generator."""
    return StreamingResponse(
        heartbeat(gen),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
