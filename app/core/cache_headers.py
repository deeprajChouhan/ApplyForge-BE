"""
No-store cache headers for API responses.

API responses are per-user (they depend on the Authorization header). Without
explicit cache headers, a CDN / reverse proxy / browser may store one user's
response and serve it to another. Every /api/ response gets
`Cache-Control: private, no-store` and `Vary: Authorization` unless the route
already set its own Cache-Control (e.g. deliberately public assets).

Pure ASGI (not BaseHTTPMiddleware) so streaming responses are unaffected.
"""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class NoStoreApiMiddleware:
    def __init__(self, app: ASGIApp, path_prefix: str = "/api/") -> None:
        self.app = app
        self.path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.path_prefix):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                if b"cache-control" not in names:
                    headers.append((b"cache-control", b"private, no-store"))
                    headers.append((b"pragma", b"no-cache"))
                    if b"vary" in names:
                        headers = [
                            (k, v + b", Authorization") if k.lower() == b"vary" and b"authorization" not in v.lower() else (k, v)
                            for k, v in headers
                        ]
                    else:
                        headers.append((b"vary", b"Authorization"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
