"""
Opaque public ids for the recruiter API.

Database primary keys are sequential ints. They are never exposed: every id
that crosses the API boundary (URLs, request/response bodies) is a
GUID-formatted, keyed-encrypted token of the int —

    10  <->  "3f9a1c2e-7b44-0d91-a5e2-6c0b8e71f4d3"

The mapping is a 128-bit Feistel permutation keyed by RECRUITER_ID_KEY (or a
key derived from SECRET_KEY), so tokens are:
  * stable (same row → same GUID, links and bookmarks keep working),
  * non-sequential and non-guessable without the key,
  * self-validating (a tampered/random GUID fails an embedded tag check),
  * free — no extra column, no lookup per request.

Enforced at the HTTP boundary by `PublicIdRoute` (the route_class of every
recruiter APIRouter), so handlers, services and schemas keep working with
ints and nothing internal changes:
  * path + query params named `id` / `*_id` / `*_ids` must be GUIDs → decoded
    to ints before FastAPI validates them (a raw int in the URL → 404);
  * JSON request bodies: GUID strings under id-like keys → ints (ints are
    still tolerated in bodies for older clients, never required);
  * JSON responses: every int under an id-like key → GUID, at any depth.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import struct
from functools import lru_cache
import json
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


_TAG = b"afrecid1"  # 8-byte integrity tag occupying the high half of the plaintext
_ROUNDS = 8
_GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@lru_cache(maxsize=1)
def _key() -> bytes:
    from app.core.config import settings

    explicit = settings.recruiter_id_key.get_secret_value() if settings.recruiter_id_key else ""
    material = explicit or f"recruiter-public-id::{settings.secret_key}"
    return hashlib.sha256(material.encode()).digest()


def _round(half: bytes, i: int) -> bytes:
    return hmac.new(_key(), bytes([i]) + half, hashlib.sha256).digest()[:8]


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def encode_id(value: int) -> str:
    if value is None:
        return None  # type: ignore[return-value]
    if isinstance(value, str):  # already public
        return value
    left, right = _TAG, struct.pack(">Q", int(value))
    for i in range(_ROUNDS):
        left, right = right, _xor(left, _round(right, i))
    h = (left + right).hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def decode_id(token: str) -> int:
    t = (token or "").strip().lower()
    if not _GUID_RE.match(t):
        raise ValueError("Invalid id")
    raw = bytes.fromhex(t.replace("-", ""))
    left, right = raw[:8], raw[8:]
    for i in reversed(range(_ROUNDS)):
        left, right = _xor(right, _round(left, i)), left
    if not hmac.compare_digest(left, _TAG):
        raise ValueError("Invalid id")
    return struct.unpack(">Q", right)[0]


# Fields that hold entity ids without the `_id` suffix.
EXTRA_ID_KEYS = frozenset({
    "ids",
    "skipped_existing",
    "captured_by",
    "completed_by",
    "screening_completed_by",
    "submitted_by",
})


def is_id_key(key: str) -> bool:
    return (
        key == "id"
        or key.endswith("_id")
        or key.endswith("_ids")
        or key in EXTRA_ID_KEYS
    )


def _is_guid(v: Any) -> bool:
    return isinstance(v, str) and bool(_GUID_RE.match(v.strip().lower()))


def encode_payload(obj: Any, _key_is_id: bool = False) -> Any:
    """Replace ints under id-like keys with GUIDs, recursively."""
    if isinstance(obj, dict):
        return {k: encode_payload(v, isinstance(k, str) and is_id_key(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [encode_payload(v, _key_is_id) for v in obj]
    if _key_is_id and isinstance(obj, int) and not isinstance(obj, bool):
        return encode_id(obj)
    return obj


def decode_payload(obj: Any, _key_is_id: bool = False) -> Any:
    """Replace GUID strings under id-like keys with ints, recursively."""
    if isinstance(obj, dict):
        return {k: decode_payload(v, isinstance(k, str) and is_id_key(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode_payload(v, _key_is_id) for v in obj]
    if _key_is_id and _is_guid(obj):
        return decode_id(obj)
    return obj


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Not found"})


class PublicIdRoute(APIRoute):
    """APIRoute that translates opaque GUIDs <-> DB ints at the boundary."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            # 1) Path params — GUID only.
            path_params = request.scope.get("path_params") or {}
            for k, v in list(path_params.items()):
                if is_id_key(k):
                    try:
                        path_params[k] = str(decode_id(v))
                    except ValueError:
                        return _not_found()

            # 2) Query params — GUID only for id-like keys.
            raw_qs = request.scope.get("query_string", b"")
            if raw_qs:
                pairs = parse_qsl(raw_qs.decode("latin-1"), keep_blank_values=True)
                changed = False
                out = []
                for k, v in pairs:
                    if is_id_key(k) and v != "":
                        try:
                            v = str(decode_id(v))
                            changed = True
                        except ValueError:
                            return _not_found()
                    out.append((k, v))
                if changed:
                    request.scope["query_string"] = urlencode(out).encode("latin-1")
                    if hasattr(request, "_query_params"):
                        del request._query_params  # force Starlette to re-parse

            # 3) JSON body — decode GUIDs under id-like keys.
            ctype = request.headers.get("content-type", "")
            if ctype.startswith("application/json"):
                body = await request.body()
                if body:
                    try:
                        data = json.loads(body)
                    except ValueError:
                        data = None
                    if data is not None:
                        try:
                            decoded = decode_payload(data)
                        except ValueError:
                            return _not_found()
                        request._body = json.dumps(decoded).encode()  # type: ignore[attr-defined]

            response = await original(request)

            # 4) JSON response — encode ints under id-like keys.
            if (
                response.media_type == "application/json"
                or (response.headers.get("content-type", "").startswith("application/json"))
            ) and getattr(response, "body", None):
                try:
                    data = json.loads(response.body)
                except ValueError:
                    return response
                new_body = json.dumps(encode_payload(data), separators=(",", ":")).encode()
                headers = {
                    k: v for k, v in response.headers.items()
                    if k.lower() not in ("content-length", "content-type")
                }
                return Response(
                    content=new_body,
                    status_code=response.status_code,
                    headers=headers,
                    media_type="application/json",
                    background=response.background,
                )
            return response

        return handler
