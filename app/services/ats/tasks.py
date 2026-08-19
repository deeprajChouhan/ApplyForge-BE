"""Celery tasks that poll ATS providers and upsert companies/jobs.

TODO(phase2): confirm the actual Celery app import path in this repo -- this
assumes the conventional `app.core.celery_app.celery_app`. Adjust the
`@celery_app.task` decorator target if it lives elsewhere.

TODO(phase2): confirm the actual SQLAlchemy model field names for
`Company`/`Job` -- this assumes a reasonably conventional shape (see the
`upsert_company` / `upsert_job` docstrings below for the exact fields
touched). Adjust the model imports and attribute names to match
`app/models/company.py` / `app/models/job.py` once verified against the repo.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.company import Company  # TODO(phase2): verify model location/fields.
from app.models.job import Job  # TODO(phase2): verify model location/fields.
from app.schemas.ats import NormalizedCompany, NormalizedJob
from app.services.ats.registry import get_provider

logger = logging.getLogger(__name__)

# Jobs not re-seen within this window (relative to a poll run) get marked
# inactive, rather than immediately on the first missed poll -- this absorbs
# transient upstream errors/empty pages without incorrectly deactivating
# live postings.
STALE_THRESHOLD = timedelta(days=7)


def upsert_company(db: Session, normalized: NormalizedCompany) -> Company:
    """Insert or update a Company row keyed on (ats_provider, ats_slug).

    Assumes a `Company` model with at least:
        ats_provider: str
        ats_slug: str
        name: str
        careers_url: str | None
    """
    company = db.execute(
        select(Company).where(
            Company.ats_provider == normalized.ats_provider,
            Company.ats_slug == normalized.ats_slug,
        )
    ).scalar_one_or_none()

    if company is None:
        company = Company(
            ats_provider=normalized.ats_provider,
            ats_slug=normalized.ats_slug,
            name=normalized.name,
            careers_url=normalized.careers_url,
        )
        db.add(company)
        db.flush()  # populate PK for use as a Job FK below.
    else:
        company.name = normalized.name
        company.careers_url = normalized.careers_url

    return company


def upsert_job(db: Session, company: Company, normalized: NormalizedJob, now: datetime) -> tuple[Job, bool]:
    """Insert or update a Job row keyed on (ats_provider, external_id).

    Returns (job, is_new).

    Assumes a `Job` model with at least:
        ats_provider: str
        external_id: str
        company_id: FK -> Company.id
        title, location, remote_mode, employment_type, seniority
        salary_min, salary_max, salary_currency
        description, description_html, apply_url
        posted_at, first_seen_at, last_seen_at, is_active
    """
    job = db.execute(
        select(Job).where(
            Job.ats_provider == normalized.ats_provider,
            Job.external_id == normalized.external_id,
        )
    ).scalar_one_or_none()

    is_new = job is None
    if job is None:
        job = Job(
            ats_provider=normalized.ats_provider,
            external_id=normalized.external_id,
            company_id=company.id,
            first_seen_at=now,
        )
        db.add(job)

    job.company_id = company.id
    job.title = normalized.title
    job.location = normalized.location
    job.remote_mode = normalized.remote_mode
    job.employment_type = normalized.employment_type
    job.seniority = normalized.seniority
    job.salary_min = normalized.salary_min
    job.salary_max = normalized.salary_max
    job.salary_currency = normalized.salary_currency
    job.description = normalized.description
    job.description_html = normalized.description_html
    job.apply_url = normalized.apply_url
    job.posted_at = normalized.posted_at
    job.last_seen_at = now
    job.is_active = True

    return job, is_new


def _deactivate_stale_jobs(db: Session, company_ids: list[Any], now: datetime) -> int:
    """Mark jobs for the polled companies inactive if they haven't been seen
    (i.e. `last_seen_at` wasn't bumped) within STALE_THRESHOLD of `now`.

    Using a threshold rather than "not seen in this exact run" avoids
    flapping a job to inactive because of a single transient upstream 5xx.
    """
    if not company_ids:
        return 0

    cutoff = now - STALE_THRESHOLD
    stale_jobs = (
        db.execute(
            select(Job).where(
                Job.company_id.in_(company_ids),
                Job.is_active.is_(True),
                Job.last_seen_at < cutoff,
            )
        )
        .scalars()
        .all()
    )
    for job in stale_jobs:
        job.is_active = False

    return len(stale_jobs)


async def _poll(provider_name: str) -> dict[str, int]:
    provider = get_provider(provider_name)
    now = datetime.now(timezone.utc)

    companies_seen = 0
    jobs_seen = 0
    jobs_new = 0
    jobs_updated = 0
    company_ids: list[Any] = []

    db = SessionLocal()
    try:
        async for normalized_company in provider.list_companies():
            companies_seen += 1
            company = upsert_company(db, normalized_company)
            db.commit()
            company_ids.append(company.id)

            try:
                async for normalized_job in provider.list_jobs(normalized_company):
                    jobs_seen += 1
                    _job, is_new = upsert_job(db, company, normalized_job, now)
                    if is_new:
                        jobs_new += 1
                    else:
                        jobs_updated += 1
                db.commit()
            except Exception:
                # Don't let one company's fetch failure abort the whole
                # provider run -- log, roll back this company's partial
                # writes, and continue to the next one.
                db.rollback()
                logger.exception(
                    "ats.poll_provider: failed to list jobs for %s/%s",
                    provider_name,
                    normalized_company.ats_slug,
                )

        deactivated = _deactivate_stale_jobs(db, company_ids, now)
        db.commit()
        logger.info(
            "ats.poll_provider: %s deactivated %d stale jobs", provider_name, deactivated
        )
    finally:
        db.close()

    return {
        "companies_seen": companies_seen,
        "jobs_seen": jobs_seen,
        "jobs_new": jobs_new,
        "jobs_updated": jobs_updated,
    }


@celery_app.task(name="app.services.ats.tasks.poll_provider")
def poll_provider(provider_name: str) -> dict[str, int]:
    """Poll a single ATS provider: upsert its companies + jobs, and mark
    long-unseen jobs for those companies as inactive.

    Runs its async provider/DB work via `asyncio.run` since Celery tasks
    execute in a plain (non-async) worker context.
    """
    return asyncio.run(_poll(provider_name))
