"""
ASGI middleware for /api/v1/recruiter/partner/*.

1. CORS — the global CORSMiddleware only trusts ApplyForge origins and sends
   credentials; partner traffic needs per-key origins and NO credentials.
   * Preflight (OPTIONS) is answered here without touching the app. Echoing
     the origin is safe: the real request is still rejected server-side by
     get_partner_context unless the Origin is on the publishable key's list.
   * On the real response we strip whatever the inner CORS layer added and
     set Access-Control-Allow-Origin only when the key validated that origin.
2. Security headers + rate-limit headers.
3. Metadata-only request log (route template, status, latency, key prefix,
   IP, origin) — never bodies, query strings or secrets.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

PARTNER_PREFIX = "/api/v1/recruiter/partner/"
_ALLOWED_HEADERS = "Authorization, Content-Type, Idempotency-Key, X-ApplyForge-Key"
_ALLOWED_METHODS = "GET, POST, PUT, PATCH, DELETE"


def _hdr(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1")
    return None


class PartnerApiMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(PARTNER_PREFIX):
            await self.app(scope, receive, send)
            return

        origin = _hdr(scope, b"origin")
        if scope["method"] == "OPTIONS":
            headers = [
                (b"vary", b"Origin"),
                (b"access-control-allow-methods", _ALLOWED_METHODS.encode()),
                (b"access-control-allow-headers", _ALLOWED_HEADERS.encode()),
                (b"access-control-max-age", b"600"),
                (b"content-length", b"0"),
            ]
            if origin:
                headers.append((b"access-control-allow-origin", origin.encode("latin-1")))
            await send({"type": "http.response.start", "status": 204, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return

        # Body size cap BEFORE Starlette spools anything to disk. Bodies on
        # partner writes must declare their length (browser FormData does).
        if scope["method"] in ("POST", "PUT", "PATCH"):
            from app.core.config import settings

            raw_len = _hdr(scope, b"content-length")
            if raw_len is None:
                if _hdr(scope, b"transfer-encoding"):
                    await _reject(send, 411, "length_required", "Send a Content-Length header.")
                    return
            else:
                try:
                    n = int(raw_len)
                except ValueError:
                    await _reject(send, 400, "bad_request", "Invalid Content-Length.")
                    return
                if n > settings.partner_max_body_bytes:
                    await _reject(send, 413, "payload_too_large", "Request body is too large (max 11 MB).")
                    return

        scope.setdefault("state", {})
        started = time.perf_counter()
        status_holder = {"code": 500}

        async def _send(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                ctx = scope["state"].get("partner")
                headers = [
                    (k, v) for k, v in message.get("headers", [])
                    if not k.lower().startswith(b"access-control-")
                ]
                has_cache_control = any(k.lower() == b"cache-control" for k, _ in headers)
                headers += [
                    (b"vary", b"Origin"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                if not has_cache_control:
                    headers.append((b"cache-control", b"no-store"))
                if ctx is not None:
                    if ctx.origin and origin and ctx.origin == origin:
                        headers.append((b"access-control-allow-origin", origin.encode("latin-1")))
                        headers.append((b"access-control-expose-headers",
                                        b"X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset, Idempotent-Replayed"))
                    rate = ctx.rate or {}
                    if rate:
                        headers += [
                            (b"x-ratelimit-limit", str(rate.get("limit", "")).encode()),
                            (b"x-ratelimit-remaining", str(rate.get("remaining", "")).encode()),
                            (b"x-ratelimit-reset", str(rate.get("reset", "")).encode()),
                        ]
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            try:
                _log(scope, status_holder["code"], int((time.perf_counter() - started) * 1000), origin)
            except Exception as exc:  # logging must never break a response
                logger.warning("partner_request_log_failed error=%s", exc)


async def _reject(send, status: int, error: str, message: str) -> None:
    import json

    body = json.dumps({"detail": {"error": error, "message": message}}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store"),
    ]})
    await send({"type": "http.response.body", "body": body})


def _log(scope, status: int, duration_ms: int, origin: str | None) -> None:
    from app.recruiter.models import ApiRequestLog
    from app.recruiter.partner import ratelimit
    from app.recruiter.partner.jobs import session_scope

    ctx = scope.get("state", {}).get("partner")
    if ctx is None:
        # Unauthenticated traffic (bad/missing keys, feed scrapers): sample a
        # few per IP per minute so anonymous floods can't fill the table.
        from starlette.requests import Request

        from app.recruiter.partner.deps import client_ip

        ok, _, _ = ratelimit.hit(f"anonlog:{ratelimit.ip_bucket(client_ip(Request(scope)))}", 10, 60)
        if not ok:
            return
    route = scope.get("route")
    path = getattr(route, "path", None) or "(unmatched)"
    ip = None
    if ctx is not None:
        ip = ctx.ip
    with session_scope() as db:
        db.add(
            ApiRequestLog(
                agency_id=ctx.agency_id if ctx else None,
                api_key_id=ctx.key_id if ctx else None,
                key_prefix=ctx.key_prefix if ctx else None,
                method=scope.get("method", "")[:8],
                path=path[:300],
                status_code=status,
                duration_ms=duration_ms,
                ip=ip,
                origin=(origin or None) and origin[:255],
            )
        )
        db.commit()
