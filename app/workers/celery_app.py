"""Celery application factory for ApplyForge background workers.

No I/O is performed at import time; the broker/backend connections are
established lazily by the Celery library when tasks are dispatched or
consumed.
"""
from __future__ import annotations

import os

from celery import Celery

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

celery_app = Celery(
    "applyforge",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
)

celery_app.autodiscover_tasks(
    packages=[
        "app.services.ats",
        "app.services.auto_apply",
        "app.workers.tasks",
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_max_tasks_per_child=200,
)

# Import here (not at module top) to avoid a circular import between
# celery_app and beat_schedule, since beat_schedule references task names
# only as strings and does not import celery_app itself.
from app.workers.beat_schedule import BEAT_SCHEDULE  # noqa: E402

celery_app.conf.beat_schedule = BEAT_SCHEDULE


__all__ = ["celery_app"]
