"""Smoke-test tasks for the ApplyForge Celery worker."""
from __future__ import annotations

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    """Trivial task used to verify the worker/broker/backend wiring."""
    return "pong"


__all__ = ["ping"]
