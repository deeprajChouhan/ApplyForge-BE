"""
Email transport abstraction — same shape as LLMProvider / EmbeddingProvider.

  ResendEmailProvider   production (https://api.resend.com)
  ConsoleEmailProvider  dev / tests: logs instead of sending (EMAIL_PROVIDER=console)

Swapping to another provider (e.g. Azure Communication Services) is a new
class here; no call site changes.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class OutgoingEmail:
    from_addr: str
    to: list[str]
    subject: str
    html: str
    text: str | None = None
    reply_to: list[str] | None = None
    cc: list[str] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    attachments: list[dict[str, Any]] | None = None  # [{filename, content(base64)}]
    idempotency_key: str | None = None


class EmailProvider(Protocol):
    name: str

    def send(self, email: OutgoingEmail) -> str: ...

    def get_received(self, email_id: str) -> dict: ...

    def list_received_attachments(self, email_id: str) -> list[dict]: ...


def _clean_tag(v: str) -> str:
    return "".join(c if (c.isalnum() or c in "_-") else "_" for c in str(v))[:256] or "na"


class ResendEmailProvider:
    name = "resend"

    def __init__(self, api_key: str, base_url: str = "https://api.resend.com", timeout: float = 15.0):
        if not api_key:
            raise RuntimeError("RESEND_API_KEY is not set")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": "applyforge-backend"},
        )

    def send(self, email: OutgoingEmail) -> str:
        body: dict[str, Any] = {
            "from": email.from_addr,
            "to": email.to,
            "subject": email.subject,
            "html": email.html,
        }
        if email.text is not None:
            body["text"] = email.text
        if email.reply_to:
            body["reply_to"] = email.reply_to
        if email.cc:
            body["cc"] = email.cc
        if email.headers:
            body["headers"] = email.headers
        if email.tags:
            body["tags"] = [{"name": _clean_tag(k), "value": _clean_tag(v)} for k, v in email.tags.items()]
        if email.attachments:
            body["attachments"] = email.attachments
        headers = {"Idempotency-Key": email.idempotency_key[:256]} if email.idempotency_key else {}
        try:
            res = self._client.post("/emails", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise EmailSendError(f"resend transport error: {exc}") from exc
        if res.status_code >= 400:
            # 4xx (bad address, unverified domain) won't fix itself; 429/5xx will.
            retryable = res.status_code == 429 or res.status_code >= 500
            raise EmailSendError(f"resend {res.status_code}: {res.text[:500]}", retryable=retryable)
        return res.json().get("id", "")

    def get_received(self, email_id: str) -> dict:
        res = self._client.get(f"/emails/receiving/{email_id}")
        res.raise_for_status()
        return res.json()

    def list_received_attachments(self, email_id: str) -> list[dict]:
        res = self._client.get(f"/emails/receiving/{email_id}/attachments")
        res.raise_for_status()
        return res.json().get("data", [])

    def download(self, url: str) -> bytes:
        # Signed URL — no auth header needed, but a plain client keeps it simple.
        res = httpx.get(url, timeout=30.0)
        res.raise_for_status()
        return res.content


class ConsoleEmailProvider:
    """Logs emails instead of sending. Keeps the last 200 in memory for tests."""

    name = "console"
    sent: list[OutgoingEmail] = []

    def send(self, email: OutgoingEmail) -> str:
        ConsoleEmailProvider.sent.append(email)
        del ConsoleEmailProvider.sent[:-200]
        logger.info("email.console_send to=%s subject=%r", email.to, email.subject)
        return f"console-{uuid.uuid4()}"

    def get_received(self, email_id: str) -> dict:
        return {}

    def list_received_attachments(self, email_id: str) -> list[dict]:
        return []

    def download(self, url: str) -> bytes:
        return b""


@lru_cache(maxsize=1)
def get_email_provider() -> EmailProvider:
    if settings.email_provider == "resend" and settings.resend_api_key_value:
        return ResendEmailProvider(settings.resend_api_key_value, settings.resend_api_base)
    if settings.email_provider == "resend":
        logger.warning("EMAIL_PROVIDER=resend but RESEND_API_KEY is empty — falling back to console")
    return ConsoleEmailProvider()
