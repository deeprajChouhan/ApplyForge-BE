"""Symmetric encryption for secrets we must be able to read back (webhook signing secrets)."""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    material = (
        settings.partner_key_pepper.get_secret_value() if settings.partner_key_pepper else ""
    ) or f"partner-webhook-secret::{settings.secret_key}"
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # key changed or row tampered with
        raise ValueError("Unable to decrypt secret") from exc
