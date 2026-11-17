"""Admin cleanup for crawled / discovered jobs.

Two stores fill up from background workers:

* ``jobs`` (+ ``job_sources``) — the shared ATS discovery table written by the
  Celery ``poll_provider`` sweeps (hourly) and ``live_search`` (every 2 min).
* ``crawled_jobs`` — per-user results from the APScheduler job crawler
  (06:00 / 15:00 / 19:00 UTC).

Safety rules (rows we never delete):

* ``jobs`` referenced by any ``job_applications.job_id`` (including soft-deleted
  applications). The auto-apply orchestrator de-dupes "already applied" by
  ``Job.id``; deleting a referenced job would let the next poll re-insert it
  under a new id and auto-apply to the same posting twice.
* ``crawled_jobs`` the user saved, dismissed, or linked to an application —
  deleting a dismissed row would make the next crawl show it to them again.

Deletes run in id batches so a large purge doesn't produce one giant
transaction / binlog event (see the 2026-11-02 binlog incident in ats.tasks).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.job_source import JobSource
from app.models.models import CrawledJob, JobApplication

BATCH_SIZE = 1000


@dataclass
class StoreCounts:
    total: int
    eligible: int
    protected: int


def _cutoff(older_than_hours: int) -> datetime:
    # Both tables store naive UTC on MySQL.
    return datetime.utcnow() - timedelta(hours=older_than_hours)


def _referenced_job_ids():
    return select(JobApplication.job_id).where(JobApplication.job_id.isnot(None))


def _discovered_old(cutoff: datetime):
    return Job.first_seen_at < cutoff


def _discovered_eligible(cutoff: datetime):
    return and_(_discovered_old(cutoff), Job.id.notin_(_referenced_job_ids()))


def _crawled_old(cutoff: datetime):
    return CrawledJob.crawled_at < cutoff


def _crawled_eligible(cutoff: datetime):
    return and_(
        _crawled_old(cutoff),
        or_(CrawledJob.is_saved.is_(False), CrawledJob.is_saved.is_(None)),
        or_(CrawledJob.is_dismissed.is_(False), CrawledJob.is_dismissed.is_(None)),
        CrawledJob.application_id.is_(None),
    )


def _count(db: Session, model, *where) -> int:
    q = select(func.count(model.id))
    for w in where:
        q = q.where(w)
    return int(db.execute(q).scalar() or 0)


class CrawledJobsCleanupService:
    def __init__(self, db: Session):
        self.db = db

    # ── Preview ─────────────────────────────────────────────────────────
    def preview(self, older_than_hours: int) -> dict:
        cutoff = _cutoff(older_than_hours)
        d_total = _count(self.db, Job)
        d_old = _count(self.db, Job, _discovered_old(cutoff))
        d_elig = _count(self.db, Job, _discovered_eligible(cutoff))
        c_total = _count(self.db, CrawledJob)
        c_old = _count(self.db, CrawledJob, _crawled_old(cutoff))
        c_elig = _count(self.db, CrawledJob, _crawled_eligible(cutoff))
        return {
            "older_than_hours": older_than_hours,
            "cutoff": cutoff,
            "discovered_jobs": StoreCounts(d_total, d_elig, d_old - d_elig).__dict__,
            "user_crawled_jobs": StoreCounts(c_total, c_elig, c_old - c_elig).__dict__,
        }

    # ── Delete ──────────────────────────────────────────────────────────
    def purge(
        self,
        older_than_hours: int,
        include_discovered: bool = True,
        include_user_crawled: bool = True,
    ) -> dict:
        cutoff = _cutoff(older_than_hours)
        deleted_discovered = 0
        deleted_sources = 0
        deleted_crawled = 0

        if include_discovered:
            while True:
                ids = list(
                    self.db.execute(
                        select(Job.id).where(_discovered_eligible(cutoff)).limit(BATCH_SIZE)
                    ).scalars()
                )
                if not ids:
                    break
                # Explicit child delete: bulk DELETE skips ORM cascades and
                # SQLite (tests) doesn't enforce ON DELETE CASCADE by default.
                deleted_sources += self.db.execute(
                    delete(JobSource).where(JobSource.job_id.in_(ids))
                ).rowcount or 0
                deleted_discovered += self.db.execute(
                    delete(Job).where(Job.id.in_(ids))
                ).rowcount or 0
                self.db.commit()
                if len(ids) < BATCH_SIZE:
                    break

        if include_user_crawled:
            while True:
                ids = list(
                    self.db.execute(
                        select(CrawledJob.id).where(_crawled_eligible(cutoff)).limit(BATCH_SIZE)
                    ).scalars()
                )
                if not ids:
                    break
                deleted_crawled += self.db.execute(
                    delete(CrawledJob).where(CrawledJob.id.in_(ids))
                ).rowcount or 0
                self.db.commit()
                if len(ids) < BATCH_SIZE:
                    break

        return {
            "older_than_hours": older_than_hours,
            "cutoff": cutoff,
            "deleted_discovered_jobs": deleted_discovered,
            "deleted_job_sources": deleted_sources,
            "deleted_user_crawled_jobs": deleted_crawled,
        }
