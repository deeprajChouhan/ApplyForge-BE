"""Phase 3 auto-apply orchestrator.

`tick_all` fans out one `tick_user` task per active user; `tick_user`
finds candidate jobs, scores them, queues new JobApplication rows up to
the user's daily cap, and hands each newly-queued application off to
the dispatcher (`prepare_application`).

NOTE: field names on AutoApplySettings / AutoApplyRun are assumed per
the Phase 3 spec (these models are being written in parallel). If the
real column names differ, update the attribute accesses below.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

import structlog
from celery import shared_task
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auto_apply import AutoApplyRun, AutoApplySettings
from app.models.job import Job
from app.models.models import JobApplication, User
from app.services.auto_apply.events import emit
from app.services.auto_apply.matching import score_job_for_user

logger = structlog.get_logger(__name__)

CANDIDATE_LIMIT = 200


@shared_task(name="app.services.auto_apply.orchestrator.tick_user")
def tick_user(user_id: int) -> Dict[str, Any]:
    """Score and queue new applications for one user, respecting their daily cap."""
    try:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if user is None:
                return {"error": "user_not_found", "user_id": user_id}

            settings = (
                db.execute(select(AutoApplySettings).where(AutoApplySettings.user_id == user_id))
                .scalars()
                .first()
            )
            if settings is None:
                return {"skipped": True, "reason": "no_settings", "user_id": user_id}
            if not getattr(settings, "is_active", False):
                return {"skipped": True, "reason": "inactive", "user_id": user_id}
            if getattr(settings, "paused_at", None) is not None:
                return {"skipped": True, "reason": "paused", "user_id": user_id}

            run = AutoApplyRun(user_id=user_id, started_at=datetime.utcnow())
            db.add(run)
            db.commit()
            db.refresh(run)

            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            todays_queued_or_submitted = (
                db.execute(
                    select(JobApplication).where(
                        JobApplication.user_id == user_id,
                        JobApplication.auto_apply_stage.in_(["queued", "submitted"]),
                    )
                )
                .scalars()
                .all()
            )
            # Restrict to today where a created_at timestamp is available; if the
            # model doesn't expose one, fall back to counting all matching rows.
            today_count = len(
                [
                    ja
                    for ja in todays_queued_or_submitted
                    if getattr(ja, "created_at", None) is None
                    or getattr(ja, "created_at") >= today_start
                ]
            )

            daily_cap = getattr(settings, "daily_cap", 0) or 0
            if today_count >= daily_cap:
                run.finished_at = datetime.utcnow()
                _set_run_counters(run, jobs_considered=0, jobs_queued=0, jobs_skipped=0)
                db.add(run)
                db.commit()
                return {
                    "skipped": True,
                    "reason": "daily_cap_reached",
                    "user_id": user_id,
                    "today_count": today_count,
                }

            already_applied_subq = (
                select(JobApplication.job_id)
                .where(JobApplication.user_id == user_id, JobApplication.job_id.isnot(None))
            )
            candidates: List[Job] = (
                db.execute(
                    select(Job)
                    .where(Job.is_active == True)  # noqa: E712
                    .where(Job.id.notin_(already_applied_subq))
                    .limit(CANDIDATE_LIMIT)
                )
                .scalars()
                .all()
            )

            min_match_score = getattr(settings, "min_match_score", 60) or 60
            remaining_slots = max(0, daily_cap - today_count)

            jobs_considered = 0
            jobs_queued = 0
            jobs_skipped = 0
            newly_queued_ids: List[int] = []

            for job in candidates:
                jobs_considered += 1
                if jobs_queued >= remaining_slots:
                    jobs_skipped += 1
                    continue

                result = score_job_for_user(user, job, settings)
                score = result["score"]
                if score < min_match_score:
                    jobs_skipped += 1
                    continue

                company = getattr(job, "company", None)
                company_name = getattr(company, "name", None) if company is not None else None

                application = JobApplication(
                    user_id=user.id,
                    company_name=company_name,
                    role_title=job.title,
                    job_description=job.description,
                    # Carry the original apply URL through so the drawer's
                    # "View job" link works and the Playwright submitter can
                    # navigate to it without a separate Job lookup.
                    jd_link=getattr(job, "apply_url", None),
                    status="draft",
                    auto_apply_stage="queued",
                    match_score=score,
                    match_reasons_json=result["reasons"],
                    job_id=job.id,
                )
                db.add(application)
                db.commit()
                db.refresh(application)

                emit(db, application.id, "queued", {"score": score, "band": result["band"]})

                newly_queued_ids.append(application.id)
                jobs_queued += 1

            run.finished_at = datetime.utcnow()
            _set_run_counters(
                run,
                jobs_considered=jobs_considered,
                jobs_queued=jobs_queued,
                jobs_skipped=jobs_skipped,
            )
            db.add(run)
            db.commit()

            # Hand off newly-queued applications to the dispatcher.
            for app_id in newly_queued_ids:
                prepare_application_delay(app_id)

            return {
                "user_id": user_id,
                "jobs_considered": jobs_considered,
                "jobs_queued": jobs_queued,
                "jobs_skipped": jobs_skipped,
            }
    except Exception as exc:
        logger.error("auto_apply.tick_user_failed", user_id=user_id, error=str(exc))
        return {"error": str(exc), "user_id": user_id}


def _set_run_counters(run: AutoApplyRun, jobs_considered: int, jobs_queued: int, jobs_skipped: int) -> None:
    """Populate counters on an AutoApplyRun, tolerant of the actual column names."""
    for attr, value in (
        ("jobs_considered", jobs_considered),
        ("jobs_queued", jobs_queued),
        ("jobs_skipped", jobs_skipped),
    ):
        if hasattr(run, attr):
            setattr(run, attr, value)


def prepare_application_delay(app_id: int) -> None:
    """Indirection so this module doesn't need a hard import cycle with dispatcher."""
    from app.services.auto_apply.dispatcher import prepare_application

    prepare_application.delay(app_id)


@shared_task(name="app.services.auto_apply.orchestrator.tick_all")
def tick_all() -> Dict[str, Any]:
    """Enqueue tick_user for every user with active, unpaused auto-apply settings."""
    try:
        with SessionLocal() as db:
            user_ids = (
                db.execute(
                    select(AutoApplySettings.user_id).where(
                        AutoApplySettings.is_active == True,  # noqa: E712
                        AutoApplySettings.paused_at.is_(None),
                    )
                )
                .scalars()
                .all()
            )

        queued = 0
        for user_id in user_ids:
            tick_user.delay(user_id)
            queued += 1

        return {"queued": queued}
    except Exception as exc:
        logger.error("auto_apply.tick_all_failed", error=str(exc))
        return {"error": str(exc)}
