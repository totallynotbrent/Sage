"""optional password gate for the web app."""
from __future__ import annotations

import hmac
from urllib.parse import quote

COOKIE = "sage_auth"

# paths the login flow itself needs, always reachable with auth off
OPEN_PREFIXES = ("/api/health", "/api/login", "/api/logout", "/login", "/auth/")


def token_for(password: str) -> str:
    return hmac.new(password.encode(), b"sage-auth", "sha256").hexdigest()


class AuthMiddleware:
    """standalone-asgi gate; drops requests to the login flow when authed."""

    def __init__(self, app, password: str = ""):
        self.app = app
        self.password = password

    async def _respond(self, send, status, location=None, body=b""):
        headers = [("content-type", "application/json"), ("content-length", str(len(body)))]
        status_line = {401: b"401 Unauthorized", 302: b"302 Found"}[status]
        if location:
            headers.append(("location", location))
        await send({"type": "http.response.start", "status": status, "headers": [(k.encode(), v.encode()) for k, v in headers]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.password:
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path.startswith(OPEN_PREFIXES):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        cookie = headers.get(b"cookie", b"").decode("latin1", "replace")
        expected = token_for(self.password)
        if any(part.split("=", 1) == [COOKIE, expected] for part in cookie.split("; ") if "=" in part):
            await self.app(scope, receive, send)
            return
        if path.startswith("/api/"):
            body = b'{"detail":"login required"}'
            await self._respond(send, 401, body=body)
        else:
            await self._respond(send, 302, location=quote("/login"))