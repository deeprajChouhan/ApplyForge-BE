"""
Operator email console (admin only). Mounted at /api/v1/admin/email.

  Overview     GET  /overview
  Send log     GET  /messages · GET /messages/{scope}/{id} · POST /messages/{scope}/{id}/retry
  Templates    GET  /templates · GET/PUT/DELETE /templates/{key} · POST /templates · POST /templates/preview
               POST /templates/{key}/test
  Automations  GET  /events · GET/POST /rules · PATCH/DELETE /rules/{id} · POST /rules/{id}/dry-run
  Suppressions GET/POST /suppressions · DELETE /suppressions/{id}
  Mailbox      GET  /mailbox/summary · GET /mailbox/threads · GET/PATCH /mailbox/threads/{id}
               POST /mailbox/threads/{id}/reply · POST /mailbox/compose · GET /mailbox/lookup
               GET  /mailbox/messages/{id}/attachments/{idx} · GET/POST /mailbox/canned · DELETE /mailbox/canned/{id}

Consumer and recruiter send logs are read with two separate queries and merged
in Python — never joined.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.email import (
    EmailMessage,
    OpsCannedReply,
    OpsEmailRule,
    OpsEmailSuppression,
    OpsEmailTemplate,
    OpsMailboxMessage,
    OpsMailboxThread,
)
from app.models.models import SupportTicket, User
from app.services.audit.service import AuditService
from app.services.email import conditions, dispatch, events, mailbox, rules
from app.services.email.catalog import CATEGORIES, SYSTEM_TEMPLATES
from app.services.email.jobs import load_scopes
from app.services.email.provider import EmailSendError, OutgoingEmail, get_email_provider
from app.services.email.render import BLOCKS, render, validate_template_source
from app.services.email.templates import list_all, resolve
from app.services.email.themes import CONSUMER, OPS, RECRUITER, agency_theme
from app.services.email.webhooks import suppress

load_scopes()
router = APIRouter(prefix="/admin/email", tags=["admin: email"], dependencies=[Depends(require_admin)])

Scope = Literal["consumer", "recruiter"]


def _audit(db: Session, admin: User, action: str, entity_type: str, entity_id: int | None, after: dict | None = None):
    try:
        AuditService(db).log(action=action, entity_type=entity_type, entity_id=entity_id,
                             actor_user_id=admin.id, actor_role="admin", after=after)
    except Exception:  # noqa: BLE001 — audit must never block the action
        pass


def _model(scope: str):
    cfg = dispatch.SCOPES.get(scope)
    if cfg is None:
        raise HTTPException(404, "Unknown scope")
    return cfg.model


# ── overview ───────────────────────────────────────────────────────────────
@router.get("/overview")
def overview(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    out: dict[str, Any] = {"days": days, "scopes": {}}
    for scope, cfg in dispatch.SCOPES.items():
        m = cfg.model
        rows = db.query(m.status, func.count(m.id)).filter(m.created_at >= since).group_by(m.status).all()
        out["scopes"][scope] = {s: n for s, n in rows}
    out["suppressions"] = db.query(func.count(OpsEmailSuppression.id)).scalar() or 0
    out["active_rules"] = db.query(func.count(OpsEmailRule.id)).filter(OpsEmailRule.is_active.is_(True)).scalar() or 0
    out["unread_threads"] = db.query(func.count(OpsMailboxThread.id)).filter(
        OpsMailboxThread.unread.is_(True), OpsMailboxThread.status.notin_(("spam", "archived"))).scalar() or 0
    out["provider"] = get_email_provider().name
    out["webhook_configured"] = bool(settings.resend_webhook_secret_value)
    out["domain"] = settings.email_domain
    return out


# ── send log ───────────────────────────────────────────────────────────────
def _msg_out(scope: str, r, full: bool = False) -> dict:
    d = {
        "scope": scope, "id": r.id, "to_email": r.to_email, "template_key": r.template_key, "category": r.category,
        "subject": r.subject, "status": r.status, "error": r.error, "event_key": r.event_key, "rule_id": r.rule_id,
        "attempts": r.attempts, "created_at": r.created_at, "sent_at": r.sent_at, "delivered_at": r.delivered_at,
        "opened_at": r.opened_at, "clicked_at": r.clicked_at, "send_after": r.send_after,
        "user_id": getattr(r, "user_id", None), "agency_id": getattr(r, "agency_id", None),
        "recruiter_id": getattr(r, "recruiter_id", None), "is_external": getattr(r, "is_external", False),
    }
    if full:
        d["html"], d["text"] = r.html, r.text
    return d


@router.get("/messages")
def list_messages(
    scope: Literal["all", "consumer", "recruiter"] = "all",
    status: str | None = None,
    template_key: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    scopes = list(dispatch.SCOPES) if scope == "all" else [scope]
    items: list[dict] = []
    total = 0
    take = page * page_size
    for s in scopes:
        m = _model(s)
        q = db.query(m)
        if status:
            q = q.filter(m.status == status)
        if template_key:
            q = q.filter(m.template_key == template_key)
        if search:
            like = f"%{search.strip()}%"
            q = q.filter(or_(m.to_email.ilike(like), m.subject.ilike(like)))
        total += q.count()
        items += [_msg_out(s, r) for r in q.order_by(m.created_at.desc()).limit(take).all()]
    items.sort(key=lambda d: d["created_at"] or datetime.min, reverse=True)
    return {"items": items[(page - 1) * page_size: take], "total": total, "page": page, "page_size": page_size}


@router.get("/messages/{scope}/{msg_id}")
def get_message(scope: Scope, msg_id: int, db: Session = Depends(get_db)):
    r = db.get(_model(scope), msg_id)
    if r is None:
        raise HTTPException(404, "Message not found")
    return _msg_out(scope, r, full=True)


@router.post("/messages/{scope}/{msg_id}/retry")
def retry_message(scope: Scope, msg_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = db.get(_model(scope), msg_id)
    if r is None:
        raise HTTPException(404, "Message not found")
    if r.status not in ("failed", "cancelled", "bounced", "suppressed"):
        raise HTTPException(400, "Only failed, cancelled, bounced or suppressed emails can be retried.")
    if r.status == "suppressed" or db.query(OpsEmailSuppression.id).filter(OpsEmailSuppression.email == r.to_email).first():
        raise HTTPException(400, "Address is suppressed — remove it from the suppression list first.")
    r.status, r.error, r.attempts, r.send_after = "queued", None, 0, datetime.utcnow()
    dispatch.mark_for_dispatch(db, scope, r.id)
    _audit(db, admin, "admin.email.retry", f"{scope}_email", r.id)
    db.commit()
    return _msg_out(scope, r)


# ── templates ──────────────────────────────────────────────────────────────
class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)
    preheader: str | None = Field(None, max_length=300)
    heading: str | None = Field(None, max_length=300)
    cta_label: str | None = Field(None, max_length=120)
    cta_url: str | None = Field(None, max_length=500)
    blocks: list[str] | None = None
    is_active: bool = True


class TemplateCreate(TemplateIn):
    key: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]+$")
    product: Literal["consumer", "recruiter", "external", "ops"]
    category: Literal["transactional", "notification", "digest", "lifecycle"]


def _tpl_out(spec, include_body: bool = True) -> dict:
    base = SYSTEM_TEMPLATES.get(spec.key)
    d = {
        "key": spec.key, "product": spec.product, "category": spec.category, "name": spec.name,
        "subject": spec.subject, "preheader": spec.preheader, "heading": spec.heading,
        "cta_label": spec.cta_label, "cta_url": spec.cta_url, "blocks": spec.blocks,
        "is_system": spec.is_system, "is_active": spec.is_active, "overridden": spec.overridden,
        "description": base.description if base else None,
        "variables": list(base.variables) if base else [],
        "sample": base.sample if base else {},
    }
    if include_body:
        d["body"] = spec.body
    return d


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    return [_tpl_out(s) for s in list_all(db)]


@router.get("/templates/{key}")
def get_template(key: str, db: Session = Depends(get_db)):
    spec = resolve(db, key)
    if spec is None:
        raise HTTPException(404, "Template not found")
    out = _tpl_out(spec)
    base = SYSTEM_TEMPLATES.get(key)
    out["default"] = base.to_dict() if base else None
    return out


def _validate(t: TemplateIn) -> None:
    try:
        validate_template_source(t.subject, t.body, t.preheader, t.heading, t.cta_label, t.cta_url)
    except Exception as exc:  # jinja TemplateSyntaxError
        raise HTTPException(422, f"Template syntax error: {exc}")
    bad = [b for b in (t.blocks or []) if b not in BLOCKS]
    if bad:
        raise HTTPException(422, f"Unknown blocks: {', '.join(bad)}")


@router.put("/templates/{key}")
def save_template(key: str, payload: TemplateIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    spec = resolve(db, key)
    if spec is None:
        raise HTTPException(404, "Template not found")
    _validate(payload)
    row = db.query(OpsEmailTemplate).filter(OpsEmailTemplate.key == key).first()
    if row is None:
        row = OpsEmailTemplate(key=key, product=spec.product, category=spec.category, is_system=spec.is_system)
        db.add(row)
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    row.updated_by = admin.id
    _audit(db, admin, "admin.email.template_update", "email_template", None, {"key": key})
    db.commit()
    return _tpl_out(resolve(db, key))


@router.post("/templates", status_code=201)
def create_template(payload: TemplateCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.key in SYSTEM_TEMPLATES or db.query(OpsEmailTemplate.id).filter(OpsEmailTemplate.key == payload.key).first():
        raise HTTPException(409, "A template with this key already exists.")
    _validate(payload)
    db.add(OpsEmailTemplate(**payload.model_dump(), is_system=False, updated_by=admin.id))
    _audit(db, admin, "admin.email.template_create", "email_template", None, {"key": payload.key})
    db.commit()
    return _tpl_out(resolve(db, payload.key))


@router.delete("/templates/{key}")
def reset_or_delete_template(key: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(OpsEmailTemplate).filter(OpsEmailTemplate.key == key).first()
    if row is None:
        raise HTTPException(404, "Nothing to reset — this template is using its default copy.")
    if key not in SYSTEM_TEMPLATES and db.query(OpsEmailRule.id).filter(OpsEmailRule.template_key == key).first():
        raise HTTPException(409, "An automation uses this template. Change or delete it first.")
    db.delete(row)
    _audit(db, admin, "admin.email.template_reset" if key in SYSTEM_TEMPLATES else "admin.email.template_delete",
           "email_template", None, {"key": key})
    db.commit()
    return {"ok": True, "reset": key in SYSTEM_TEMPLATES}


class PreviewIn(BaseModel):
    key: str | None = None
    product: Literal["consumer", "recruiter", "external", "ops"] | None = None
    subject: str | None = None
    body: str | None = None
    preheader: str | None = None
    heading: str | None = None
    cta_label: str | None = None
    cta_url: str | None = None
    blocks: list[str] | None = None
    context: dict[str, Any] | None = None


def _theme(product: str):
    from dataclasses import replace

    if product == "recruiter":
        return replace(RECRUITER, app_url=settings.recruiter_app_url.rstrip("/"))
    if product == "external":
        return agency_theme("Northbridge Talent", "#0f766e", None)
    if product == "ops":
        return replace(OPS, app_url=settings.consumer_app_url.rstrip("/"))
    return replace(CONSUMER, app_url=settings.consumer_app_url.rstrip("/"))


def _render_preview(db: Session, p: PreviewIn):
    spec = resolve(db, p.key) if p.key else None
    base = SYSTEM_TEMPLATES.get(p.key or "")
    product = p.product or (spec.product if spec else "consumer")
    t = _theme(product)
    ctx = {"app_url": t.app_url, "first_name": "Deepraj", **(base.sample if base else {}), **(p.context or {})}
    pick = lambda field: getattr(p, field) if getattr(p, field) is not None else (getattr(spec, field) if spec else None)  # noqa: E731
    try:
        validate_template_source(pick("subject"), pick("body"), pick("heading"), pick("cta_label"), pick("cta_url"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Template syntax error: {exc}")
    category = spec.category if spec else "transactional"
    return render(
        theme=t, subject=pick("subject") or "", body=pick("body") or "", ctx=ctx, preheader=pick("preheader"),
        heading=pick("heading"), cta_label=pick("cta_label"), cta_url=pick("cta_url"),
        blocks=p.blocks if p.blocks is not None else (spec.blocks if spec else []),
        footer_reason="You're receiving this because you have an account." if product in ("consumer", "recruiter") else None,
        prefs_url=f"{t.app_url}/settings" if product in ("consumer", "recruiter") else None,
        unsubscribe_url=f"{t.app_url}/unsubscribe?token=preview" if category != "transactional" else None,
    ), product


@router.post("/templates/preview")
def preview_template(payload: PreviewIn, db: Session = Depends(get_db)):
    r, _ = _render_preview(db, payload)
    return {"subject": r.subject, "html": r.html, "text": r.text}


class TestSendIn(PreviewIn):
    to: str | None = None


@router.post("/templates/{key}/test")
def test_send(key: str, payload: TestSendIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    payload.key = key
    r, product = _render_preview(db, payload)
    to = payload.to or admin.email
    from_addr = settings.email_from_recruiter if product == "recruiter" else settings.email_from_consumer
    try:
        pid = get_email_provider().send(OutgoingEmail(
            from_addr=from_addr, to=[to], subject=f"[TEST] {r.subject}", html=r.html, text=r.text,
            reply_to=[settings.email_reply_to], tags={"scope": "test", "template": key},
        ))
    except EmailSendError as exc:
        raise HTTPException(502, f"Send failed: {exc}")
    return {"ok": True, "to": to, "provider_message_id": pid}


# ── automations ────────────────────────────────────────────────────────────
@router.get("/events")
def list_events():
    d = events.describe()
    d["recipients"] = {
        "consumer": [{"value": "subject", "label": "The user"}, {"value": "fixed", "label": "A fixed address (internal)"}],
        "recruiter": [{"value": "subject", "label": "The recruiter"}, {"value": "agency_owner", "label": "Agency owner(s)"},
                      {"value": "agency_all", "label": "Everyone in the agency"},
                      {"value": "external", "label": "External email from the event"},
                      {"value": "fixed", "label": "A fixed address (internal)"}],
    }
    d["operators"] = list(conditions.OPS)
    d["blocks"] = list(BLOCKS)
    d["categories"] = list(CATEGORIES)
    return d


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    scope: Scope
    event_key: str
    conditions: dict | None = None
    template_key: str
    recipient: Literal["subject", "agency_owner", "agency_all", "external", "fixed"] = "subject"
    fixed_to: str | None = None
    delay_minutes: int = Field(0, ge=0, le=60 * 24 * 60)
    cancel_if: dict | None = None
    cooldown_days: int = Field(0, ge=0, le=365)
    once_per_subject: bool = True
    is_active: bool = False


class RulePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    event_key: str | None = None
    conditions: dict | None = None
    template_key: str | None = None
    recipient: Literal["subject", "agency_owner", "agency_all", "external", "fixed"] | None = None
    fixed_to: str | None = None
    delay_minutes: int | None = Field(None, ge=0, le=60 * 24 * 60)
    cancel_if: dict | None = None
    cooldown_days: int | None = Field(None, ge=0, le=365)
    once_per_subject: bool | None = None
    is_active: bool | None = None


def _rule_out(r: OpsEmailRule) -> dict:
    return {c.name: getattr(r, c.name) for c in OpsEmailRule.__table__.columns}


def _check_rule(db: Session, scope: str, event_key: str, template_key: str, recipient: str, fixed_to: str | None,
                cond: dict | None, cancel_if: dict | None) -> None:
    d = events.EVENTS.get(event_key)
    if d is None or d.scope != scope:
        raise HTTPException(422, f"'{event_key}' isn't a {scope} trigger.")
    spec = resolve(db, template_key)
    if spec is None:
        raise HTTPException(422, f"Unknown template '{template_key}'.")
    allowed_products = {"consumer": {"consumer", "ops"}, "recruiter": {"recruiter", "external", "ops"}}[scope]
    if spec.product not in allowed_products:
        raise HTTPException(422, f"Template '{template_key}' is a {spec.product} template and can't be used for {scope} automations.")
    if recipient == "fixed" and not (fixed_to and "@" in fixed_to):
        raise HTTPException(422, "Give the fixed recipient's email address.")
    if recipient in ("agency_owner", "agency_all", "external") and scope != "recruiter":
        raise HTTPException(422, "That recipient only applies to recruiter automations.")
    try:
        conditions.validate(cond)
        conditions.validate(cancel_if)
    except conditions.ConditionError as exc:
        raise HTTPException(422, str(exc))


@router.get("/rules")
def list_rules(scope: Scope | None = None, db: Session = Depends(get_db)):
    q = db.query(OpsEmailRule)
    if scope:
        q = q.filter(OpsEmailRule.scope == scope)
    return [_rule_out(r) for r in q.order_by(OpsEmailRule.scope, OpsEmailRule.name).all()]


@router.post("/rules", status_code=201)
def create_rule(payload: RuleIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _check_rule(db, payload.scope, payload.event_key, payload.template_key, payload.recipient, payload.fixed_to,
                payload.conditions, payload.cancel_if)
    r = OpsEmailRule(**payload.model_dump(), created_by=admin.id, version=1)
    db.add(r)
    db.flush()
    _audit(db, admin, "admin.email.rule_create", "email_rule", r.id, {"name": r.name, "active": r.is_active})
    db.commit()
    return _rule_out(r)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, payload: RulePatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = db.get(OpsEmailRule, rule_id)
    if r is None:
        raise HTTPException(404, "Automation not found")
    data = payload.model_dump(exclude_unset=True)
    merged = {**_rule_out(r), **data}
    _check_rule(db, r.scope, merged["event_key"], merged["template_key"], merged["recipient"], merged["fixed_to"],
                merged["conditions"], merged["cancel_if"])
    material = {"event_key", "conditions", "template_key", "recipient", "fixed_to"}
    for k, v in data.items():
        setattr(r, k, v)
    if material & data.keys():
        r.version = (r.version or 1) + 1
    _audit(db, admin, "admin.email.rule_update", "email_rule", r.id, data)
    db.commit()
    return _rule_out(r)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    r = db.get(OpsEmailRule, rule_id)
    if r is None:
        raise HTTPException(404, "Automation not found")
    db.delete(r)
    _audit(db, admin, "admin.email.rule_delete", "email_rule", rule_id, {"name": r.name})
    db.commit()
    return {"ok": True}


@router.post("/rules/{rule_id}/dry-run")
def dry_run_rule(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(OpsEmailRule, rule_id)
    if r is None:
        raise HTTPException(404, "Automation not found")
    return rules.dry_run(db, r)


@router.get("/rules/{rule_id}/stats")
def rule_stats(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(OpsEmailRule, rule_id)
    if r is None:
        raise HTTPException(404, "Automation not found")
    m = _model(r.scope)
    rows = db.query(m.status, func.count(m.id)).filter(m.rule_id == rule_id).group_by(m.status).all()
    return {"by_status": {s: n for s, n in rows}, "fire_count": r.fire_count, "last_fired_at": r.last_fired_at}


# ── suppressions ───────────────────────────────────────────────────────────
class SuppressionIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    note: str | None = None


@router.get("/suppressions")
def list_suppressions(search: str | None = None, db: Session = Depends(get_db)):
    q = db.query(OpsEmailSuppression)
    if search:
        q = q.filter(OpsEmailSuppression.email.ilike(f"%{search.strip()}%"))
    return [{"id": s.id, "email": s.email, "reason": s.reason, "note": s.note, "created_at": s.created_at}
            for s in q.order_by(OpsEmailSuppression.created_at.desc()).limit(500).all()]


@router.post("/suppressions", status_code=201)
def add_suppression(payload: SuppressionIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    suppress(db, payload.email, "manual", payload.note)
    _audit(db, admin, "admin.email.suppress", "email_suppression", None, {"email": payload.email})
    db.commit()
    return {"ok": True}


@router.delete("/suppressions/{sid}")
def remove_suppression(sid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = db.get(OpsEmailSuppression, sid)
    if s is None:
        raise HTTPException(404, "Not found")
    db.delete(s)
    _audit(db, admin, "admin.email.unsuppress", "email_suppression", sid, {"email": s.email})
    db.commit()
    return {"ok": True}


# ── mailbox ────────────────────────────────────────────────────────────────
def _thread_out(t: OpsMailboxThread) -> dict:
    return {c.name: getattr(t, c.name) for c in OpsMailboxThread.__table__.columns}


def _mail_out(m: OpsMailboxMessage) -> dict:
    d = {c.name: getattr(m, c.name) for c in OpsMailboxMessage.__table__.columns}
    d["attachments"] = [{k: v for k, v in (a or {}).items() if k != "storage_key"} | {"available": bool((a or {}).get("storage_key"))}
                        for a in (m.attachments or [])]
    return d


@router.get("/mailbox/summary")
def mailbox_summary(db: Session = Depends(get_db)):
    boxes = mailbox.mailboxes()
    out = {}
    for name, addr in boxes.items():
        base = db.query(func.count(OpsMailboxThread.id)).filter(OpsMailboxThread.mailbox == name)
        out[name] = {
            "address": addr,
            "unread": base.filter(OpsMailboxThread.unread.is_(True), OpsMailboxThread.status.notin_(("spam", "archived"))).scalar() or 0,
            "open": base.filter(OpsMailboxThread.status == "open").scalar() or 0,
        }
    return out


@router.get("/mailbox/threads")
def list_threads(
    mailbox_name: str | None = Query(None, alias="mailbox"),
    status: str | None = None,
    starred: bool | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(OpsMailboxThread)
    if mailbox_name:
        q = q.filter(OpsMailboxThread.mailbox == mailbox_name)
    if status == "inbox" or status is None:
        q = q.filter(OpsMailboxThread.status.in_(("open", "waiting")))
    elif status != "all":
        q = q.filter(OpsMailboxThread.status == status)
    if starred:
        q = q.filter(OpsMailboxThread.starred.is_(True))
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(or_(OpsMailboxThread.subject.ilike(like), OpsMailboxThread.counterpart_email.ilike(like),
                         OpsMailboxThread.counterpart_name.ilike(like), OpsMailboxThread.snippet.ilike(like)))
    total = q.count()
    rows = q.order_by(OpsMailboxThread.last_message_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_thread_out(t) for t in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/mailbox/threads/{thread_id}")
def get_thread(thread_id: int, mark_read: bool = True, db: Session = Depends(get_db)):
    t = db.get(OpsMailboxThread, thread_id)
    if t is None:
        raise HTTPException(404, "Thread not found")
    msgs = db.query(OpsMailboxMessage).filter(OpsMailboxMessage.thread_id == t.id).order_by(OpsMailboxMessage.created_at).all()
    if mark_read and t.unread:
        t.unread = False
        db.commit()
    return {"thread": _thread_out(t), "messages": [_mail_out(m) for m in msgs]}


class ThreadPatch(BaseModel):
    status: Literal["open", "waiting", "closed", "spam", "archived"] | None = None
    starred: bool | None = None
    unread: bool | None = None
    labels: list[str] | None = None
    mailbox: str | None = None


@router.patch("/mailbox/threads/{thread_id}")
def update_thread(thread_id: int, payload: ThreadPatch, db: Session = Depends(get_db)):
    t = db.get(OpsMailboxThread, thread_id)
    if t is None:
        raise HTTPException(404, "Thread not found")
    data = payload.model_dump(exclude_unset=True)
    if "mailbox" in data and data["mailbox"] not in mailbox.mailboxes():
        raise HTTPException(422, "Unknown mailbox")
    for k, v in data.items():
        setattr(t, k, v)
    if data.get("status") == "spam":
        suppress(db, t.counterpart_email, "manual", "marked as spam in mailbox")
    db.commit()
    return _thread_out(t)


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=50_000)
    cc: list[str] | None = None
    close: bool = False


@router.post("/mailbox/threads/{thread_id}/reply")
def reply(thread_id: int, payload: ReplyIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.get(OpsMailboxThread, thread_id)
    if t is None:
        raise HTTPException(404, "Thread not found")
    m = mailbox.send_outbound(db, t, admin_id=admin.id, body=payload.body, cc=payload.cc)
    if payload.close and m.status == "sent":
        t.status = "closed"
    db.commit()
    if m.status != "sent":
        raise HTTPException(502, f"Reply saved but not sent: {m.error}")
    return _mail_out(m)


class ComposeIn(BaseModel):
    mailbox: str
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=50_000)
    cc: list[str] | None = None


@router.post("/mailbox/compose", status_code=201)
def compose(payload: ComposeIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.mailbox not in mailbox.mailboxes():
        raise HTTPException(422, "Unknown mailbox")
    t = mailbox.compose(db, admin_id=admin.id, mailbox=payload.mailbox, to=payload.to, subject=payload.subject,
                        body=payload.body, cc=payload.cc)
    db.commit()
    return _thread_out(t)


@router.get("/mailbox/messages/{message_id}/attachments/{idx}")
def download_attachment(message_id: int, idx: int, db: Session = Depends(get_db)):
    from app.services.storage.service import get_storage_service

    m = db.get(OpsMailboxMessage, message_id)
    atts = (m.attachments or []) if m else []
    if not m or idx < 0 or idx >= len(atts) or not atts[idx].get("storage_key"):
        raise HTTPException(404, "Attachment not available")
    a = atts[idx]
    data = get_storage_service().download_bytes(a["storage_key"])
    safe = re.sub(r'[^A-Za-z0-9._ -]', "_", a.get("filename") or "attachment")
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff"})


@router.get("/mailbox/lookup")
def lookup_sender(email: str, db: Session = Depends(get_db)):
    """Who is this sender? One query per product, merged here — never a join."""
    e = email.strip().lower()
    out: dict[str, Any] = {"email": e, "consumer": None, "recruiter": None}
    u = db.query(User).filter(func.lower(User.email) == e).first()
    if u:
        tickets = db.query(func.count(SupportTicket.id)).filter(SupportTicket.user_id == u.id,
                                                                SupportTicket.status.in_(("open", "in_progress"))).scalar()
        out["consumer"] = {"user_id": u.id, "plan": getattr(u.plan, "value", u.plan), "is_active": u.is_active,
                           "created_at": u.created_at, "open_tickets": tickets or 0}
    from app.recruiter.services.email import lookup_recruiter

    out["recruiter"] = lookup_recruiter(db, e)
    out["suppressed"] = db.query(OpsEmailSuppression.id).filter(OpsEmailSuppression.email == e).first() is not None
    return out


class CannedIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1)
    mailbox: str | None = None


@router.get("/mailbox/canned")
def list_canned(db: Session = Depends(get_db)):
    return [{"id": c.id, "title": c.title, "body": c.body, "mailbox": c.mailbox}
            for c in db.query(OpsCannedReply).order_by(OpsCannedReply.title).all()]


@router.post("/mailbox/canned", status_code=201)
def create_canned(payload: CannedIn, db: Session = Depends(get_db)):
    c = OpsCannedReply(**payload.model_dump())
    db.add(c)
    db.commit()
    return {"id": c.id, "title": c.title, "body": c.body, "mailbox": c.mailbox}


@router.delete("/mailbox/canned/{cid}")
def delete_canned(cid: int, db: Session = Depends(get_db)):
    c = db.get(OpsCannedReply, cid)
    if c is None:
        raise HTTPException(404, "Not found")
    db.delete(c)
    db.commit()
    return {"ok": True}
