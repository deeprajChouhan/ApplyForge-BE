"""
Consumer (ApplyForgePro) side of email: watchers, built-in sends, facts,
preferences and the rule adapter. Reads consumer tables only.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.email import EmailMessage, EmailPreference, OpsEmailRule
from app.models.enums import ApplicationStatus
from app.models.models import (
    ApplicationStatusHistory,
    GeneratedDocument,
    JobApplication,
    ParsedResumeData,
    RefreshToken,
    SupportTicket,
    SupportTicketMessage,
    UploadedFile,
    User,
    UserExtensionState,
    UserProfile,
)
from app.services.email import dispatch, events, rules
from app.services.email.events import EventDef, EventField
from app.services.email.outbox import CATEGORY_PREF, QueueResult, queue_email
from app.services.email.rules import Recipient, Subject
from app.services.email.templates import resolve
from app.services.email.themes import CONSUMER

logger = logging.getLogger(__name__)
SCOPE = "consumer"


def theme():
    return replace(CONSUMER, app_url=settings.consumer_app_url.rstrip("/"))


def _enum(v: Any) -> Any:
    return getattr(v, "value", v)


# ── preferences ────────────────────────────────────────────────────────────
def get_prefs(db: Session, user_id: int) -> EmailPreference:
    p = db.query(EmailPreference).filter(EmailPreference.user_id == user_id).first()
    if p is None:
        p = EmailPreference(user_id=user_id, notifications=True, digests=True, product_updates=True)
        db.add(p)
        db.flush()
    return p


def allowed(db: Session, user_id: int, category: str) -> bool:
    attr = CATEGORY_PREF.get(category)
    if attr is None:
        return True  # transactional
    p = db.query(EmailPreference).filter(EmailPreference.user_id == user_id).first()
    return True if p is None else bool(getattr(p, attr))


def first_name(db: Session, user_id: int) -> str | None:
    prof = db.query(UserProfile.full_name).filter(UserProfile.user_id == user_id).first()
    name = (prof[0] if prof else None) or ""
    return name.split()[0] if name.strip() else None


# ── queueing ───────────────────────────────────────────────────────────────
def queue_for_user(
    db: Session,
    user: User,
    template_key: str,
    ctx: dict[str, Any],
    *,
    idempotency_key: str,
    event_key: str | None = None,
    rule_id: int | None = None,
    delay_minutes: int = 0,
    to_email: str | None = None,
    respect_prefs: bool = True,
) -> QueueResult:
    spec = resolve(db, template_key)
    if spec is None:
        return QueueResult(None, "missing_template")
    internal = to_email is not None and to_email.lower() != (user.email or "").lower()
    if respect_prefs and not internal and not allowed(db, user.id, spec.category):
        return QueueResult(None, "opted_out")
    return queue_email(
        db,
        model=EmailMessage,
        scope=SCOPE,
        owner={"user_id": user.id},
        subject_id=None if internal else user.id,
        to_email=to_email or user.email,
        template_key=template_key,
        ctx={"first_name": first_name(db, user.id), "email": user.email, **ctx},
        theme=theme(),
        idempotency_key=idempotency_key,
        event_key=event_key,
        rule_id=rule_id,
        delay_minutes=delay_minutes,
        footer_reason=None if internal else f"You're receiving this because you have an ApplyForge account ({user.email}).",
        cap_filter=[EmailMessage.user_id == user.id, EmailMessage.to_email == (user.email or "").lower()],
    )


# ── facts (for conditions) ─────────────────────────────────────────────────
def facts(db: Session, user: User) -> dict[str, Any]:
    now = datetime.utcnow()
    apps = db.query(JobApplication.status, JobApplication.created_at).filter(JobApplication.user_id == user.id).all()
    last_active = db.query(func.max(RefreshToken.created_at)).filter(RefreshToken.user_id == user.id).scalar()
    ext = db.query(UserExtensionState).filter(UserExtensionState.user_id == user.id).first()
    resumes = (db.query(func.count(ParsedResumeData.id))
               .filter(ParsedResumeData.user_id == user.id, ParsedResumeData.deleted_at.is_(None)).scalar() or 0)
    created = user.created_at or now
    return {
        "plan": _enum(user.plan),
        "subscription_status": _enum(user.subscription_status),
        "days_since_signup": (now - created).days,
        "resume_count": resumes,
        "application_count": len(apps),
        "applications_7d": sum(1 for _, c in apps if c and c >= now - timedelta(days=7)),
        "interview_count": sum(1 for s, _ in apps if _enum(s) == "interview"),
        "offer_count": sum(1 for s, _ in apps if _enum(s) == "offer"),
        "extension_connected": bool(ext and ext.connected_at),
        "days_since_active": (now - last_active).days if last_active else (now - created).days,
    }


FACT_FIELDS = (
    EventField("plan", "enum", "Plan", ("free", "pro", "enterprise")),
    EventField("days_since_signup", "number", "Days since sign-up"),
    EventField("resume_count", "number", "Resumes uploaded"),
    EventField("application_count", "number", "Applications (total)"),
    EventField("applications_7d", "number", "Applications (last 7 days)"),
    EventField("interview_count", "number", "Applications at Interview"),
    EventField("offer_count", "number", "Applications at Offer"),
    EventField("extension_connected", "boolean", "Extension connected"),
    EventField("days_since_active", "number", "Days since last sign-in"),
)


# ── rule adapter ───────────────────────────────────────────────────────────
class ConsumerAdapter:
    scope = SCOPE

    def subject_from_payload(self, db: Session, payload: dict) -> Subject | None:
        uid = payload.get("user_id") or payload.get("subject_id")
        if uid is None and payload.get("application_id"):
            uid = db.query(JobApplication.user_id).filter(JobApplication.id == payload["application_id"]).scalar()
        user = db.get(User, int(uid)) if uid else None
        if user is None or not user.is_active:
            return None
        return Subject(id=user.id, data={"user": user})

    def facts(self, db: Session, subject: Subject) -> dict:
        return facts(db, subject.data["user"])

    def recipients(self, db: Session, rule: OpsEmailRule, subject: Subject, payload: dict) -> list[Recipient]:
        user: User = subject.data["user"]
        if rule.recipient == "fixed" and rule.fixed_to:
            return [Recipient(key=f"fixed:{rule.fixed_to.lower()}:u{user.id}", email=rule.fixed_to, owner={"user": user})]
        return [Recipient(key=f"u{user.id}", email=user.email, first_name=first_name(db, user.id), owner={"user": user})]

    def queue(self, db, recipient, *, template_key, ctx, event_key, rule_id, delay_minutes, idempotency_key):
        user: User = recipient.owner["user"]
        to = recipient.email if recipient.key.startswith("fixed:") else None
        return queue_for_user(db, user, template_key, ctx, idempotency_key=idempotency_key, event_key=event_key,
                              rule_id=rule_id, delay_minutes=delay_minutes, to_email=to)

    def last_sent(self, db: Session, rule_id: int, recipient: Recipient) -> datetime | None:
        user: User = recipient.owner["user"]
        return (db.query(func.max(EmailMessage.created_at))
                .filter(EmailMessage.rule_id == rule_id, EmailMessage.user_id == user.id,
                        EmailMessage.to_email == recipient.email.lower(),
                        EmailMessage.status.in_(("queued", "sending", "sent", "delivered", "opened", "clicked")))
                .scalar())

    def daily_subjects(self, db: Session) -> Iterable[Subject]:
        q = db.query(User).filter(User.is_active.is_(True)).order_by(User.id)
        for user in q.yield_per(200):
            yield Subject(id=user.id, data={"user": user})


def _facts_for_row(db: Session, row: EmailMessage) -> dict:
    user = db.get(User, row.user_id)
    return facts(db, user) if user else {}


# ── events ─────────────────────────────────────────────────────────────────
for d in (
    EventDef("user.registered", SCOPE, "Signed up", "A new consumer account was created (email or Google)."),
    EventDef("user.password_changed", SCOPE, "Password changed", "A user's password changed."),
    EventDef("resume.parsed", SCOPE, "Resume parsed", "A resume finished parsing.",
             (EventField("parse_confidence", "number", "Parse confidence (0–100)"),)),
    EventDef("application.created", SCOPE, "Role saved", "A job was saved as a tracked application.",
             (EventField("is_first", "boolean", "First application"), EventField("priority_score", "number", "Priority Score"),
              EventField("company", "string", "Company"), EventField("role_title", "string", "Role title"))),
    EventDef("application.status_changed", SCOPE, "Application status changed", "An application moved on the board.",
             (EventField("old_status", "enum", "From", tuple(s.value for s in ApplicationStatus)),
              EventField("new_status", "enum", "To", tuple(s.value for s in ApplicationStatus)),
              EventField("company", "string", "Company"), EventField("role_title", "string", "Role title"))),
    EventDef("document.generated", SCOPE, "Documents generated", "Tailored documents were generated for an application.",
             (EventField("doc_type", "string", "Document type"), EventField("company", "string", "Company"),
              EventField("role_title", "string", "Role title"))),
    EventDef("support.ticket_created", SCOPE, "Support ticket opened", "A user opened a help-desk ticket.",
             (EventField("subject", "string", "Subject"), EventField("category", "string", "Category"),
              EventField("priority", "string", "Priority"))),
    EventDef(rules.SCHEDULE_DAILY[SCOPE], SCOPE, "Every day (per user)", "Runs once a day at 09:00 UK for every active user. Use facts to target.", scheduled=True),
):
    events.register_event(d)
events.register_facts(SCOPE, FACT_FIELDS)


def _w_user_insert(obj: User, h):
    return "user.registered", {"user_id": obj.id, "_id": f"user{obj.id}"}


def _w_user_pw(obj: User, h):
    ch = h("password_hash")
    if not ch or ch[0] is None:
        return None
    return "user.password_changed", {"user_id": obj.id, "_id": f"pw{obj.id}-{datetime.utcnow():%Y%m%d%H%M%S}"}


def _w_resume(obj: ParsedResumeData, h):
    conf = obj.confidence_score
    if conf is not None and conf <= 1:
        conf = conf * 100
    return "resume.parsed", {"user_id": obj.user_id, "resume_id": obj.id, "uploaded_file_id": obj.uploaded_file_id,
                             "parse_confidence": round(conf) if conf is not None else None, "_id": f"resume{obj.id}"}


def _w_app_created(obj: JobApplication, h):
    return "application.created", {"user_id": obj.user_id, "application_id": obj.id, "company": obj.company_name,
                                   "role_title": obj.role_title, "priority_score": obj.priority_score, "_id": f"app{obj.id}"}


def _w_status(obj: ApplicationStatusHistory, h):
    if obj.old_status is None:
        return None
    return "application.status_changed", {"application_id": obj.application_id, "old_status": _enum(obj.old_status),
                                          "new_status": _enum(obj.new_status), "_id": f"ash{obj.id}"}


def _w_doc(obj: GeneratedDocument, h):
    # A package generates 3–4 docs at once: one event id per application per hour.
    return "document.generated", {"user_id": obj.user_id, "application_id": obj.application_id,
                                  "doc_type": _enum(obj.doc_type), "_id": f"docs-app{obj.application_id}-{datetime.utcnow():%Y%m%d%H}"}


def _w_ticket(obj: SupportTicket, h):
    return "support.ticket_created", {"user_id": obj.user_id, "ticket_id": obj.id, "subject": obj.subject,
                                      "category": obj.category, "priority": obj.priority, "_id": f"ticket{obj.id}"}


def _w_ticket_msg(obj: SupportTicketMessage, h):
    if obj.sender_role != "admin" or getattr(obj, "_email_suppress", False):
        return None
    return "support.admin_replied", {"ticket_id": obj.ticket_id, "message_id": obj.id, "body": obj.body[:4000]}


events.watch(User, "insert", _w_user_insert)
events.watch(User, "update", _w_user_pw, attrs=("password_hash",))
events.watch(ParsedResumeData, "insert", _w_resume)
events.watch(JobApplication, "insert", _w_app_created)
events.watch(ApplicationStatusHistory, "insert", _w_status)
events.watch(GeneratedDocument, "insert", _w_doc)
events.watch(SupportTicket, "insert", _w_ticket)
events.watch(SupportTicketMessage, "insert", _w_ticket_msg)


# ── payload enrichment (runs before rules) ────────────────────────────────
@events.on_event("application.created")
def _enrich_app_created(db: Session, p: dict) -> None:
    n = db.query(func.count(JobApplication.id)).filter(JobApplication.user_id == p["user_id"]).scalar() or 0
    p["is_first"] = n <= 1


@events.on_event("application.status_changed")
@events.on_event("document.generated")
def _enrich_app(db: Session, p: dict) -> None:
    app = db.get(JobApplication, p["application_id"])
    if app is None:
        return
    p.setdefault("user_id", app.user_id)
    p["company"], p["role_title"] = app.company_name, app.role_title
    if app.jd_analysis_json:
        try:
            p["unsupported_gaps"] = (json.loads(app.jd_analysis_json) or {}).get("unsupported_gaps") or []
        except (ValueError, TypeError):
            pass


@events.on_event("resume.parsed")
def _enrich_resume(db: Session, p: dict) -> None:
    if p.get("uploaded_file_id"):
        f = db.get(UploadedFile, p["uploaded_file_id"])
        p["resume_name"] = f.filename if f else "your resume"
    else:
        p["resume_name"] = "your resume"


# ── built-in transactional sends ───────────────────────────────────────────
@events.on_event("user.registered")
def _welcome(db: Session, p: dict) -> None:
    user = db.get(User, p["user_id"])
    if user and user.is_active:
        queue_for_user(db, user, "c_welcome", {}, idempotency_key=f"c_welcome-u{user.id}", event_key="user.registered")


@events.on_event("user.password_changed")
def _pw_changed(db: Session, p: dict) -> None:
    user = db.get(User, p["user_id"])
    if user:
        queue_for_user(db, user, "c_password_changed", {"when": f"{datetime.utcnow():%d %b %Y %H:%M}"},
                       idempotency_key=f"c_pwchg-{p['_id']}", event_key="user.password_changed")


@events.on_event("support.admin_replied")
def _support_reply(db: Session, p: dict) -> None:
    ticket = db.get(SupportTicket, p["ticket_id"])
    user = db.get(User, ticket.user_id) if ticket else None
    if not user:
        return
    queue_for_user(db, user, "c_support_reply",
                   {"ticket_subject": ticket.subject, "ticket_id": ticket.id, "quote": p.get("body", "")},
                   idempotency_key=f"c_support_reply-m{p['message_id']}", event_key="support.admin_replied")


def send_password_reset(db: Session, user: User) -> None:
    from app.services.email.tokens import make_reset_token

    token = make_reset_token("user", user.id, user.password_hash)
    url = f"{settings.consumer_app_url.rstrip('/')}/reset-password?token={token}"
    queue_for_user(db, user, "c_password_reset", {"reset_url": url},
                   idempotency_key=f"c_pwreset-u{user.id}-{datetime.utcnow():%Y%m%d%H%M%S%f}")


# ── built-in digest (weekly, Monday) ───────────────────────────────────────
def send_weekly_digests(db: Session, today: date | None = None) -> int:
    today = today or date.today()
    week = today.isocalendar()
    n = 0
    for user in db.query(User).filter(User.is_active.is_(True)).yield_per(200):
        if not allowed(db, user.id, "digest"):
            continue
        apps = db.query(JobApplication).filter(JobApplication.user_id == user.id).all()
        live = [a for a in apps if _enum(a.status) not in ("archived", "rejected", "draft")]
        if not live:
            continue
        stale_before = datetime.utcnow() - timedelta(days=7)
        follow = [a for a in apps if _enum(a.status) == "applied" and (a.updated_at or a.created_at) < stale_before]
        res = queue_for_user(
            db, user, "c_weekly_digest",
            {"stats": [{"label": "In play", "value": len(live)},
                       {"label": "Interviews", "value": sum(1 for a in apps if _enum(a.status) == "interview")},
                       {"label": "Follow-ups due", "value": len(follow)}],
             "jobs": [{"title": a.role_title, "company": a.company_name, "score": round(a.priority_score) if a.priority_score else None,
                       "tier": ("strong" if (a.priority_score or 0) >= 75 else "good" if (a.priority_score or 0) >= 50 else "low"),
                       "url": f"/applications/{a.id}"} for a in sorted(follow, key=lambda a: -(a.priority_score or 0))[:3]]},
            idempotency_key=f"c_weekly-u{user.id}-{week[0]}w{week[1]}", event_key="schedule.weekly",
        )
        n += 1 if res.row is not None else 0
        db.commit()
    return n


rules.register_adapter(ConsumerAdapter())
events.register_rule_runner(SCOPE, lambda db, key, payload: rules.run_event(db, SCOPE, key, payload))
dispatch.register_scope(SCOPE, dispatch.ScopeConfig(
    model=EmailMessage, default_from=lambda: settings.email_from_consumer, facts_for_row=_facts_for_row,
))
