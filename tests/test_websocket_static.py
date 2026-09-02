from __future__ import annotations

import asyncio

import pytest


def _make_app():
    from app.config import Settings
    from app.main import create_app

    settings = Settings(api_key="test-key", api_url="http://127.0.0.1:9/v1", _env_file=None)
    return create_app(settings)


class _FakeWebSocketProtocol:
    def __init__(self):
        self.sent = []

    async def receive(self):
        return {"type": "websocket.connect"}

    async def send(self, message):
        self.sent.append(message)


def test_websocket_to_static_does_not_crash():
    async def run():
        app = _make_app()
        proto = _FakeWebSocketProtocol()
        scope = {
            "type": "websocket",
            "path": "/",
            "raw_path": b"/",
            "scheme": "ws",
            "query_string": b"",
            "headers": [],
            "client": ("172.19.0.1", 58622),
            "server": ("sage", 8000),
            "subprotocols": [],
            "root_path": "",
            "app": None,
            "state": {},
        }
        await app(scope, proto.receive, proto.send)
        close_msgs = [m for m in proto.sent if m.get("type") == "websocket.close"]
        assert close_msgs, f"expected websocket.close, got {proto.sent}"
        assert close_msgs[0].get("code") in (1008, 1000), close_msgs

    asyncio.run(run())


def test_http_to_static_still_serves():
    async def run():
        app = _make_app()
        send_messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            send_messages.append(message)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "scheme": "http",
            "query_string": b"",
            "headers": [(b"host", b"sage")],
            "client": ("172.19.0.1", 58622),
            "server": ("sage", 8000),
            "root_path": "",
            "app": None,
            "state": {},
        }
        await app(scope, receive, send)
        starts = [m for m in send_messages if m.get("type") == "http.response.start"]
        assert starts, f"expected http.response.start, got {send_messages}"
        assert starts[0]["status"] in (200, 307, 404), starts

    asyncio.run(run())