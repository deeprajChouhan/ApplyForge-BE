"""Celery tasks for email (used when EMAIL_JOBS_BACKEND=celery)."""
from __future__ import annotations

from app.workers.celery_app import celery_app


@celery_app.task(name="app.services.email.tasks.send_email", acks_late=True)
def send_email_task(scope: str, row_id: int) -> str:
    from app.services.email.dispatch import send_one
    from app.services.email.jobs import load_scopes

    load_scopes()
    return send_one(scope, row_id)


@celery_app.task(name="app.services.email.tasks.sweep")
def sweep_task() -> int:
    from app.services.email.jobs import sweep

    return sweep()


@celery_app.task(name="app.services.email.tasks.daily_rules")
def daily_rules_task() -> int:
    from app.services.email.jobs import daily_rules

    return daily_rules()


@celery_app.task(name="app.services.email.tasks.daily_briefs")
def daily_briefs_task() -> int:
    from app.services.email.jobs import daily_briefs

    return daily_briefs()


@celery_app.task(name="app.services.email.tasks.weekly_digests")
def weekly_digests_task() -> int:
    from app.services.email.jobs import weekly_digests

    return weekly_digests()


@celery_app.task(name="app.services.email.tasks.purge_webhook_events")
def purge_webhook_events_task() -> int:
    from app.services.email.jobs import purge_webhook_events

    return purge_webhook_events()
