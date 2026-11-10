"""Celery tasks for the Partner API (used when PARTNER_JOBS_BACKEND=celery)."""
from __future__ import annotations

from app.workers.celery_app import celery_app


@celery_app.task(name="app.recruiter.partner.tasks.process_application", acks_late=True, max_retries=2)
def process_application_task(application_id: int) -> None:
    from app.recruiter.partner.jobs import process_application

    process_application(application_id)


@celery_app.task(name="app.recruiter.partner.tasks.deliver_webhooks")
def deliver_webhooks_task(agency_id: int | None = None) -> None:
    from app.recruiter.partner.jobs import deliver_webhooks_now

    deliver_webhooks_now(agency_id)


@celery_app.task(name="app.recruiter.partner.tasks.purge_old_logs")
def purge_old_logs_task() -> int:
    from app.recruiter.partner.jobs import purge_old_logs

    return purge_old_logs()
