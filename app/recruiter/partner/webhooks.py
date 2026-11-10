"""
Outbound webhooks.

Design rules (no data leakage):
  * payloads are THIN — event type, ids (opaque GUIDs) and state only, never
    names, emails, CV text or compensation. Receivers fetch details through
    the authenticated API, so a leaked webhook body exposes nothing personal;
  * client-scoped endpoints only receive events for that client's roles;
  * every request is signed: `ApplyForge-Signature: t=<unix>,v1=<hex>` where
    v1 = HMAC-SHA256(secret, f"{t}.{raw_body}");
  * targets must be https and resolve only to public IPs (SSRF guard),
    re-checked at delivery time; redirects are never followed;
  * retries back off over ~24h; an endpoint is auto-disabled after
    `AUTO_DISABLE_AFTER` consecutive failures.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.config import settings
from app.recruiter.ids import encode_payload
from app.recruiter.models import WebhookDelivery, WebhookEndpoint
from app.recruiter.partner import crypto

logger = logging.getLogger(__name__)

EVENTS: dict[str, str] = {
    "job.created": "A job was created (app or API)",
    "job.updated": "A job's details or status changed",
    "job.closed": "A job was closed, filled or put on hold",
    "application.created": "A candidate entered a job's pipeline",
    "application.processed": "CV parsing + fit scoring finished for an application",
    "application.stage_changed": "An application moved pipeline stage",
    "candidate.deleted": "A candidate was erased",
    "shortlist.completed": "AI matching produced a new shortlist for a job",
}

RETRY_SCHEDULE_SECONDS = [60, 300, 900, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600]
AUTO_DISABLE_AFTER = 20
DELIVERY_TIMEOUT_SECONDS = 5.0
MAX_BATCH = 50


class WebhookUrlRejected(ValueError):
    pass


# ── Secrets ─────────────────────────────────────────────────────────────
def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


# ── SSRF guard ──────────────────────────────────────────────────────────
def _ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    return bool(ip.is_global) and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local)


def validate_url(url: str, *, resolve: bool = True) -> str:
    url = (url or "").strip()
    if len(url) > 500:
        raise WebhookUrlRejected("URL is too long.")
    parts = urlsplit(url)
    allow_insecure = settings.partner_webhook_allow_insecure
    if parts.scheme != "https" and not (allow_insecure and parts.scheme == "http"):
        raise WebhookUrlRejected("Webhook URLs must use https.")
    if parts.username or parts.password:
        raise WebhookUrlRejected("Webhook URLs must not contain credentials.")
    host = parts.hostname
    if not host:
        raise WebhookUrlRejected("Webhook URL has no host.")
    if parts.fragment:
        raise WebhookUrlRejected("Webhook URLs must not contain a fragment.")
    if allow_insecure:
        return url
    lowered = host.lower()
    if lowered in ("localhost",) or lowered.endswith((".local", ".internal", ".localhost")):
        raise WebhookUrlRejected("Webhook URL must be publicly reachable.")
    try:
        literal = ipaddress.ip_address(host)
        if not _ip_is_public(literal):
            raise WebhookUrlRejected("Webhook URL must be publicly reachable.")
        return url
    except ValueError:
        pass
    if resolve:
        try:
            infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            raise WebhookUrlRejected("Webhook host could not be resolved.")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not _ip_is_public(ip):
                raise WebhookUrlRejected("Webhook URL must be publicly reachable.")
    return url


# ── Emit ────────────────────────────────────────────────────────────────
def _client_endpoint_may_receive(
    event: str, endpoint_client_id: int, role_client_id: int | None, intake_client_id: int | None
) -> bool:
    if event.startswith("job."):
        return role_client_id == endpoint_client_id
    if event.startswith("application."):
        return intake_client_id is not None and intake_client_id == endpoint_client_id
    return False


def emit(
    db: Session,
    agency_id: int,
    event: str,
    data: dict,
    *,
    client_id: int | None = None,
    intake_client_id: int | None = None,
) -> int:
    """
    Queue `event` for every active endpoint subscribed to it. Caller commits.
    `data` must contain ids/state only. Never raises — webhooks must not
    break the recruiter action that triggered them.

    Client-scoped endpoints see strictly less than agency endpoints:
      * job.*          — only for jobs of that client (`client_id` = role's client);
      * application.*  — only for applicants that client's keys submitted
                         (`intake_client_id`), never agency-sourced candidates;
      * everything else (shortlists, erasures) — never.
    """
    if event not in EVENTS:
        logger.warning("webhook_unknown_event event=%s", event)
        return 0
    try:
        endpoints = (
            db.query(WebhookEndpoint)
            .filter(WebhookEndpoint.agency_id == agency_id, WebhookEndpoint.is_active.is_(True))
            .all()
        )
        queued = 0
        event_id = "evt_" + secrets.token_hex(12)
        payload = {
            "id": event_id,
            "type": event,
            "created_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "data": encode_payload(data),
        }
        for ep in endpoints:
            if event not in (ep.events or []):
                continue
            if ep.client_id is not None and not _client_endpoint_may_receive(
                event, ep.client_id, client_id, intake_client_id
            ):
                continue
            db.add(
                WebhookDelivery(
                    agency_id=agency_id,
                    endpoint_id=ep.id,
                    event_id=event_id,
                    event=event,
                    payload=payload,
                    status="pending",
                    next_attempt_at=datetime.utcnow(),
                )
            )
            queued += 1
        return queued
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("webhook_emit_failed event=%s error=%s", event, exc)
        return 0


# ── Deliver ─────────────────────────────────────────────────────────────
def _resolve_public(host: str, port: int) -> str:
    """Resolve once and return a validated public IP (raises WebhookUrlRejected)."""
    try:
        literal = ipaddress.ip_address(host)
        if not _ip_is_public(literal):
            raise WebhookUrlRejected("Webhook URL must be publicly reachable.")
        return str(literal)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise WebhookUrlRejected("Webhook host could not be resolved.")
    ips = [ipaddress.ip_address(i[4][0]) for i in infos]
    if not ips or any(not _ip_is_public(ip) for ip in ips):
        raise WebhookUrlRejected("Webhook URL must be publicly reachable.")
    return str(ips[0])


def _post(url: str, body: bytes, headers: dict) -> tuple[int | None, str | None]:
    """
    Deliver with the destination IP PINNED: we resolve and validate once, then
    connect to that exact IP (TLS SNI + certificate check still use the real
    hostname). A DNS-rebinding host can't swap in an internal address between
    the check and the connect. Errors are reported coarsely (no port-scan oracle).
    """
    import httpx

    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if settings.partner_webhook_allow_insecure:
        target, extensions = url, {}
    else:
        try:
            validate_url(url, resolve=False)
            ip = _resolve_public(host, port)
        except WebhookUrlRejected:
            return None, "blocked"
        ip_host = f"[{ip}]" if ":" in ip else ip
        netloc = f"{ip_host}:{parts.port}" if parts.port else ip_host
        target = parts._replace(netloc=netloc).geturl()
        extensions = {"sni_hostname": host}
        headers = {**headers, "Host": parts.netloc.rsplit("@", 1)[-1]}
    try:
        with httpx.Client(timeout=DELIVERY_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
            resp = client.post(target, content=body, headers=headers, extensions=extensions)
        return resp.status_code, None
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception:
        return None, "connection_failed"


def deliver_one(db: Session, delivery: WebhookDelivery, *, sender=None) -> bool:
    ep = db.get(WebhookEndpoint, delivery.endpoint_id)
    if ep is None or ep.agency_id != delivery.agency_id:
        delivery.status = "dead"
        delivery.last_error = "endpoint removed"
        return False
    if not ep.is_active:
        delivery.status = "dead"
        delivery.last_error = "endpoint disabled"
        return False
    try:
        secret = crypto.decrypt(ep.secret_encrypted)
    except ValueError:
        delivery.status = "dead"
        delivery.last_error = "signing secret unreadable — rotate the secret"
        return False

    body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode()
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ApplyForge-Webhooks/1.0",
        "ApplyForge-Event": delivery.event,
        "ApplyForge-Delivery": str(delivery.event_id),
        "ApplyForge-Signature": sign(secret, ts, body),
    }
    status_code, err = (sender or _post)(ep.url, body, headers)
    delivery.attempts += 1
    delivery.last_status_code = status_code
    now = datetime.utcnow()
    if status_code is not None and 200 <= status_code < 300:
        delivery.status = "delivered"
        delivery.delivered_at = now
        delivery.next_attempt_at = None
        delivery.last_error = None
        ep.consecutive_failures = 0
        ep.last_success_at = now
        return True

    delivery.last_error = (err or f"HTTP {status_code}")[:300]
    ep.consecutive_failures = (ep.consecutive_failures or 0) + 1
    ep.last_failure_at = now
    if delivery.attempts >= len(RETRY_SCHEDULE_SECONDS) + 1:
        delivery.status = "dead"
        delivery.next_attempt_at = None
    else:
        delivery.status = "failed"
        delivery.next_attempt_at = now + timedelta(
            seconds=RETRY_SCHEDULE_SECONDS[min(delivery.attempts - 1, len(RETRY_SCHEDULE_SECONDS) - 1)]
        )
    if ep.consecutive_failures >= AUTO_DISABLE_AFTER and ep.is_active:
        ep.is_active = False
        ep.disabled_reason = f"Auto-disabled after {ep.consecutive_failures} consecutive failures"
        from app.recruiter.partner.audit import record as audit

        audit(db, ep.agency_id, "system", "webhook.auto_disabled", target=f"webhook:{ep.id}")
    return False


def deliver_pending(db: Session, *, agency_id: int | None = None, limit: int = MAX_BATCH, sender=None) -> int:
    """Attempt every due delivery (optionally for one agency). Commits."""
    q = db.query(WebhookDelivery).filter(
        WebhookDelivery.status.in_(("pending", "failed")),
        WebhookDelivery.next_attempt_at <= datetime.utcnow(),
    )
    if agency_id is not None:
        q = q.filter(WebhookDelivery.agency_id == agency_id)
    sent = 0
    for d in q.order_by(WebhookDelivery.id).limit(limit).all():
        try:
            if deliver_one(db, d, sender=sender):
                sent += 1
            db.commit()
        except Exception as exc:  # pragma: no cover - defensive
            db.rollback()
            logger.error("webhook_delivery_crashed id=%s error=%s", d.id, exc)
    return sent
