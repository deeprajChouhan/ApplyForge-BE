"""
Central AI metering: every LLM and embedding call is recorded and quota-checked
here, so no feature can spend tokens without showing up in usage_events.

How a call is attributed
------------------------
A request-scoped ``UsageContext`` (contextvar) says who is spending and where:

* HTTP: ``UsageContextMiddleware`` decodes the Bearer access token (no DB hit)
  and labels the call with the method + templated path, e.g.
  ``POST /applications/{id}/tailor``. Recruiter and partner paths are marked
  ``skip`` — recruiter metering lives in the recruiter module (data wall).
* Background / scheduled work: wrap it in ``usage_scope(user_id, "crawler.scheduled")``.
* No context at all → recorded as system spend (user_id NULL), never dropped.

Recording
---------
* LLM calls: one ``usage_events`` row per call (kind="llm") + the monthly
  ``usage_ledger`` upsert. Real token counts from the provider when it reports
  them, otherwise a tiktoken/word estimate.
* Embeddings: summed per request and flushed as one row (kind="embedding") when
  the request or scope ends — indexing a resume makes dozens of calls. They are
  priced on the Costs page but do not count toward user quotas.
* Writes use their own short-lived session, so a caller's rollback can't erase
  spend, and a metering failure never breaks the user's request.

Enforcement
-----------
Before each LLM call ``QuotaService.enforce`` runs for the context's user. When
the daily (rolling 24h) or monthly allowance is used up it raises
``UsageLimitExceeded`` (an HTTP 429). Some services swallow provider errors and
fall back to heuristics; the middleware then tags the response with
``X-Usage-Limited`` so the UI can still tell the user why AI output is missing.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, Optional

import structlog

from app.models.enums import FeatureFlag

logger = structlog.get_logger(__name__)

KIND_LLM = "llm"
KIND_EMBEDDING = "embedding"


@dataclass
class UsageContext:
    user_id: Optional[int]
    endpoint: str
    feature: Optional[FeatureFlag] = None
    skip: bool = False
    # Filled while the request runs
    embed_tokens: int = 0
    embed_calls: int = 0
    embed_model: Optional[str] = None
    blocked: Optional[str] = None  # "daily" | "monthly" once a call was refused


_ctx: ContextVar[Optional[UsageContext]] = ContextVar("applyforge_usage_ctx", default=None)


def current() -> Optional[UsageContext]:
    return _ctx.get()


def _session():
    # Imported lazily so tests that swap the engine before import still work.
    from app.db.session import SessionLocal
    return SessionLocal()


# ── Attribution helpers ─────────────────────────────────────────────────────

_ID_SEGMENT = re.compile(r"^(\d+|[0-9a-f]{8}-[0-9a-f-]{27,})$", re.I)

# First path segment after /api/v1 → feature bucket for the Costs page.
_FEATURE_BY_PREFIX: dict[str, FeatureFlag] = {
    "chat": FeatureFlag.chat,
    "interview": FeatureFlag.interview_practice,
    "profile": FeatureFlag.resume,
    "files": FeatureFlag.resume,
    "knowledge": FeatureFlag.resume,
    "resume-templates": FeatureFlag.resume,
    "kits": FeatureFlag.package_generation,
    "crawler": FeatureFlag.job_crawler,
    "jobs": FeatureFlag.job_discovery,
    "applications": FeatureFlag.jd_analyze,
    "extension": FeatureFlag.jd_analyze,
    "ephemeral": FeatureFlag.jd_analyze,
    "documents": FeatureFlag.jd_analyze,
}


def endpoint_label(method: str, path: str) -> str:
    """'POST', '/api/v1/applications/42/tailor' → 'POST /applications/{id}/tailor'."""
    p = path.split("?", 1)[0]
    if p.startswith("/api/v1"):
        p = p[len("/api/v1"):]
    parts = ["{id}" if _ID_SEGMENT.match(s) else s for s in p.split("/") if s]
    label = f"{method.upper()} /{'/'.join(parts)}"
    return label[:100]


def feature_for_path(path: str) -> Optional[FeatureFlag]:
    p = path[len("/api/v1"):] if path.startswith("/api/v1") else path
    first = next((s for s in p.split("/") if s), "")
    if "package" in p:
        return FeatureFlag.package_generation
    return _FEATURE_BY_PREFIX.get(first)


@contextmanager
def usage_scope(
    user_id: Optional[int],
    endpoint: str,
    feature: Optional[FeatureFlag] = None,
    skip: bool = False,
) -> Iterator[UsageContext]:
    """Attribute every AI call inside the block. Flushes embedding usage on exit."""
    ctx = UsageContext(user_id=user_id, endpoint=endpoint[:100], feature=feature, skip=skip)
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        try:
            flush_embeddings(ctx)
        finally:
            _ctx.reset(token)


# ── Recording ───────────────────────────────────────────────────────────────

def _count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    from app.services.usage.service import _count_tokens_approx
    return _count_tokens_approx(text or "", model)


def record_llm(model: str, tokens_in: int, tokens_out: int, ctx: Optional[UsageContext] = None) -> None:
    """Write one usage_events row (+ ledger upsert for a known user). Never raises."""
    ctx = ctx if ctx is not None else current()
    if ctx is not None and ctx.skip:
        return
    from app.models.models import UsageEvent, UsageLedger

    user_id = ctx.user_id if ctx else None
    endpoint = ctx.endpoint if ctx else "system"
    feature = ctx.feature if ctx else None
    db = _session()
    try:
        db.add(UsageEvent(
            user_id=user_id, feature=feature, endpoint=endpoint, kind=KIND_LLM,
            tokens_in=int(tokens_in or 0), tokens_out=int(tokens_out or 0), model=model or "unknown",
        ))
        if user_id is not None:
            from app.services.usage.service import _current_month
            month = _current_month()
            ledger = db.query(UsageLedger).filter_by(user_id=user_id, month_year=month).first()
            total = int(tokens_in or 0) + int(tokens_out or 0)
            if ledger:
                ledger.tokens_used = (ledger.tokens_used or 0) + total
                ledger.api_calls = (ledger.api_calls or 0) + 1
            else:
                db.add(UsageLedger(user_id=user_id, month_year=month, tokens_used=total, api_calls=1))
        db.commit()
    except Exception as exc:  # metering must never break the user's request
        db.rollback()
        logger.warning("usage_record_failed", error=str(exc), endpoint=endpoint)
    finally:
        db.close()


def add_embedding(model: str, tokens: int) -> None:
    ctx = current()
    if ctx is None:
        # Not inside a request/scope: record immediately as system spend.
        _write_embedding(None, "system", None, model, tokens, 1)
        return
    if ctx.skip:
        return
    ctx.embed_tokens += int(tokens or 0)
    ctx.embed_calls += 1
    ctx.embed_model = model


def flush_embeddings(ctx: Optional[UsageContext]) -> None:
    if ctx is None or ctx.skip or ctx.embed_calls == 0:
        return
    _write_embedding(ctx.user_id, ctx.endpoint, ctx.feature, ctx.embed_model or "embedding", ctx.embed_tokens, ctx.embed_calls)
    ctx.embed_tokens = ctx.embed_calls = 0


def _write_embedding(user_id, endpoint, feature, model, tokens, calls) -> None:
    from app.models.models import UsageEvent
    db = _session()
    try:
        # One row for the batch; endpoint carries the call count for the curious.
        label = f"{endpoint} ×{calls}" if calls > 1 else endpoint
        db.add(UsageEvent(
            user_id=user_id, feature=feature, endpoint=label[:100], kind=KIND_EMBEDDING,
            tokens_in=int(tokens or 0), tokens_out=0, model=model,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("usage_embedding_record_failed", error=str(exc))
    finally:
        db.close()


# ── Enforcement ─────────────────────────────────────────────────────────────

def enforce_current(estimated_tokens: int = 0) -> None:
    """Raise UsageLimitExceeded if the context's user has no allowance left."""
    ctx = current()
    if ctx is None or ctx.skip or ctx.user_id is None:
        return
    from app.services.usage.quota import QuotaService, UsageLimitExceeded
    db = _session()
    try:
        QuotaService(db).enforce(ctx.user_id, estimated_tokens)
    except UsageLimitExceeded as exc:
        ctx.blocked = exc.scope
        raise
    finally:
        db.close()


# ── HTTP middleware ─────────────────────────────────────────────────────────

_SKIP_PREFIXES = ("/api/v1/recruiter", "/api/v1/partner", "/partner")


def _user_id_from_headers(headers: list[tuple[bytes, bytes]]) -> Optional[int]:
    auth = next((v.decode("latin-1") for k, v in headers if k.lower() == b"authorization"), "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        from jose import jwt
        from app.core.config import settings
        payload = jwt.decode(auth[7:].strip(), settings.secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "access":
            return None
        return int(payload.get("sub"))
    except Exception:
        return None


class UsageContextMiddleware:
    """Pure ASGI so the contextvar reaches sync endpoints in the threadpool and streaming bodies."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        skip = path.startswith(_SKIP_PREFIXES)
        user_id = None if skip else _user_id_from_headers(scope.get("headers") or [])
        ctx = UsageContext(
            user_id=user_id,
            endpoint=endpoint_label(scope.get("method", "GET"), path),
            feature=None if skip else feature_for_path(path),
            skip=skip,
        )
        token = _ctx.set(ctx)

        async def send_wrapper(message):
            if message.get("type") == "http.response.start" and ctx.blocked:
                headers = list(message.get("headers") or [])
                headers.append((b"x-usage-limited", ctx.blocked.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                flush_embeddings(ctx)
            finally:
                _ctx.reset(token)
