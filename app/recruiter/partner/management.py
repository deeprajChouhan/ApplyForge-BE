"""
Managing the Partner API from the admin panels.

Two routers, one service layer:
  * owner_router   — /api/v1/recruiter/agency/integrations/*  (agency owner,
    agency derived from the token — no agency id in the path, so an owner can
    never reach another tenant)
  * operator_router — /api/v1/recruiter/admin/integrations/*  (platform
    operator via require_admin; oversight + kill switches across agencies)

Secrets: a key's plaintext and a webhook's signing secret are returned exactly
once (create / roll / rotate). Lists only ever show prefixes and last-4.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.models import User
from app.recruiter.api.deps import require_owner
from app.recruiter.ids import PublicIdRoute
from app.recruiter.models import (
    Agency,
    ApiKey,
    ApiRequestLog,
    Client,
    IntegrationAuditEvent,
    Recruiter,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.recruiter.partner import crypto, webhooks
from app.recruiter.partner import keys as keys_mod
from app.recruiter.partner.audit import record as audit
from app.recruiter.partner.deps import api_enabled_for, effective_rate_limit

MAX_ACTIVE_KEYS = 25
MAX_WEBHOOKS = 10
MAX_RATE_LIMIT = 10_000
_ORIGIN_RE = re.compile(r"^https://[a-z0-9.-]+(:\d{2,5})?$")
_DEV_ORIGIN_RE = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d{2,5})?$")


# ── Schemas ─────────────────────────────────────────────────────────────
class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["secret", "publishable"] = "secret"
    environment: Literal["live", "test"] = "live"
    scopes: list[str] | None = None
    client_id: int | None = None
    allowed_origins: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(default=None, ge=1, le=730)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=MAX_RATE_LIMIT)


class ApiKeyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scopes: list[str] | None = None
    allowed_origins: list[str] | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=MAX_RATE_LIMIT)


class ApiKeyOut(BaseModel):
    id: int
    name: str
    kind: str
    environment: str
    key_prefix: str
    last4: str
    scopes: list[str]
    client_id: int | None = None
    client_name: str | None = None
    allowed_origins: list[str]
    rate_limit_per_minute: int | None = None
    effective_rate_limit: int
    created_by: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    last_used_ip: str | None = None
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    active: bool


class ApiKeyCreated(ApiKeyOut):
    secret: str  # shown exactly once


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=255)


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=500)
    events: list[str] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=255)
    client_id: int | None = None


class WebhookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = Field(default=None, min_length=8, max_length=500)
    events: list[str] | None = None
    description: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class WebhookOut(BaseModel):
    id: int
    url: str
    description: str | None = None
    events: list[str]
    client_id: int | None = None
    is_active: bool
    disabled_reason: str | None = None
    secret_last4: str
    consecutive_failures: int
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    created_at: datetime | None = None


class WebhookCreated(WebhookOut):
    secret: str  # shown exactly once


class DeliveryOut(BaseModel):
    id: int
    endpoint_id: int
    event_id: str
    event: str
    status: str
    attempts: int
    last_status_code: int | None = None
    last_error: str | None = None
    next_attempt_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime | None = None


class RequestLogOut(BaseModel):
    id: int
    key_prefix: str | None = None
    method: str
    path: str
    status_code: int
    duration_ms: int
    ip: str | None = None
    origin: str | None = None
    created_at: datetime | None = None


class AuditOut(BaseModel):
    id: int
    actor: str
    action: str
    target: str | None = None
    detail: dict | None = None
    created_at: datetime | None = None


class IntegrationOverview(BaseModel):
    agency_id: int
    api_enabled: bool
    access_mode: str
    plan: str
    base_url: str
    public_job_feed_enabled: bool
    public_job_feed_url: str | None = None
    active_keys: int
    active_webhooks: int
    requests_24h: int
    errors_24h: int
    pending_deliveries: int
    failed_deliveries_24h: int
    default_rate_limit: int
    scopes: dict[str, str]
    publishable_scopes: list[str]
    client_scoped_max: list[str]
    events: dict[str, str]
    captcha_configured: bool = False


class IntegrationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_job_feed_enabled: bool | None = None


class OperatorAgencyRow(BaseModel):
    agency_id: int
    agency_name: str
    plan: str
    status: str
    access_mode: str
    api_enabled: bool
    rate_limit_override: int | None = None
    active_keys: int
    active_webhooks: int
    requests_24h: int
    errors_24h: int
    last_used_at: datetime | None = None


class OperatorAgencyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    partner_api_access: Literal["plan", "enabled", "disabled"] | None = None
    partner_api_rate_limit: int | None = Field(default=None, ge=1, le=MAX_RATE_LIMIT)
    clear_rate_limit: bool = False


# ── Service helpers ─────────────────────────────────────────────────────
def _bad(msg: str, code: int = 422) -> HTTPException:
    return HTTPException(status_code=code, detail=msg)


def _base_url() -> str:
    return "/api/v1/recruiter/partner/v1"


def _validate_origins(origins: list[str], environment: str) -> list[str]:
    out: list[str] = []
    for o in origins or []:
        o = (o or "").strip().rstrip("/").lower()
        if not o:
            continue
        if not (_ORIGIN_RE.match(o) or (environment == keys_mod.ENV_TEST and _DEV_ORIGIN_RE.match(o))):
            raise _bad(
                f"Invalid origin '{o[:80]}'. Use https://host[:port] (no path, no wildcard). "
                "http://localhost is allowed for test keys only."
            )
        parts = urlsplit(o)
        if parts.path or parts.query:
            raise _bad(f"Origin '{o[:80]}' must not contain a path.")
        if o not in out:
            out.append(o)
    if len(out) > 20:
        raise _bad("At most 20 allowed origins per key.")
    return out


def _validate_scopes(requested: list[str] | None, kind: str, client_scoped: bool) -> list[str]:
    allowed = keys_mod.allowed_scopes(kind, client_scoped)
    if requested is None:
        if kind == keys_mod.KIND_PUBLISHABLE:
            return sorted(keys_mod.PUBLISHABLE_SCOPES)
        return keys_mod.DEFAULT_CLIENT_SCOPES if client_scoped else keys_mod.DEFAULT_SECRET_SCOPES
    req = {s.strip() for s in requested if s and s.strip()}
    unknown = req - keys_mod.ALL_SCOPES
    if unknown:
        raise _bad(f"Unknown scopes: {', '.join(sorted(unknown))}")
    not_allowed = req - allowed
    if not_allowed:
        what = "a publishable key" if kind == keys_mod.KIND_PUBLISHABLE else (
            "a client-scoped key" if client_scoped else "this key"
        )
        raise _bad(f"Scopes not allowed for {what}: {', '.join(sorted(not_allowed))}")
    if not req:
        raise _bad("Select at least one scope.")
    return sorted(req)


def _client_name(db: Session, client_id: int | None) -> str | None:
    if client_id is None:
        return None
    c = db.get(Client, client_id)
    return c.name if c else None


def key_out(db: Session, agency: Agency, k: ApiKey) -> ApiKeyOut:
    now = datetime.utcnow()
    return ApiKeyOut(
        id=k.id, name=k.name, kind=k.kind, environment=k.environment, key_prefix=k.key_prefix,
        last4=k.last4, scopes=list(k.scopes or []), client_id=k.client_id,
        client_name=_client_name(db, k.client_id), allowed_origins=list(k.allowed_origins or []),
        rate_limit_per_minute=k.rate_limit_per_minute, effective_rate_limit=effective_rate_limit(agency, k),
        created_by=k.created_by_label, created_at=k.created_at, expires_at=k.expires_at,
        last_used_at=k.last_used_at, last_used_ip=k.last_used_ip, revoked_at=k.revoked_at,
        revoked_reason=k.revoked_reason,
        active=k.revoked_at is None and (k.expires_at is None or k.expires_at > now),
    )


def webhook_out(ep: WebhookEndpoint) -> WebhookOut:
    return WebhookOut(
        id=ep.id, url=ep.url, description=ep.description, events=list(ep.events or []),
        client_id=ep.client_id, is_active=ep.is_active, disabled_reason=ep.disabled_reason,
        secret_last4=ep.secret_last4, consecutive_failures=ep.consecutive_failures or 0,
        last_success_at=ep.last_success_at, last_failure_at=ep.last_failure_at, created_at=ep.created_at,
    )


def create_key(
    db: Session, agency: Agency, payload: ApiKeyCreate, *, actor: str,
    recruiter_id: int | None, operator: bool,
) -> ApiKeyCreated:
    active = (
        db.query(func.count(ApiKey.id))
        .filter(ApiKey.agency_id == agency.id, ApiKey.revoked_at.is_(None))
        .scalar() or 0
    )
    if active >= MAX_ACTIVE_KEYS:
        raise _bad(f"An agency can have at most {MAX_ACTIVE_KEYS} active keys. Revoke unused keys first.", 409)
    if payload.client_id is not None:
        c = db.get(Client, payload.client_id)
        if c is None or c.agency_id != agency.id:
            raise _bad("Client not found.", 404)
    client_scoped = payload.client_id is not None
    scopes = _validate_scopes(payload.scopes, payload.kind, client_scoped)
    origins = _validate_origins(payload.allowed_origins, payload.environment)
    if payload.kind == keys_mod.KIND_PUBLISHABLE and not origins:
        raise _bad("Publishable keys need at least one allowed origin (your careers-site domain).")
    if payload.kind == keys_mod.KIND_SECRET and origins:
        raise _bad("Secret keys are server-side only and can't have browser origins.")
    if payload.rate_limit_per_minute and not operator:
        ceiling = agency.partner_api_rate_limit or (
            settings.partner_rate_limit_publishable if payload.kind == keys_mod.KIND_PUBLISHABLE
            else settings.partner_rate_limit_secret
        )
        if payload.rate_limit_per_minute > ceiling:
            raise _bad(f"Rate limit can't exceed your plan's {ceiling}/min. Ask support to raise it.")

    full, prefix, last4 = keys_mod.generate_key(payload.kind, payload.environment)
    k = ApiKey(
        agency_id=agency.id, client_id=payload.client_id, name=payload.name.strip(), kind=payload.kind,
        environment=payload.environment, key_prefix=prefix, key_hash=keys_mod.hash_key(full), last4=last4,
        scopes=scopes, allowed_origins=origins, rate_limit_per_minute=payload.rate_limit_per_minute,
        created_by_recruiter_id=recruiter_id, created_by_label=actor[:255],
        expires_at=(datetime.utcnow() + timedelta(days=payload.expires_in_days)) if payload.expires_in_days else None,
    )
    db.add(k)
    db.flush()
    audit(db, agency.id, actor, "api_key.created", target=f"key:{prefix}",
          detail={"kind": k.kind, "environment": k.environment, "scopes": scopes, "client_id": k.client_id})
    db.commit()
    db.refresh(k)
    return ApiKeyCreated(**key_out(db, agency, k).model_dump(), secret=full)


def _load_key(db: Session, agency_id: int, key_id: int) -> ApiKey:
    k = db.get(ApiKey, key_id)
    if k is None or k.agency_id != agency_id:
        raise _bad("API key not found.", 404)
    return k


def revoke_key(db: Session, agency: Agency, key_id: int, reason: str | None, actor: str) -> ApiKeyOut:
    k = _load_key(db, agency.id, key_id)
    if k.revoked_at is None:
        k.revoked_at = datetime.utcnow()
        k.revoked_reason = (reason or None) and reason[:255]
        audit(db, agency.id, actor, "api_key.revoked", target=f"key:{k.key_prefix}", detail={"reason": reason})
        db.commit()
        db.refresh(k)
    return key_out(db, agency, k)


def roll_key(db: Session, agency: Agency, key_id: int, actor: str, recruiter_id: int | None, operator: bool) -> ApiKeyCreated:
    old = _load_key(db, agency.id, key_id)
    if old.revoked_at is not None:
        raise _bad("This key is already revoked.", 409)
    created = create_key(
        db, agency,
        ApiKeyCreate(
            name=old.name, kind=old.kind, environment=old.environment, scopes=list(old.scopes or []),
            client_id=old.client_id, allowed_origins=list(old.allowed_origins or []),
            rate_limit_per_minute=old.rate_limit_per_minute,
        ),
        actor=actor, recruiter_id=recruiter_id, operator=True,  # copies an already-approved config
    )
    revoke_key(db, agency, key_id, f"rolled → {created.key_prefix}", actor)
    return created


def update_key(db: Session, agency: Agency, key_id: int, payload: ApiKeyUpdate, actor: str, operator: bool) -> ApiKeyOut:
    k = _load_key(db, agency.id, key_id)
    if k.revoked_at is not None:
        raise _bad("Revoked keys can't be edited.", 409)
    changes: dict = {}
    if payload.name is not None:
        k.name = payload.name.strip()
        changes["name"] = k.name
    if payload.scopes is not None:
        k.scopes = _validate_scopes(payload.scopes, k.kind, k.client_id is not None)
        changes["scopes"] = k.scopes
    if payload.allowed_origins is not None:
        origins = _validate_origins(payload.allowed_origins, k.environment)
        if k.kind == keys_mod.KIND_PUBLISHABLE and not origins:
            raise _bad("Publishable keys need at least one allowed origin.")
        if k.kind == keys_mod.KIND_SECRET and origins:
            raise _bad("Secret keys can't have browser origins.")
        k.allowed_origins = origins
        changes["allowed_origins"] = origins
    if payload.rate_limit_per_minute is not None:
        if not operator:
            ceiling = agency.partner_api_rate_limit or (
                settings.partner_rate_limit_publishable if k.kind == keys_mod.KIND_PUBLISHABLE
                else settings.partner_rate_limit_secret
            )
            if payload.rate_limit_per_minute > ceiling:
                raise _bad(f"Rate limit can't exceed your plan's {ceiling}/min.")
        k.rate_limit_per_minute = payload.rate_limit_per_minute
        changes["rate_limit_per_minute"] = k.rate_limit_per_minute
    if changes:
        audit(db, agency.id, actor, "api_key.updated", target=f"key:{k.key_prefix}", detail=changes)
    db.commit()
    db.refresh(k)
    return key_out(db, agency, k)


def _validate_events(events: list[str]) -> list[str]:
    ev = sorted({e.strip() for e in events if e and e.strip()})
    unknown = [e for e in ev if e not in webhooks.EVENTS]
    if unknown:
        raise _bad(f"Unknown events: {', '.join(unknown)}")
    if not ev:
        raise _bad("Subscribe to at least one event.")
    return ev


def create_webhook(db: Session, agency: Agency, payload: WebhookCreate, actor: str) -> WebhookCreated:
    n = db.query(func.count(WebhookEndpoint.id)).filter(WebhookEndpoint.agency_id == agency.id).scalar() or 0
    if n >= MAX_WEBHOOKS:
        raise _bad(f"At most {MAX_WEBHOOKS} webhook endpoints per agency.", 409)
    try:
        url = webhooks.validate_url(payload.url)
    except webhooks.WebhookUrlRejected as exc:
        raise _bad(str(exc))
    if payload.client_id is not None:
        c = db.get(Client, payload.client_id)
        if c is None or c.agency_id != agency.id:
            raise _bad("Client not found.", 404)
    secret = webhooks.new_secret()
    ep = WebhookEndpoint(
        agency_id=agency.id, client_id=payload.client_id, url=url, description=payload.description,
        events=_validate_events(payload.events), secret_encrypted=crypto.encrypt(secret),
        secret_last4=secret[-4:], is_active=True,
    )
    db.add(ep)
    db.flush()
    audit(db, agency.id, actor, "webhook.created", target=f"webhook:{ep.id}",
          detail={"url_host": urlsplit(url).hostname, "events": ep.events})
    db.commit()
    db.refresh(ep)
    return WebhookCreated(**webhook_out(ep).model_dump(), secret=secret)


def _load_webhook(db: Session, agency_id: int, webhook_id: int) -> WebhookEndpoint:
    ep = db.get(WebhookEndpoint, webhook_id)
    if ep is None or ep.agency_id != agency_id:
        raise _bad("Webhook not found.", 404)
    return ep


def update_webhook(db: Session, agency: Agency, webhook_id: int, payload: WebhookUpdate, actor: str) -> WebhookOut:
    ep = _load_webhook(db, agency.id, webhook_id)
    changes: dict = {}
    if payload.url is not None:
        try:
            ep.url = webhooks.validate_url(payload.url)
        except webhooks.WebhookUrlRejected as exc:
            raise _bad(str(exc))
        changes["url_host"] = urlsplit(ep.url).hostname
    if payload.events is not None:
        ep.events = _validate_events(payload.events)
        changes["events"] = ep.events
    if payload.description is not None:
        ep.description = payload.description
    if payload.is_active is not None:
        ep.is_active = payload.is_active
        if payload.is_active:
            ep.consecutive_failures = 0
            ep.disabled_reason = None
        else:
            ep.disabled_reason = f"Disabled by {actor}"[:255]
        changes["is_active"] = payload.is_active
    audit(db, agency.id, actor, "webhook.updated", target=f"webhook:{ep.id}", detail=changes)
    db.commit()
    db.refresh(ep)
    return webhook_out(ep)


def rotate_webhook_secret(db: Session, agency: Agency, webhook_id: int, actor: str) -> WebhookCreated:
    ep = _load_webhook(db, agency.id, webhook_id)
    secret = webhooks.new_secret()
    ep.secret_encrypted = crypto.encrypt(secret)
    ep.secret_last4 = secret[-4:]
    audit(db, agency.id, actor, "webhook.secret_rotated", target=f"webhook:{ep.id}")
    db.commit()
    db.refresh(ep)
    return WebhookCreated(**webhook_out(ep).model_dump(), secret=secret)


def delete_webhook(db: Session, agency: Agency, webhook_id: int, actor: str) -> None:
    ep = _load_webhook(db, agency.id, webhook_id)
    db.query(WebhookDelivery).filter(WebhookDelivery.endpoint_id == ep.id).delete()
    db.delete(ep)
    audit(db, agency.id, actor, "webhook.deleted", target=f"webhook:{webhook_id}")
    db.commit()


def send_test_ping(db: Session, agency: Agency, webhook_id: int, actor: str) -> DeliveryOut:
    """Send a signed `ping` right now and report the result."""
    import secrets as _secrets

    ep = _load_webhook(db, agency.id, webhook_id)
    if not ep.is_active:
        raise _bad("Enable the webhook before testing it.", 409)
    d = WebhookDelivery(
        agency_id=agency.id, endpoint_id=ep.id, event_id="evt_" + _secrets.token_hex(12), event="ping",
        payload={"id": "ping", "type": "ping", "created_at": datetime.utcnow().isoformat() + "Z", "data": {}},
        status="pending", next_attempt_at=datetime.utcnow(),
    )
    db.add(d)
    db.flush()
    webhooks.deliver_one(db, d)
    if d.status != "delivered":
        d.status = "dead"  # pings are never retried
        d.next_attempt_at = None
    audit(db, agency.id, actor, "webhook.tested", target=f"webhook:{ep.id}", detail={"status": d.status})
    db.commit()
    db.refresh(d)
    return DeliveryOut.model_validate(d, from_attributes=True)


def list_deliveries(db: Session, agency_id: int, webhook_id: int | None, limit: int, status_filter: str | None):
    q = db.query(WebhookDelivery).filter(WebhookDelivery.agency_id == agency_id)
    if webhook_id is not None:
        q = q.filter(WebhookDelivery.endpoint_id == webhook_id)
    if status_filter:
        q = q.filter(WebhookDelivery.status == status_filter)
    return [DeliveryOut.model_validate(d, from_attributes=True)
            for d in q.order_by(WebhookDelivery.id.desc()).limit(limit).all()]


def retry_delivery(db: Session, agency: Agency, delivery_id: int, actor: str) -> DeliveryOut:
    d = db.get(WebhookDelivery, delivery_id)
    if d is None or d.agency_id != agency.id:
        raise _bad("Delivery not found.", 404)
    if d.status == "delivered":
        raise _bad("Already delivered.", 409)
    if d.event == "ping":
        raise _bad("Use the test button to send a new ping.", 409)
    d.status = "pending"
    d.next_attempt_at = datetime.utcnow()
    d.attempts = 0
    webhooks.deliver_one(db, d)
    audit(db, agency.id, actor, "webhook.delivery_retried", target=f"delivery:{d.id}", detail={"status": d.status})
    db.commit()
    db.refresh(d)
    return DeliveryOut.model_validate(d, from_attributes=True)


def list_logs(db: Session, agency_id: int, limit: int, errors_only: bool, key_prefix: str | None):
    q = db.query(ApiRequestLog).filter(ApiRequestLog.agency_id == agency_id)
    if errors_only:
        q = q.filter(ApiRequestLog.status_code >= 400)
    if key_prefix:
        q = q.filter(ApiRequestLog.key_prefix == key_prefix[:40])
    return [RequestLogOut.model_validate(r, from_attributes=True)
            for r in q.order_by(ApiRequestLog.id.desc()).limit(limit).all()]


def list_audit(db: Session, agency_id: int, limit: int):
    return [AuditOut.model_validate(a, from_attributes=True)
            for a in db.query(IntegrationAuditEvent).filter(IntegrationAuditEvent.agency_id == agency_id)
            .order_by(IntegrationAuditEvent.id.desc()).limit(limit).all()]


def _stats(db: Session, agency_id: int) -> dict:
    since = datetime.utcnow() - timedelta(hours=24)
    now = datetime.utcnow()
    active_keys = (
        db.query(func.count(ApiKey.id))
        .filter(ApiKey.agency_id == agency_id, ApiKey.revoked_at.is_(None))
        .filter((ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now))
        .scalar() or 0
    )
    active_hooks = (
        db.query(func.count(WebhookEndpoint.id))
        .filter(WebhookEndpoint.agency_id == agency_id, WebhookEndpoint.is_active.is_(True)).scalar() or 0
    )
    req = db.query(func.count(ApiRequestLog.id)).filter(
        ApiRequestLog.agency_id == agency_id, ApiRequestLog.created_at >= since).scalar() or 0
    err = db.query(func.count(ApiRequestLog.id)).filter(
        ApiRequestLog.agency_id == agency_id, ApiRequestLog.created_at >= since,
        ApiRequestLog.status_code >= 400).scalar() or 0
    pending = db.query(func.count(WebhookDelivery.id)).filter(
        WebhookDelivery.agency_id == agency_id, WebhookDelivery.status.in_(("pending", "failed"))).scalar() or 0
    failed = db.query(func.count(WebhookDelivery.id)).filter(
        WebhookDelivery.agency_id == agency_id, WebhookDelivery.status == "dead",
        WebhookDelivery.created_at >= since).scalar() or 0
    last_used = db.query(func.max(ApiKey.last_used_at)).filter(ApiKey.agency_id == agency_id).scalar()
    return {
        "active_keys": int(active_keys), "active_webhooks": int(active_hooks), "requests_24h": int(req),
        "errors_24h": int(err), "pending_deliveries": int(pending), "failed_deliveries_24h": int(failed),
        "last_used_at": last_used,
    }


def overview(db: Session, agency: Agency) -> IntegrationOverview:
    st = _stats(db, agency.id)
    st.pop("last_used_at", None)
    return IntegrationOverview(
        agency_id=agency.id, api_enabled=api_enabled_for(agency), access_mode=agency.partner_api_access or "plan",
        plan=agency.plan.value, base_url=_base_url(), public_job_feed_enabled=bool(agency.public_job_feed_enabled),
        public_job_feed_url=f"{_base_url()}/feeds/{agency.slug}/jobs.xml" if agency.public_job_feed_enabled else None,
        default_rate_limit=agency.partner_api_rate_limit or settings.partner_rate_limit_secret,
        scopes=dict(keys_mod.SCOPE_DESCRIPTIONS), publishable_scopes=sorted(keys_mod.PUBLISHABLE_SCOPES),
        client_scoped_max=sorted(keys_mod.CLIENT_SCOPED_MAX), events=dict(webhooks.EVENTS),
        captcha_configured=bool(settings.partner_turnstile_secret), **st,
    )


# ── Owner router (agency admin) ─────────────────────────────────────────
owner_router = APIRouter(route_class=PublicIdRoute, prefix="/agency/integrations", tags=["recruiter: integrations"])


def _owner_agency(db: Session, owner: Recruiter) -> Agency:
    agency = db.get(Agency, owner.agency_id)
    if agency is None:  # pragma: no cover
        raise _bad("Agency not found.", 404)
    return agency


def _owner_actor(owner: Recruiter) -> str:
    return f"owner:{owner.email}"


@owner_router.get("", response_model=IntegrationOverview)
def owner_overview(owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    return overview(db, _owner_agency(db, owner))


@owner_router.patch("/settings", response_model=IntegrationOverview)
def owner_settings(payload: IntegrationSettingsUpdate, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    agency = _owner_agency(db, owner)
    if payload.public_job_feed_enabled is not None:
        agency.public_job_feed_enabled = payload.public_job_feed_enabled
        audit(db, agency.id, _owner_actor(owner), "settings.job_feed",
              detail={"enabled": payload.public_job_feed_enabled})
    db.commit()
    return overview(db, agency)


@owner_router.get("/api-keys", response_model=list[ApiKeyOut])
def owner_list_keys(
    include_revoked: bool = Query(default=False),
    owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db),
):
    agency = _owner_agency(db, owner)
    q = db.query(ApiKey).filter(ApiKey.agency_id == agency.id)
    if not include_revoked:
        q = q.filter(ApiKey.revoked_at.is_(None))
    return [key_out(db, agency, k) for k in q.order_by(ApiKey.id.desc()).all()]


@owner_router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def owner_create_key(payload: ApiKeyCreate, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    agency = _owner_agency(db, owner)
    if not api_enabled_for(agency):
        raise _bad("The Partner API isn't enabled on your plan. Upgrade or contact support.", 403)
    return create_key(db, agency, payload, actor=_owner_actor(owner), recruiter_id=owner.id, operator=False)


@owner_router.patch("/api-keys/{key_id}", response_model=ApiKeyOut)
def owner_update_key(key_id: int, payload: ApiKeyUpdate, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    return update_key(db, _owner_agency(db, owner), key_id, payload, _owner_actor(owner), operator=False)


@owner_router.post("/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def owner_revoke_key(key_id: int, payload: RevokeIn | None = None, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    return revoke_key(db, _owner_agency(db, owner), key_id, payload.reason if payload else None, _owner_actor(owner))


@owner_router.post("/api-keys/{key_id}/roll", response_model=ApiKeyCreated)
def owner_roll_key(key_id: int, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    return roll_key(db, _owner_agency(db, owner), key_id, _owner_actor(owner), owner.id, operator=False)


@owner_router.get("/webhooks", response_model=list[WebhookOut])
def owner_list_webhooks(owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    agency = _owner_agency(db, owner)
    return [webhook_out(e) for e in db.query(WebhookEndpoint).filter(WebhookEndpoint.agency_id == agency.id)
            .order_by(WebhookEndpoint.id.desc()).all()]


@owner_router.post("/webhooks", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
def owner_create_webhook(payload: WebhookCreate, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    agency = _owner_agency(db, owner)
    if not api_enabled_for(agency):
        raise _bad("The Partner API isn't enabled on your plan.", 403)
    return create_webhook(db, agency, payload, _owner_actor(owner))


@owner_router.patch("/webhooks/{webhook_id}", response_model=WebhookOut)
def owner_update_webhook(webhook_id: int, payload: WebhookUpdate, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    return update_webhook(db, _owner_agency(db, owner), webhook_id, payload, _owner_actor(owner))


@owner_router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def owner_delete_webhook(webhook_id: int, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    delete_webhook(db, _owner_agency(db, owner), webhook_id, _owner_actor(owner))


@owner_router.post("/webhooks/{webhook_id}/rotate-secret", response_model=WebhookCreated)
def owner_rotate_secret(webhook_id: int, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    return rotate_webhook_secret(db, _owner_agency(db, owner), webhook_id, _owner_actor(owner))


@owner_router.post("/webhooks/{webhook_id}/test", response_model=DeliveryOut)
def owner_test_webhook(webhook_id: int, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    return send_test_ping(db, _owner_agency(db, owner), webhook_id, _owner_actor(owner))


@owner_router.get("/webhook-deliveries", response_model=list[DeliveryOut])
def owner_deliveries(
    webhook_id: int | None = None,
    status_filter: Literal["pending", "delivered", "failed", "dead"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db),
):
    return list_deliveries(db, owner.agency_id, webhook_id, limit, status_filter)


@owner_router.post("/webhook-deliveries/{delivery_id}/retry", response_model=DeliveryOut)
def owner_retry_delivery(delivery_id: int, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    return retry_delivery(db, _owner_agency(db, owner), delivery_id, _owner_actor(owner))


@owner_router.get("/request-logs", response_model=list[RequestLogOut])
def owner_logs(
    errors_only: bool = False, key_prefix: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
    owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db),
):
    return list_logs(db, owner.agency_id, limit, errors_only, key_prefix)


@owner_router.get("/audit", response_model=list[AuditOut])
def owner_audit(limit: int = Query(default=100, ge=1, le=500), owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    return list_audit(db, owner.agency_id, limit)


# ── Operator router (platform admin) ────────────────────────────────────
operator_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/admin/integrations",
    tags=["recruiter: admin integrations"],
    dependencies=[Depends(require_admin)],
)


def _operator_actor(user: User) -> str:
    return f"operator:{user.email}"


def _agency_or_404(db: Session, agency_id: int) -> Agency:
    a = db.get(Agency, agency_id)
    if a is None:
        raise _bad("Agency not found.", 404)
    return a


@operator_router.get("/agencies", response_model=list[OperatorAgencyRow])
def operator_agencies(db: Session = Depends(get_db)):
    rows = []
    for a in db.query(Agency).order_by(Agency.name).all():
        st = _stats(db, a.id)
        rows.append(OperatorAgencyRow(
            agency_id=a.id, agency_name=a.name, plan=a.plan.value, status=a.status.value,
            access_mode=a.partner_api_access or "plan", api_enabled=api_enabled_for(a),
            rate_limit_override=a.partner_api_rate_limit, active_keys=st["active_keys"],
            active_webhooks=st["active_webhooks"], requests_24h=st["requests_24h"],
            errors_24h=st["errors_24h"], last_used_at=st["last_used_at"],
        ))
    return rows


@operator_router.get("/agencies/{agency_id}", response_model=IntegrationOverview)
def operator_agency_overview(agency_id: int, db: Session = Depends(get_db)):
    return overview(db, _agency_or_404(db, agency_id))


@operator_router.patch("/agencies/{agency_id}", response_model=IntegrationOverview)
def operator_update_agency(
    agency_id: int, payload: OperatorAgencyUpdate,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    a = _agency_or_404(db, agency_id)
    changes: dict = {}
    if payload.partner_api_access is not None:
        a.partner_api_access = payload.partner_api_access
        changes["access"] = payload.partner_api_access
    if payload.clear_rate_limit:
        a.partner_api_rate_limit = None
        changes["rate_limit"] = None
    elif payload.partner_api_rate_limit is not None:
        a.partner_api_rate_limit = payload.partner_api_rate_limit
        changes["rate_limit"] = payload.partner_api_rate_limit
    audit(db, a.id, _operator_actor(user), "operator.agency_api_updated", detail=changes)
    db.commit()
    return overview(db, a)


@operator_router.get("/agencies/{agency_id}/api-keys", response_model=list[ApiKeyOut])
def operator_keys(agency_id: int, db: Session = Depends(get_db)):
    a = _agency_or_404(db, agency_id)
    return [key_out(db, a, k) for k in db.query(ApiKey).filter(ApiKey.agency_id == a.id).order_by(ApiKey.id.desc()).all()]


# NOTE: operators deliberately cannot CREATE keys. A key minted by an operator
# would put a working secret for the agency's candidate data in operator hands;
# keys are created only by the agency owner, who is the data controller.


class OperatorKeyLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_limit_per_minute: int = Field(ge=1, le=MAX_RATE_LIMIT)


@operator_router.patch("/agencies/{agency_id}/api-keys/{key_id}", response_model=ApiKeyOut)
def operator_update_key(
    agency_id: int, key_id: int, payload: OperatorKeyLimit,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Operators can tune a key's rate limit (or revoke it) — never widen its scopes or origins."""
    return update_key(
        db, _agency_or_404(db, agency_id), key_id,
        ApiKeyUpdate(rate_limit_per_minute=payload.rate_limit_per_minute), _operator_actor(user), operator=True,
    )


@operator_router.post("/agencies/{agency_id}/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def operator_revoke_key(agency_id: int, key_id: int, payload: RevokeIn | None = None, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return revoke_key(db, _agency_or_404(db, agency_id), key_id, payload.reason if payload else "revoked by operator", _operator_actor(user))


@operator_router.get("/agencies/{agency_id}/webhooks", response_model=list[WebhookOut])
def operator_webhooks(agency_id: int, db: Session = Depends(get_db)):
    a = _agency_or_404(db, agency_id)
    return [webhook_out(e) for e in db.query(WebhookEndpoint).filter(WebhookEndpoint.agency_id == a.id)
            .order_by(WebhookEndpoint.id.desc()).all()]


class OperatorWebhookToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


@operator_router.patch("/agencies/{agency_id}/webhooks/{webhook_id}", response_model=WebhookOut)
def operator_update_webhook(
    agency_id: int, webhook_id: int, payload: OperatorWebhookToggle,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Operators may only enable/disable an endpoint — never repoint its URL or events
    (that would let an operator redirect an agency's event stream)."""
    return update_webhook(
        db, _agency_or_404(db, agency_id), webhook_id, WebhookUpdate(is_active=payload.is_active), _operator_actor(user)
    )


@operator_router.get("/agencies/{agency_id}/webhook-deliveries", response_model=list[DeliveryOut])
def operator_deliveries(
    agency_id: int, webhook_id: int | None = None,
    status_filter: Literal["pending", "delivered", "failed", "dead"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db),
):
    _agency_or_404(db, agency_id)
    return list_deliveries(db, agency_id, webhook_id, limit, status_filter)


@operator_router.get("/agencies/{agency_id}/request-logs", response_model=list[RequestLogOut])
def operator_logs(
    agency_id: int, errors_only: bool = False, key_prefix: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db),
):
    _agency_or_404(db, agency_id)
    return list_logs(db, agency_id, limit, errors_only, key_prefix)


@operator_router.get("/agencies/{agency_id}/audit", response_model=list[AuditOut])
def operator_audit(agency_id: int, limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    _agency_or_404(db, agency_id)
    return list_audit(db, agency_id, limit)


@operator_router.post("/maintenance/backfill-cv-files")
def operator_backfill_cv_files(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Move CVs saved by the old ingestion writer into secure storage. Idempotent."""
    from app.recruiter.services.legacy_files import backfill_legacy_cv_files

    result = backfill_legacy_cv_files(db)
    return {**result, "run_by": _operator_actor(user)}
