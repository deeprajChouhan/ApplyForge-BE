"""
Domain events → email.

Instead of sprinkling send calls across services, we watch ORM changes:
watchers registered here turn an INSERT/UPDATE of a model into a named event
(e.g. ParsedResumeData insert → "resume.parsed"). Events are captured in
after_flush, held on the session, and processed only after the transaction
commits — in a fresh session, so email work can never break or roll back the
request that triggered it.

Each event then goes to:
  1. built-in handlers (code; transactional mail such as welcome / invites)
  2. admin automations (ops_email_rules) for the event's scope

Consumer watchers live in app/services/email/consumer.py, recruiter watchers in
app/recruiter/services/email.py. Each side registers its own; neither reads the
other's tables.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_EVENTS_KEY = "email_events_pending"
_PROCESSING_KEY = "email_processing"


@dataclass(frozen=True)
class EventField:
    name: str
    type: str  # string|number|boolean|enum|list
    label: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventDef:
    key: str
    scope: str  # consumer|recruiter
    label: str
    description: str
    fields: tuple[EventField, ...] = ()
    scheduled: bool = False


EVENTS: dict[str, EventDef] = {}
FACTS: dict[str, tuple[EventField, ...]] = {}


def register_event(d: EventDef) -> None:
    EVENTS[d.key] = d


def register_facts(scope: str, fields: tuple[EventField, ...]) -> None:
    FACTS[scope] = fields


# ── watchers ───────────────────────────────────────────────────────────────
@dataclass
class Watcher:
    model: type
    on: str  # insert|update
    build: Callable[[Any, Callable[[str], tuple[Any, Any] | None]], tuple[str, dict] | list[tuple[str, dict]] | None]
    attrs: tuple[str, ...] = ()


_WATCHERS: list[Watcher] = []
Handler = Callable[[Session, dict], None]
_HANDLERS: dict[str, list[Handler]] = {}
# scope → fn(db, event_key, payload) running admin rules for that scope
_RULE_RUNNERS: dict[str, Callable[[Session, str, dict], None]] = {}


def watch(model: type, on: str, build, attrs: tuple[str, ...] = ()) -> None:
    _WATCHERS.append(Watcher(model, on, build, attrs))


def on_event(key: str):
    def deco(fn: Handler) -> Handler:
        _HANDLERS.setdefault(key, []).append(fn)
        return fn
    return deco


def register_rule_runner(scope: str, fn: Callable[[Session, str, dict], None]) -> None:
    _RULE_RUNNERS[scope] = fn


def emit(db: Session, key: str, payload: dict) -> None:
    """Explicit emit for events that aren't a simple row change. Processed after commit."""
    db.info.setdefault(_EVENTS_KEY, []).append((key, payload))


def _history_getter(obj: Any):
    state = inspect(obj)

    def get(attr: str) -> tuple[Any, Any] | None:
        h = state.attrs[attr].history
        if not h.has_changes():
            return None
        old = h.deleted[0] if h.deleted else None
        new = h.added[0] if h.added else None
        return old, new
    return get


@event.listens_for(Session, "after_flush")
def _capture(session: Session, flush_context) -> None:
    if session.info.get(_PROCESSING_KEY) or not _WATCHERS:
        return
    try:
        out = session.info.setdefault(_EVENTS_KEY, [])
        for w in _WATCHERS:
            pool = session.new if w.on == "insert" else session.dirty
            for obj in pool:
                if not isinstance(obj, w.model):
                    continue
                getter = _history_getter(obj)
                if w.on == "update" and w.attrs and not any(getter(a) for a in w.attrs):
                    continue
                res = w.build(obj, getter)
                if not res:
                    continue
                for key, payload in (res if isinstance(res, list) else [res]):
                    out.append((key, payload))
    except Exception:  # noqa: BLE001 — never break the caller's flush
        logger.exception("email.capture_failed")


@event.listens_for(Session, "after_rollback")
def _drop(session: Session) -> None:
    session.info.pop(_EVENTS_KEY, None)


@event.listens_for(Session, "after_commit")
def _process(session: Session) -> None:
    items = session.info.pop(_EVENTS_KEY, None)
    if not items or session.info.get(_PROCESSING_KEY):
        return
    try:
        bind = session.get_bind()
    except Exception:  # noqa: BLE001
        bind = None
    process(items, bind=bind)


def _new_session(bind=None) -> Session:
    """Same database as the session that produced the events (tests use their own engine)."""
    if bind is not None:
        return Session(bind=bind, autoflush=False)
    from app.db.session import SessionLocal

    return SessionLocal()


def process(items: list[tuple[str, dict]], bind=None) -> None:
    db = _new_session(bind)
    db.info[_PROCESSING_KEY] = True
    try:
        for key, payload in items:
            for h in _HANDLERS.get(key, []):
                try:
                    h(db, payload)
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
                    logger.exception("email.handler_failed event=%s handler=%s", key, getattr(h, "__name__", h))
            d = EVENTS.get(key)
            runner = _RULE_RUNNERS.get(d.scope) if d else None
            if runner:
                try:
                    runner(db, key, payload)
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
                    logger.exception("email.rules_failed event=%s", key)
    finally:
        # Rows queued here are dispatched by dispatch._after_commit on this session.
        db.close()


def describe() -> dict[str, Any]:
    return {
        "events": [
            {"key": d.key, "scope": d.scope, "label": d.label, "description": d.description, "scheduled": d.scheduled,
             "fields": [{"name": f.name, "type": f.type, "label": f.label, "options": list(f.options)} for f in d.fields]}
            for d in EVENTS.values()
        ],
        "facts": {s: [{"name": f.name, "type": f.type, "label": f.label, "options": list(f.options)} for f in fs]
                  for s, fs in FACTS.items()},
    }
