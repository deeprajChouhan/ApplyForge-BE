"""Resend webhook signature check (Svix scheme), without the svix dependency.

signed_content = f"{svix_id}.{svix_timestamp}.{raw_body}"
key            = base64decode(secret without "whsec_" prefix)
expected       = base64(HMAC_SHA256(key, signed_content))
header         = "v1,<sig> v1,<sig2> ..." (space-separated, any may match)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

TOLERANCE_SECONDS = 5 * 60


class WebhookVerificationError(Exception):
    pass


def verify(secret: str, *, svix_id: str | None, svix_timestamp: str | None,
           svix_signature: str | None, body: bytes, now: float | None = None) -> None:
    if not secret:
        raise WebhookVerificationError("webhook secret not configured")
    if not (svix_id and svix_timestamp and svix_signature):
        raise WebhookVerificationError("missing svix headers")
    try:
        ts = int(svix_timestamp)
    except ValueError as exc:
        raise WebhookVerificationError("bad timestamp") from exc
    if abs((now or time.time()) - ts) > TOLERANCE_SECONDS:
        raise WebhookVerificationError("timestamp outside tolerance")

    raw_secret = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw_secret)
    except Exception as exc:  # noqa: BLE001
        raise WebhookVerificationError("bad secret encoding") from exc

    signed = f"{svix_id}.{svix_timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in svix_signature.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return
    raise WebhookVerificationError("signature mismatch")


def sign(secret: str, svix_id: str, svix_timestamp: str, body: bytes) -> str:
    """Test helper: produce a valid svix-signature header value."""
    raw_secret = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    key = base64.b64decode(raw_secret)
    signed = f"{svix_id}.{svix_timestamp}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
