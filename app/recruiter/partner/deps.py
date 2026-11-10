"""
Partner API authentication + authorisation.

Every partner request resolves to a PartnerContext: (agency, key, client
scope, scopes). Rules enforced here, before any handler runs:

  * key presented only in a header (Authorization: Bearer … or
    X-ApplyForge-Key) — never a query string;
  * key looked up by its public prefix, verified by constant-time HMAC compare;
  * revoked / expired keys, suspended or pending agencies, and agencies whose
    plan or operator override disables the API are rejected;
  * secret keys are refused when the request carries a browser Origin
    (a secret key in front-end code is a leak — fail loudly);
  * publishable keys must come from an allow-listed Origin;
  * scopes are checked per route; test-mode keys can't read private data;
  * per-key (and, for public submissions, per-IP) rate limits.

Failures use generic messages so the API never confirms which keys exist.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.recruiter.enums import AgencyStatus, plan_has_feature
from app.recruiter.models import Agency, ApiKey
from app.recruiter.partner import keys as keys_mod
from app.recruiter.partner import ratelimit
from app.recruiter.services import access as access_service

STATE_KEY = "partner"  # request.state.partner → PartnerContext (read by the CORS/log middleware)


@dataclass
class PartnerContext:
    agency: Agency
    key: ApiKey
    scopes: frozenset[str]
    origin: str | None
    ip: str | None
    client_id: int | None = None
    is_test: bool = False
    is_publishable: bool = False
    rate: dict = field(default_factory=dict)
    # Plain snapshots (safe to read after the request's DB session closes —
    # the request-log middleware runs after it).
    agency_id: int = 0
    key_id: int = 0
    key_prefix: str = ""

    @property
    def is_client_scoped(self) -> bool:
        return self.client_id is not None


def _deny(code: int, message: str, error: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"error": error, "message": message})


def client_ip(request: Request) -> str | None:
    """
    Client IP for rate limiting / logs. X-Forwarded-For's left entries are
    caller-controlled, so we take the entry `PARTNER_TRUSTED_PROXY_HOPS` from
    the right (the address our own nearest trusted proxy saw). 0 = ignore the
    header and use the socket peer.
    """
    hops = max(0, settings.partner_trusted_proxy_hops)
    fwd = request.headers.get("x-forwarded-for")
    raw = None
    if fwd and hops:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            raw = parts[-hops] if len(parts) >= hops else parts[0]
    if raw is None:
        raw = request.client.host if request.client else None
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def _presented_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    hdr = request.headers.get("x-applyforge-key")
    return hdr.strip() if hdr else None


def api_enabled_for(agency: Agency) -> bool:
    mode = (agency.partner_api_access or "plan").lower()
    if mode == "disabled":
        return False
    if mode == "enabled":
        return True
    return plan_has_feature(agency.plan, "partner_api")


def effective_rate_limit(agency: Agency, key: ApiKey) -> int:
    if key.rate_limit_per_minute:
        return key.rate_limit_per_minute
    if agency.partner_api_rate_limit:
        return agency.partner_api_rate_limit
    return (
        settings.partner_rate_limit_publishable
        if key.kind == keys_mod.KIND_PUBLISHABLE
        else settings.partner_rate_limit_secret
    )


def get_partner_context(request: Request, db: Session = Depends(get_db)) -> PartnerContext:
    if request.url.query and any(
        p in request.url.query.lower() for p in ("api_key=", "apikey=", "key=afk_", "key=afp_", "token=")
    ):
        raise _deny(400, "Send the API key in the Authorization header, never in the URL.", "key_in_url")

    ip = client_ip(request)
    # Pre-auth limit per IP: invalid/garbage keys cost a DB lookup, so cap them
    # before touching the database.
    ok, _, reset = ratelimit.hit(f"preauth:{ratelimit.ip_bucket(ip)}", settings.partner_preauth_per_ip_per_minute, 60)
    if not ok:
        exc = _deny(429, "Too many requests from this network.", "rate_limited")
        exc.headers = {"Retry-After": str(max(1, reset - int(datetime.utcnow().timestamp())))}
        raise exc

    presented = _presented_key(request)
    if not presented:
        raise _deny(401, "Missing API key.", "unauthenticated")
    prefix = keys_mod.split_prefix(presented)
    if prefix is None:
        raise _deny(401, "Invalid API key.", "invalid_key")

    key = db.query(ApiKey).filter(ApiKey.key_prefix == prefix).first()
    if key is None or not keys_mod.verify(presented, key.key_hash):
        raise _deny(401, "Invalid API key.", "invalid_key")
    now = datetime.utcnow()
    if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
        raise _deny(401, "Invalid API key.", "invalid_key")

    agency = db.get(Agency, key.agency_id)
    if agency is None or agency.status != AgencyStatus.active:
        raise _deny(403, "API access is not available for this account.", "account_inactive")
    if not api_enabled_for(agency):
        raise _deny(403, "The Partner API is not enabled for this account.", "api_disabled")

    origin = request.headers.get("origin")
    is_pub = key.kind == keys_mod.KIND_PUBLISHABLE
    if not is_pub and origin:
        raise _deny(
            403,
            "Secret keys must only be used server-side. Use a publishable key in browsers.",
            "secret_key_in_browser",
        )
    if is_pub:
        allowed = {o.rstrip("/").lower() for o in (key.allowed_origins or [])}
        if not origin or origin.rstrip("/").lower() not in allowed:
            raise _deny(403, "This origin is not allowed for this key.", "origin_not_allowed")

    limit = effective_rate_limit(agency, key)
    ok, remaining, reset = ratelimit.hit(f"key:{key.id}", limit, 60)
    rate = {"limit": limit, "remaining": remaining, "reset": reset}
    if not ok:
        exc = _deny(429, "Rate limit exceeded. Retry after the window resets.", "rate_limited")
        exc.headers = {
            "Retry-After": str(max(1, reset - int(now.timestamp()))),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(reset),
        }
        raise exc

    granted = frozenset(key.scopes or []) & keys_mod.allowed_scopes(key.kind, key.client_id is not None)
    ctx = PartnerContext(
        agency=agency,
        key=key,
        scopes=granted,
        origin=origin if is_pub else None,
        ip=ip,
        client_id=key.client_id,
        is_test=key.environment == keys_mod.ENV_TEST,
        is_publishable=is_pub,
        rate=rate,
        agency_id=agency.id,
        key_id=key.id,
        key_prefix=key.key_prefix,
    )
    setattr(request.state, STATE_KEY, ctx)

    # Usage stamp (cheap; one UPDATE per request at most once a minute).
    if key.last_used_at is None or (now - key.last_used_at).total_seconds() > 60 or key.last_used_ip != ip:
        key.last_used_at = now
        key.last_used_ip = ip
        try:
            db.commit()
        except Exception:
            db.rollback()
    return ctx


def require_scope(scope: str):
    def _dep(ctx: PartnerContext = Depends(get_partner_context)) -> PartnerContext:
        if scope not in ctx.scopes:
            raise _deny(403, f"This key is missing the '{scope}' scope.", "insufficient_scope")
        if ctx.is_test and scope in keys_mod.PRIVATE_READ_SCOPES:
            raise _deny(
                403, "Test-mode keys can't read live data. Use a live key.", "test_key_private_read"
            )
        return ctx

    return _dep


def require_write_scope(scope: str):
    """Scope check + trial-lock check for mutating routes."""
    base = require_scope(scope)

    def _dep(ctx: PartnerContext = Depends(base)) -> PartnerContext:
        if access_service.is_locked(ctx.agency):
            raise _deny(402, "This account's trial has ended.", "account_locked")
        return ctx

    return _dep


def require_agency_key(ctx: PartnerContext) -> None:
    """Some operations are agency-wide only — never for a client-scoped key."""
    if ctx.is_client_scoped:
        raise _deny(403, "This operation needs an agency-wide key.", "agency_key_required")


def enforce_ip_limit(ctx: PartnerContext, bucket: str, per_hour: int) -> None:
    # Unknown IPs share one bucket — failing closed rather than skipping the limit.
    ok, _, reset = ratelimit.hit(f"ip:{bucket}:{ctx.agency_id}:{ratelimit.ip_bucket(ctx.ip)}", per_hour, 3600)
    if not ok:
        exc = _deny(429, "Too many submissions from this network. Try again later.", "rate_limited")
        exc.headers = {"Retry-After": str(max(1, reset - int(datetime.utcnow().timestamp())))}
        raise exc
