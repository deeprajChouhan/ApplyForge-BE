"""
Recruiter side of email: watchers, built-in sends, facts, preferences and the
rule adapter. Reads rec_* tables only — the shared email layer
(app/services/email/*) never touches these tables directly.

Three kinds of recipient:
  • recruiter seats (owner / recruiter)        → recruiter theme
  • external people (candidates, clients)      → agency-branded theme, reply-to recruiter
  • internal ops alerts (info@)                → ops theme
All rows land in rec_email_messages and cascade with the agency.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.email import OpsEmailRule
from app.recruiter.enums import AgencyStatus, NotificationKind, RecruiterSeatRole
from app.recruiter.models import (
    Agency,
    AgencyInvite,
    Application,
    CandidateProfile,
    Notification,
    RecEmailMessage,
    RecEmailPreference,
    Recruiter,
    Role,
)
from app.services.email import dispatch, events, rules
from app.services.email.events import EventDef, EventField
from app.services.email.outbox import CATEGORY_PREF, QueueResult, queue_email
from app.services.email.rules import Recipient, Subject
from app.services.email.templates import resolve
from app.services.email.themes import OPS, RECRUITER, Theme, agency_theme

logger = logging.getLogger(__name__)
SCOPE = "recruiter"

# Notification kinds that are too chatty for email by default (still in-app).
DEFAULT_MUTED_KINDS = {NotificationKind.stale_pipeline.value, NotificationKind.other.value}

NOTIFICATION_PATHS = {
    "interview": "/interviews", "offer": "/offers", "placement": "/placements", "submission": "/submissions",
    "task": "/tasks", "application": "/pipeline", "role": "/roles", "client": "/clients", "candidate": "/pool",
}


def _enum(v: Any) -> Any:
    return getattr(v, "value", v)


def theme() -> Theme:
    return replace(RECRUITER, app_url=settings.recruiter_app_url.rstrip("/"))


def first_name(r: Recruiter | None) -> str | None:
    name = (r.full_name or "").strip() if r else ""
    return name.split()[0] if name else None


# ── preferences ────────────────────────────────────────────────────────────
def get_prefs(db: Session, recruiter: Recruiter) -> RecEmailPreference:
    p = db.query(RecEmailPreference).filter(RecEmailPreference.recruiter_id == recruiter.id).first()
    if p is None:
        p = RecEmailPreference(agency_id=recruiter.agency_id, recruiter_id=recruiter.id,
                               notifications=True, digests=True, product_updates=True,
                               muted_kinds=sorted(DEFAULT_MUTED_KINDS))
        db.add(p)
        db.flush()
    return p


def allowed(db: Session, recruiter_id: int, category: str, kind: str | None = None) -> bool:
    p = db.query(RecEmailPreference).filter(RecEmailPreference.recruiter_id == recruiter_id).first()
    if kind is not None:
        muted = set(p.muted_kinds or []) if p and p.muted_kinds is not None else DEFAULT_MUTED_KINDS
        if kind in muted:
            return False
    attr = CATEGORY_PREF.get(category)
    if attr is None or p is None:
        return True
    return bool(getattr(p, attr))


# ── queueing ───────────────────────────────────────────────────────────────
def queue_for_recruiter(
    db: Session,
    recruiter: Recruiter,
    template_key: str,
    ctx: dict[str, Any],
    *,
    idempotency_key: str,
    event_key: str | None = None,
    rule_id: int | None = None,
    delay_minutes: int = 0,
    kind: str | None = None,
    respect_prefs: bool = True,
) -> QueueResult:
    spec = resolve(db, template_key)
    if spec is None:
        return QueueResult(None, "missing_template")
    if not recruiter.is_active:
        return QueueResult(None, "inactive")
    if respect_prefs and not allowed(db, recruiter.id, spec.category, kind):
        return QueueResult(None, "opted_out")
    agency = db.get(Agency, recruiter.agency_id)
    return queue_email(
        db,
        model=RecEmailMessage,
        scope=SCOPE,
        owner={"agency_id": recruiter.agency_id, "recruiter_id": recruiter.id, "is_external": False},
        subject_id=recruiter.id,
        to_email=recruiter.email,
        template_key=template_key,
        ctx={"first_name": first_name(recruiter), "email": recruiter.email,
             "agency_name": agency.name if agency else "", **ctx},
        theme=theme(),
        idempotency_key=idempotency_key,
        event_key=event_key,
        rule_id=rule_id,
        delay_minutes=delay_minutes,
        footer_reason=f"You're receiving this because you have a seat at {agency.name if agency else 'an agency'} on ApplyForge Recruiter.",
        prefs_path="/settings/email",
        cap_filter=[RecEmailMessage.recruiter_id == recruiter.id],
    )


def queue_to_address(
    db: Session,
    agency: Agency,
    to_email: str,
    template_key: str,
    ctx: dict[str, Any],
    *,
    idempotency_key: str,
    external: bool,
    reply_to: str | None = None,
    event_key: str | None = None,
    rule_id: int | None = None,
    delay_minutes: int = 0,
    ops: bool = False,
) -> QueueResult:
    """Mail to someone without a seat: an invitee, a candidate/client (external) or ops (internal)."""
    if ops:
        t = replace(OPS, app_url=settings.consumer_app_url.rstrip("/"))
        from_addr, footer = None, None
    elif external:
        t = replace(agency_theme(agency.name, agency.primary_color, agency.logo_url), app_url="")
        from_addr = f"{agency.name} via ApplyForge <{settings.email_external_from_local}@{settings.email_domain}>"
        footer = f"Sent by {agency.name} using ApplyForge Recruiter. Reply to this email to reach them directly."
    else:
        t = theme()
        from_addr, footer = None, None
    return queue_email(
        db,
        model=RecEmailMessage,
        scope=SCOPE,
        owner={"agency_id": agency.id, "recruiter_id": None, "is_external": external},
        subject_id=None,
        to_email=to_email,
        template_key=template_key,
        ctx={"agency_name": agency.name, **ctx},
        theme=t,
        idempotency_key=idempotency_key,
        event_key=event_key,
        rule_id=rule_id,
        delay_minutes=delay_minutes,
        from_addr=from_addr,
        reply_to=reply_to,
        footer_reason=footer,
    )


def owners(db: Session, agency_id: int) -> list[Recruiter]:
    return (db.query(Recruiter)
            .filter(Recruiter.agency_id == agency_id, Recruiter.role == RecruiterSeatRole.owner, Recruiter.is_active.is_(True))
            .all())


def seats(db: Session, agency_id: int) -> list[Recruiter]:
    return db.query(Recruiter).filter(Recruiter.agency_id == agency_id, Recruiter.is_active.is_(True)).all()


# ── facts ──────────────────────────────────────────────────────────────────
def facts(db: Session, agency: Agency, recruiter: Recruiter | None) -> dict[str, Any]:
    now = datetime.utcnow()
    created = agency.created_at or now
    return {
        "plan": _enum(agency.plan),
        "agency_status": _enum(agency.status),
        "subscription_status": agency.subscription_status,
        "days_since_signup": (now - created).days,
        "trial_days_left": (agency.trial_ends_at - now).days if agency.trial_ends_at else None,
        "seats_used": db.query(func.count(Recruiter.id)).filter(Recruiter.agency_id == agency.id, Recruiter.is_active.is_(True)).scalar() or 0,
        "seat_limit": agency.seat_limit,
        "candidates_count": db.query(func.count(CandidateProfile.id)).filter(CandidateProfile.agency_id == agency.id).scalar() or 0,
        "roles_count": db.query(func.count(Role.id)).filter(Role.agency_id == agency.id).scalar() or 0,
        "applications_count": db.query(func.count(Application.id)).filter(Application.agency_id == agency.id).scalar() or 0,
        "seat_role": _enum(recruiter.role) if recruiter else None,
    }


FACT_FIELDS = (
    EventField("plan", "enum", "Agency plan", ("free", "pro", "enterprise")),
    EventField("agency_status", "enum", "Agency status", tuple(s.value for s in AgencyStatus)),
    EventField("subscription_status", "enum", "Subscription", ("trialing", "active", "past_due", "canceled")),
    EventField("days_since_signup", "number", "Days since agency sign-up"),
    EventField("trial_days_left", "number", "Trial days left"),
    EventField("seats_used", "number", "Seats in use"),
    EventField("seat_limit", "number", "Seat limit"),
    EventField("candidates_count", "number", "Candidates in pool"),
    EventField("roles_count", "number", "Roles"),
    EventField("applications_count", "number", "Pipeline entries"),
    EventField("seat_role", "enum", "Seat role", ("owner", "recruiter")),
)


# ── rule adapter ───────────────────────────────────────────────────────────
class RecruiterAdapter:
    scope = SCOPE

    def subject_from_payload(self, db: Session, payload: dict) -> Subject | None:
        rid = payload.get("recruiter_id") or (payload.get("subject_id") if payload.get("subject_kind") == "recruiter" else None)
        recruiter = db.get(Recruiter, int(rid)) if rid else None
        agency = db.get(Agency, recruiter.agency_id if recruiter else int(payload.get("agency_id") or 0)) if (recruiter or payload.get("agency_id")) else None
        if agency is None:
            return None
        if recruiter is None:
            o = owners(db, agency.id)
            recruiter = o[0] if o else None
        return Subject(id=recruiter.id if recruiter else -agency.id, data={"agency": agency, "recruiter": recruiter})

    def facts(self, db: Session, subject: Subject) -> dict:
        return facts(db, subject.data["agency"], subject.data["recruiter"])

    def recipients(self, db: Session, rule: OpsEmailRule, subject: Subject, payload: dict) -> list[Recipient]:
        agency: Agency = subject.data["agency"]
        rec: Recruiter | None = subject.data["recruiter"]
        if rule.recipient == "fixed" and rule.fixed_to:
            return [Recipient(key=f"fixed:{rule.fixed_to.lower()}:a{agency.id}", email=rule.fixed_to, owner={"agency": agency, "ops": True})]
        if rule.recipient == "external" and payload.get("external_email"):
            e = str(payload["external_email"]).lower()
            return [Recipient(key=f"x:{e}:a{agency.id}", email=e, owner={"agency": agency, "external": True,
                                                                         "reply_to": rec.email if rec else None})]
        if rule.recipient == "agency_owner":
            people = owners(db, agency.id)
        elif rule.recipient == "agency_all":
            people = seats(db, agency.id)
        else:
            people = [rec] if rec else []
        return [Recipient(key=f"r{p.id}", email=p.email, first_name=first_name(p), owner={"recruiter": p}) for p in people]

    def queue(self, db, recipient, *, template_key, ctx, event_key, rule_id, delay_minutes, idempotency_key):
        o = recipient.owner
        if "recruiter" in o:
            return queue_for_recruiter(db, o["recruiter"], template_key, ctx, idempotency_key=idempotency_key,
                                       event_key=event_key, rule_id=rule_id, delay_minutes=delay_minutes)
        return queue_to_address(db, o["agency"], recipient.email, template_key, ctx, idempotency_key=idempotency_key,
                                external=bool(o.get("external")), reply_to=o.get("reply_to"), ops=bool(o.get("ops")),
                                event_key=event_key, rule_id=rule_id, delay_minutes=delay_minutes)

    def last_sent(self, db: Session, rule_id: int, recipient: Recipient) -> datetime | None:
        q = db.query(func.max(RecEmailMessage.created_at)).filter(
            RecEmailMessage.rule_id == rule_id, RecEmailMessage.to_email == recipient.email.lower(),
            RecEmailMessage.status.in_(("queued", "sending", "sent", "delivered", "opened", "clicked")))
        if "agency" in recipient.owner:
            q = q.filter(RecEmailMessage.agency_id == recipient.owner["agency"].id)
        return q.scalar()

    def daily_subjects(self, db: Session) -> Iterable[Subject]:
        q = (db.query(Recruiter, Agency).join(Agency, Agency.id == Recruiter.agency_id)  # both rec_* — inside the wall
             .filter(Recruiter.is_active.is_(True), Agency.status == AgencyStatus.active).order_by(Recruiter.id))
        for r, a in q.yield_per(200):
            yield Subject(id=r.id, data={"agency": a, "recruiter": r})


def _facts_for_row(db: Session, row: RecEmailMessage) -> dict:
    agency = db.get(Agency, row.agency_id)
    rec = db.get(Recruiter, row.recruiter_id) if row.recruiter_id else None
    return facts(db, agency, rec) if agency else {}


# ── events ─────────────────────────────────────────────────────────────────
for d in (
    EventDef("rec.agency.created", SCOPE, "Agency signed up", "An agency was created (self-serve or by an operator).",
             (EventField("pending", "boolean", "Awaiting approval"),)),
    EventDef("rec.agency.status_changed", SCOPE, "Agency status changed", "Approved, suspended or reactivated.",
             (EventField("old_status", "enum", "From", tuple(s.value for s in AgencyStatus)),
              EventField("new_status", "enum", "To", tuple(s.value for s in AgencyStatus)))),
    EventDef("rec.invite.created", SCOPE, "Seat invite sent", "An owner invited someone to the agency."),
    EventDef("rec.invite.accepted", SCOPE, "Seat invite accepted", "An invitee claimed their seat."),
    EventDef("rec.password_changed", SCOPE, "Password changed", "A recruiter's password changed."),
    EventDef("rec.notification.created", SCOPE, "Pipeline notification", "Any in-app notification (interviews, offers, feedback, new applicants…).",
             (EventField("kind", "enum", "Kind", tuple(k.value for k in NotificationKind)),
              EventField("priority", "enum", "Priority", ("low", "medium", "high", "urgent")),
              EventField("title", "string", "Title"))),
    EventDef(rules.SCHEDULE_DAILY[SCOPE], SCOPE, "Every day (per recruiter)", "Runs once a day at 09:00 UK for every active seat in an active agency.", scheduled=True),
):
    events.register_event(d)
events.register_facts(SCOPE, FACT_FIELDS)


def _w_agency_new(obj: Agency, h):
    return "rec.agency.created", {"agency_id": obj.id, "pending": _enum(obj.status) == "pending", "_id": f"agency{obj.id}"}


def _w_agency_status(obj: Agency, h):
    ch = h("status")
    if not ch or ch[0] is None:
        return None
    return "rec.agency.status_changed", {"agency_id": obj.id, "old_status": _enum(ch[0]), "new_status": _enum(ch[1]),
                                         "_id": f"agst{obj.id}-{_enum(ch[1])}-{datetime.utcnow():%Y%m%d%H%M%S}"}


def _w_invite_new(obj: AgencyInvite, h):
    return "rec.invite.created", {"agency_id": obj.agency_id, "invite_id": obj.id, "_id": f"inv{obj.id}"}


def _w_invite_status(obj: AgencyInvite, h):
    ch = h("status")
    if not ch or ch[1] != "accepted":
        return None
    return "rec.invite.accepted", {"agency_id": obj.agency_id, "invite_id": obj.id, "email": obj.email, "_id": f"inva{obj.id}"}


def _w_rec_pw(obj: Recruiter, h):
    ch = h("password_hash")
    if not ch or ch[0] is None:
        return None
    return "rec.password_changed", {"agency_id": obj.agency_id, "recruiter_id": obj.id,
                                    "_id": f"rpw{obj.id}-{datetime.utcnow():%Y%m%d%H%M%S}"}


def _w_notification(obj: Notification, h):
    return "rec.notification.created", {
        "agency_id": obj.agency_id, "notification_id": obj.id, "recruiter_id": obj.recruiter_id,
        "kind": _enum(obj.kind), "priority": _enum(obj.priority), "title": obj.title, "body": obj.body,
        "parent_kind": obj.parent_kind, "_id": f"notif{obj.id}",
    }


events.watch(Agency, "insert", _w_agency_new)
events.watch(Agency, "update", _w_agency_status, attrs=("status",))
events.watch(AgencyInvite, "insert", _w_invite_new)
events.watch(AgencyInvite, "update", _w_invite_status, attrs=("status",))
events.watch(Recruiter, "update", _w_rec_pw, attrs=("password_hash",))
events.watch(Notification, "insert", _w_notification)


# ── built-in sends ─────────────────────────────────────────────────────────
def _mailbox(name: str) -> str:
    return f"{name}@{settings.email_domain}"


@events.on_event("rec.agency.created")
def _agency_created(db: Session, p: dict) -> None:
    agency = db.get(Agency, p["agency_id"])
    if agency is None:
        return
    for o in owners(db, agency.id):
        queue_for_recruiter(db, o, "r_welcome", {"pending": p["pending"]},
                            idempotency_key=f"r_welcome-a{agency.id}-r{o.id}", event_key="rec.agency.created")
    if p["pending"]:
        o = owners(db, agency.id)
        who = f"{o[0].full_name or o[0].email} ({o[0].email})" if o else "unknown owner"
        queue_to_address(db, agency, _mailbox("info"), "o_internal_alert",
                         {"title": "New agency awaiting approval", "body": f"**{agency.name}** — {who} signed up.",
                          "admin_url": f"{settings.consumer_app_url.rstrip('/')}/admin/agencies"},
                         idempotency_key=f"o_pending-a{agency.id}", external=False, ops=True)


@events.on_event("rec.agency.status_changed")
def _agency_status(db: Session, p: dict) -> None:
    agency = db.get(Agency, p["agency_id"])
    if agency is None:
        return
    old, new = p["old_status"], p["new_status"]
    if old == "pending" and new == "active":
        key, ctx = "r_agency_approved", {}
    elif new == "suspended":
        key, ctx = "r_agency_status", {"status_label": "suspended"}
    elif old == "suspended" and new == "active":
        key, ctx = "r_agency_status", {"status_label": "reactivated"}
    else:
        return
    for o in owners(db, agency.id):
        queue_for_recruiter(db, o, key, ctx, idempotency_key=f"{key}-{p['_id']}-r{o.id}", event_key="rec.agency.status_changed")


@events.on_event("rec.invite.created")
def _invite(db: Session, p: dict) -> None:
    from app.recruiter.services.onboarding import invite_link

    inv = db.get(AgencyInvite, p["invite_id"])
    agency = db.get(Agency, inv.agency_id) if inv else None
    if not inv or not agency or inv.status != "pending":
        return
    o = owners(db, agency.id)
    inviter = (o[0].full_name or o[0].email) if o else agency.name
    queue_to_address(
        db, agency, inv.email, "r_invite",
        {"inviter_name": inviter, "role_label": "an owner" if _enum(inv.role) == "owner" else "a recruiter",
         "invite_url": invite_link(inv.token), "expires": f"{inv.expires_at:%d %b %Y}" if inv.expires_at else "in 7 days"},
        idempotency_key=f"r_invite-{inv.id}", external=False, reply_to=o[0].email if o else None,
        event_key="rec.invite.created",
    )


@events.on_event("rec.invite.accepted")
def _invite_accepted(db: Session, p: dict) -> None:
    agency = db.get(Agency, p["agency_id"])
    member = db.query(Recruiter).filter(func.lower(Recruiter.email) == (p.get("email") or "").lower()).first()
    if not agency or not member:
        return
    f = facts(db, agency, member)
    for o in owners(db, agency.id):
        if o.id == member.id:
            continue
        queue_for_recruiter(db, o, "r_invite_accepted",
                            {"member_name": member.full_name or member.email, "member_email": member.email,
                             "seats_used": f["seats_used"], "seat_limit": f["seat_limit"]},
                            idempotency_key=f"r_inv_acc-{p['invite_id']}-r{o.id}", event_key="rec.invite.accepted")


@events.on_event("rec.password_changed")
def _rec_pw(db: Session, p: dict) -> None:
    r = db.get(Recruiter, p["recruiter_id"])
    if r:
        queue_for_recruiter(db, r, "r_password_changed", {"when": f"{datetime.utcnow():%d %b %Y %H:%M}"},
                            idempotency_key=f"r_pwchg-{p['_id']}", event_key="rec.password_changed")


@events.on_event("rec.notification.created")
def _notification(db: Session, p: dict) -> None:
    targets = [db.get(Recruiter, p["recruiter_id"])] if p.get("recruiter_id") else seats(db, p["agency_id"])
    link = f"{settings.recruiter_app_url.rstrip('/')}{NOTIFICATION_PATHS.get(p.get('parent_kind') or '', '/dashboard')}"
    for r in targets:
        if r is None:
            continue
        queue_for_recruiter(db, r, "r_notification",
                            {"title": p["title"], "body": p.get("body") or "", "link": link, "kind": p["kind"]},
                            idempotency_key=f"r_notif-{p['notification_id']}-r{r.id}", event_key="rec.notification.created",
                            kind=p["kind"])


def lookup_recruiter(db: Session, email: str) -> dict | None:
    """Mailbox sender context (recruiter side). Returns plain values only."""
    r = db.query(Recruiter).filter(func.lower(Recruiter.email) == email.lower()).first()
    if r is None:
        return None
    a = db.get(Agency, r.agency_id)
    return {"recruiter_id": r.id, "full_name": r.full_name, "seat_role": _enum(r.role), "is_active": r.is_active,
            "agency_id": a.id if a else None, "agency_name": a.name if a else None,
            "agency_plan": _enum(a.plan) if a else None, "agency_status": _enum(a.status) if a else None,
            "subscription_status": a.subscription_status if a else None}


def send_password_reset(db: Session, recruiter: Recruiter) -> None:
    from app.services.email.tokens import make_reset_token

    token = make_reset_token("recruiter", recruiter.id, recruiter.password_hash)
    url = f"{settings.recruiter_app_url.rstrip('/')}/reset-password?token={token}"
    queue_for_recruiter(db, recruiter, "r_password_reset", {"reset_url": url},
                        idempotency_key=f"r_pwreset-r{recruiter.id}-{datetime.utcnow():%Y%m%d%H%M%S%f}")


# ── built-in digest: daily brief (weekdays) ────────────────────────────────
def send_daily_briefs(db: Session, today: date | None = None) -> int:
    from app.recruiter.services.productivity import daily_brief

    today = today or date.today()
    if today.weekday() >= 5:
        return 0
    n = 0
    for agency in db.query(Agency).filter(Agency.status == AgencyStatus.active).yield_per(100):
        people = [r for r in seats(db, agency.id) if allowed(db, r.id, "digest")]
        if not people:
            continue
        try:
            brief = daily_brief(db, agency.id)
        except Exception:  # noqa: BLE001
            logger.exception("email.daily_brief_failed agency=%s", agency.id)
            continue
        items = brief["urgent"] + brief["high"] + brief["medium"]
        if not items and not brief["opportunities"]:
            continue  # nothing to say — don't send an empty brief
        ctx = {
            "stats": [{"label": "Urgent", "value": len(brief["urgent"])}, {"label": "High", "value": len(brief["high"])},
                      {"label": "To do", "value": len(brief["medium"])}],
            "table": {"columns": ["Priority", "What", "Detail"],
                      "rows": [[i["priority"].title(), i["title"], i.get("detail") or ""] for i in items[:12]]},
        }
        for r in people:
            res = queue_for_recruiter(db, r, "r_daily_brief", ctx, idempotency_key=f"r_brief-r{r.id}-{today.isoformat()}",
                                      event_key="schedule.daily_brief")
            n += 1 if res.row is not None else 0
        db.commit()
    return n


rules.register_adapter(RecruiterAdapter())
events.register_rule_runner(SCOPE, lambda db, key, payload: rules.run_event(db, SCOPE, key, payload))
dispatch.register_scope(SCOPE, dispatch.ScopeConfig(
    model=RecEmailMessage, default_from=lambda: settings.email_from_recruiter, facts_for_row=_facts_for_row,
))
