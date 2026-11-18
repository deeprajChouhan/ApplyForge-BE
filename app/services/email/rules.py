"""
Admin automations engine (ops_email_rules), scope-agnostic.

Each product supplies a RuleAdapter that knows its own tables (facts,
recipients, outbox model). The engine only evaluates JSON conditions and
de-duplicates; it never queries product tables itself.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Protocol

from sqlalchemy.orm import Session

from app.models.email import OpsEmailRule
from app.services.email import conditions

logger = logging.getLogger(__name__)

# Per-scope "every day" trigger keys (event keys are global, so each scope has its own).
SCHEDULE_DAILY = {"consumer": "schedule.daily", "recruiter": "rec.schedule.daily"}


@dataclass
class Subject:
    """The entity an event is about (consumer user / recruiter seat)."""
    id: int
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Recipient:
    key: str  # stable id for de-dup, e.g. "u12" / "r7" / "x:ada@example.com"
    email: str
    first_name: str | None = None
    owner: dict[str, Any] = field(default_factory=dict)


class RuleAdapter(Protocol):
    scope: str

    def subject_from_payload(self, db: Session, payload: dict) -> Subject | None: ...
    def facts(self, db: Session, subject: Subject) -> dict: ...
    def recipients(self, db: Session, rule: OpsEmailRule, subject: Subject, payload: dict) -> list[Recipient]: ...
    def queue(self, db: Session, recipient: Recipient, *, template_key: str, ctx: dict, event_key: str,
              rule_id: int, delay_minutes: int, idempotency_key: str) -> Any: ...
    def last_sent(self, db: Session, rule_id: int, recipient: Recipient) -> datetime | None: ...
    def daily_subjects(self, db: Session) -> Iterable[Subject]: ...


ADAPTERS: dict[str, RuleAdapter] = {}


def register_adapter(adapter: RuleAdapter) -> None:
    ADAPTERS[adapter.scope] = adapter


def active_rules(db: Session, scope: str, event_key: str) -> list[OpsEmailRule]:
    return (
        db.query(OpsEmailRule)
        .filter(OpsEmailRule.scope == scope, OpsEmailRule.event_key == event_key, OpsEmailRule.is_active.is_(True))
        .all()
    )


def _event_id(payload: dict) -> str:
    return str(payload.get("_id") or payload.get("date") or datetime.utcnow().strftime("%Y%m%d%H%M%S%f"))


def apply_rule(db: Session, adapter: RuleAdapter, rule: OpsEmailRule, event_key: str, payload: dict,
               subject: Subject | None = None, *, dry_run: bool = False) -> list[Recipient]:
    subject = subject or adapter.subject_from_payload(db, payload)
    if subject is None:
        return []
    facts = adapter.facts(db, subject)
    if not conditions.evaluate(rule.conditions, {"event": payload, "facts": facts}):
        return []
    matched: list[Recipient] = []
    queued = 0
    for rcpt in adapter.recipients(db, rule, subject, payload):
        last = adapter.last_sent(db, rule.id, rcpt)
        if last is not None:
            if rule.once_per_subject:
                continue
            if rule.cooldown_days and last >= datetime.utcnow() - timedelta(days=rule.cooldown_days):
                continue
        matched.append(rcpt)
        if dry_run:
            continue
        idem = (f"rule{rule.id}-{rcpt.key}" if rule.once_per_subject
                else f"rule{rule.id}-v{rule.version}-{_event_id(payload)}-{rcpt.key}")
        ctx = {**facts, **{k: v for k, v in payload.items() if not k.startswith("_")},
               "first_name": rcpt.first_name, "_event": payload}
        res = adapter.queue(db, rcpt, template_key=rule.template_key, ctx=ctx, event_key=event_key,
                            rule_id=rule.id, delay_minutes=rule.delay_minutes or 0, idempotency_key=idem)
        queued += 1 if getattr(res, "row", None) is not None else 0
    if queued and not dry_run:
        rule.fire_count = (rule.fire_count or 0) + queued
        rule.last_fired_at = datetime.utcnow()
    return matched


def run_event(db: Session, scope: str, event_key: str, payload: dict) -> None:
    adapter = ADAPTERS.get(scope)
    if adapter is None:
        return
    for rule in active_rules(db, scope, event_key):
        try:
            with db.begin_nested():
                apply_rule(db, adapter, rule, event_key, payload)
        except Exception:  # noqa: BLE001
            logger.exception("email.rule_failed rule=%s", rule.id)


def run_daily(db: Session, scope: str, *, today: date | None = None) -> int:
    adapter = ADAPTERS.get(scope)
    key = SCHEDULE_DAILY.get(scope, "")
    rules = active_rules(db, scope, key) if adapter else []
    if not rules:
        return 0
    payload = {"date": (today or date.today()).isoformat()}
    n = 0
    for subject in adapter.daily_subjects(db):
        for rule in rules:
            try:
                with db.begin_nested():
                    n += len(apply_rule(db, adapter, rule, key, {**payload, "subject_id": subject.id}, subject))
            except Exception:  # noqa: BLE001
                logger.exception("email.daily_rule_failed rule=%s subject=%s", rule.id, subject.id)
        db.commit()
    return n


def dry_run(db: Session, rule: OpsEmailRule, *, limit: int = 2000) -> dict:
    """How many people would a schedule.daily rule hit right now? (event rules: not computable ahead)."""
    adapter = ADAPTERS.get(rule.scope)
    if adapter is None or rule.event_key != SCHEDULE_DAILY.get(rule.scope):
        return {"supported": False, "count": None, "sample": []}
    payload = {"date": date.today().isoformat()}
    hits: list[str] = []
    for i, subject in enumerate(adapter.daily_subjects(db)):
        if i >= limit:
            break
        hits += [r.email for r in apply_rule(db, adapter, rule, rule.event_key, payload, subject, dry_run=True)]
    return {"supported": True, "count": len(hits), "sample": hits[:10]}
