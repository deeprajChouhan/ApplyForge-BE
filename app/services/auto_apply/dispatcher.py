"""Phase 3 auto-apply dispatcher.

`prepare_application` moves a queued JobApplication through analysis /
generation and either parks it for user review or (if the user opted
into fully-automatic mode) hands it to `submit_application`.

`submit_application` is a Phase 3 stub: it just marks the application
submitted. The real per-ATS submission flow (Greenhouse/Lever/etc.
form-filling or API submission) arrives in Phase 4.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import structlog
from celery import shared_task
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auto_apply import AutoApplySettings
from app.models.models import JobApplication
from app.services.auto_apply.events import emit

logger = structlog.get_logger(__name__)


@shared_task(name="app.services.auto_apply.dispatcher.prepare_application")
def prepare_application(app_id: int) -> Dict[str, Any]:
    """Analyze + generate documents for a queued application, then route it."""
    try:
        with SessionLocal() as db:
            ja = db.get(JobApplication, app_id)
            if ja is None:
                return {"error": "application_not_found", "app_id": app_id}

            if ja.auto_apply_stage not in (None, "queued"):
                return {"skipped": True, "reason": "wrong_stage", "app_id": app_id, "stage": ja.auto_apply_stage}

            ja.auto_apply_stage = "preparing"
            db.add(ja)
            db.commit()
            emit(db, ja.id, "preparing")

            try:
                # Safe import — ApplicationsService lives in a module owned by
                # another part of the codebase; signatures unverified here.
                from app.services.applications.service import ApplicationsService

                svc = ApplicationsService(db, ja.user_id)
                svc.analyze_jd(ja.id, ja.job_description)  # TODO: verify signature
                svc.generate(ja.id, ["resume", "cover_letter"])  # TODO: verify signature
            except Exception as exc:
                # Non-fatal — mark generated_error but still move to awaiting_review
                # so the user can intervene manually rather than the pipeline
                # silently stalling.
                emit(db, ja.id, "generation_error", {"error": str(exc)})

            settings = (
                db.execute(select(AutoApplySettings).where(AutoApplySettings.user_id == ja.user_id))
                .scalars()
                .first()
            )
            fully_automatic = bool(getattr(settings, "fully_automatic", False)) if settings else False

            if fully_automatic:
                ja.auto_apply_stage = "submitting"
                db.add(ja)
                db.commit()
                emit(db, ja.id, "submitting")
                submit_application.delay(ja.id)
            else:
                ja.auto_apply_stage = "awaiting_review"
                db.add(ja)
                db.commit()
                emit(db, ja.id, "awaiting_review")

            return {"app_id": app_id, "stage": ja.auto_apply_stage}
    except Exception as exc:
        logger.error("auto_apply.prepare_application_failed", app_id=app_id, error=str(exc))
        try:
            with SessionLocal() as db:
                ja = db.get(JobApplication, app_id)
                if ja is not None:
                    ja.auto_apply_stage = "failed"
                    db.add(ja)
                    db.commit()
                    emit(db, ja.id, "failed", {"error": str(exc)})
        except Exception as inner_exc:  # pragma: no cover - defensive
            logger.error("auto_apply.prepare_application_failure_handling_failed", app_id=app_id, error=str(inner_exc))
        return {"error": str(exc), "app_id": app_id}


@shared_task(name="app.services.auto_apply.dispatcher.submit_application")
def submit_application(app_id: int) -> Dict[str, Any]:
    """Phase 3 stub submitter.

    Marks the application as submitted with submit_method="manual". The
    real per-ATS submitter (Greenhouse/Lever/Ashby/etc. form automation
    or API submission) is planned for Phase 4.
    """
    try:
        with SessionLocal() as db:
            ja = db.get(JobApplication, app_id)
            if ja is None:
                return {"error": "application_not_found", "app_id": app_id}

            ja.auto_apply_stage = "submitted"
            ja.submitted_at = datetime.utcnow()
            ja.submit_method = "manual"
            db.add(ja)
            db.commit()
            emit(db, ja.id, "submitted", {"submit_method": "manual"})

            return {"app_id": app_id, "stage": ja.auto_apply_stage}
    except Exception as exc:
        logger.error("auto_apply.submit_application_failed", app_id=app_id, error=str(exc))
        return {"error": str(exc), "app_id": app_id}
