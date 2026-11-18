"""Periodic email jobs, shared by Celery beat (EMAIL_JOBS_BACKEND=celery) and APScheduler (otherwise)."""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


def load_scopes() -> None:
    """Import both sides so their watchers, handlers and adapters are registered."""
    import app.services.email.consumer  # noqa: F401
    import app.recruiter.services.email  # noqa: F401


def sweep() -> int:
    load_scopes()
    from app.services.email.dispatch import sweep_due

    return sweep_due()


def daily_rules() -> int:
    """09:00 UK — admin automations on the 'Every day' trigger, both scopes."""
    load_scopes()
    from app.db.session import SessionLocal
    from app.services.email.rules import run_daily

    db = SessionLocal()
    try:
        return run_daily(db, "consumer") + run_daily(db, "recruiter")
    finally:
        db.close()


def daily_briefs() -> int:
    load_scopes()
    from app.db.session import SessionLocal
    from app.recruiter.services.email import send_daily_briefs

    db = SessionLocal()
    try:
        return send_daily_briefs(db, date.today())
    finally:
        db.close()


def weekly_digests() -> int:
    load_scopes()
    from app.db.session import SessionLocal
    from app.services.email.consumer import send_weekly_digests

    db = SessionLocal()
    try:
        return send_weekly_digests(db, date.today())
    finally:
        db.close()


def purge_webhook_events(days: int = 30) -> int:
    from datetime import datetime, timedelta

    from app.db.session import SessionLocal
    from app.models.email import OpsEmailWebhookEvent

    db = SessionLocal()
    try:
        n = db.query(OpsEmailWebhookEvent).filter(
            OpsEmailWebhookEvent.created_at < datetime.utcnow() - timedelta(days=days)).delete()
        db.commit()
        return n
    finally:
        db.close()
