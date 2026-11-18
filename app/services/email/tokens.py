"""Signed tokens used in email links (password reset, unsubscribe).

Password reset tokens embed a fingerprint of the current password hash, so a
token stops working as soon as the password changes (single use) without a
token table. Unsubscribe tokens don't expire (links in old emails must work).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.core.config import settings

RESET_TTL = timedelta(minutes=60)


class TokenError(Exception):
    pass


def _fp(password_hash: str | None) -> str:
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:24]


def make_reset_token(kind: str, subject_id: int, password_hash: str | None) -> str:
    """kind: 'user' (consumer) or 'recruiter'."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject_id),
        "type": f"pwreset_{kind}",
        "fp": _fp(password_hash),
        "iat": int(now.timestamp()),
        "exp": int((now + RESET_TTL).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def read_reset_token(kind: str, token: str) -> tuple[int, str]:
    """Returns (subject_id, fingerprint). Caller must compare fp to the live hash."""
    try:
        p = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise TokenError("This reset link is invalid or has expired.") from exc
    if p.get("type") != f"pwreset_{kind}":
        raise TokenError("This reset link is invalid or has expired.")
    return int(p["sub"]), p.get("fp", "")


def reset_fp_matches(fp: str, password_hash: str | None) -> bool:
    return fp == _fp(password_hash)


def make_unsub_token(scope: str, subject_id: int, category: str) -> str:
    payload = {"sub": f"{scope}:{subject_id}", "type": "email_unsub", "cat": category}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def read_unsub_token(token: str) -> tuple[str, int, str]:
    try:
        p = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm],
                       options={"verify_exp": False})
    except JWTError as exc:
        raise TokenError("Invalid unsubscribe link.") from exc
    if p.get("type") != "email_unsub":
        raise TokenError("Invalid unsubscribe link.")
    scope, _, sid = str(p.get("sub", "")).partition(":")
    if scope not in ("consumer", "recruiter") or not sid.isdigit():
        raise TokenError("Invalid unsubscribe link.")
    return scope, int(sid), p.get("cat", "lifecycle")
