"""
Background work for the Partner API: CV parse + fit scoring for new
applications, and webhook delivery.

PARTNER_JOBS_BACKEND=inline (default) runs the work in-process after the
HTTP response is sent (FastAPI BackgroundTasks) — the request thread never
waits on parsing or LLM calls. PARTNER_JOBS_BACKEND=celery hands the same
functions to the Celery worker (Service Bus/KEDA later), and a beat entry
sweeps due webhook retries.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime

from fastapi import BackgroundTasks

from app.core.config import settings

logger = logging.getLogger(__name__)


@contextmanager
def session_scope():
    """A fresh DB session for background work. Honours test-time get_db overrides."""
    from app.db.session import get_db

    provider = get_db
    try:
        from app.main import app as fastapi_app

        provider = fastapi_app.dependency_overrides.get(get_db, get_db)
    except Exception:  # pragma: no cover - worker without the web app
        pass
    gen = provider()
    db = next(gen)
    try:
        yield db
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def _parse_quality(parsed) -> dict:
    checks = {
        "name": bool(parsed.full_name),
        "email": bool(parsed.email),
        "skills": bool(parsed.skills),
        "experience": bool(parsed.experiences) or parsed.years_experience is not None,
        "summary": bool(parsed.summary or parsed.headline),
    }
    found = [k for k, v in checks.items() if v]
    return {
        "score": round(len(found) / len(checks), 2),
        "fields_found": found,
        "fields_missing": [k for k, v in checks.items() if not v],
        "parsed_by_llm": bool(parsed.parsed_by_llm),
    }


def process_application(application_id: int) -> None:
    """Parse the application's CV, enrich a NEW profile, score against the role."""
    from app.recruiter.models import Application, CandidateDocument, Role
    from app.recruiter.partner import webhooks
    from app.recruiter.services import documents as documents_service
    from app.recruiter.services.ingestion import add_parsed_details
    from app.recruiter.services.matching import embed_candidate, embed_role, score_candidate
    from app.recruiter.services.parsing import parse_cv

    with session_scope() as db:
        app_row = db.get(Application, application_id)
        if app_row is None:
            return
        app_row.processing_status = "processing"
        db.commit()
        try:
            cand = app_row.candidate
            doc = (
                db.query(CandidateDocument)
                .filter(
                    CandidateDocument.agency_id == app_row.agency_id,
                    CandidateDocument.application_id == app_row.id,
                    CandidateDocument.kind == "cv",
                )
                .order_by(CandidateDocument.id.desc())
                .first()
            )
            quality = None
            enrich = bool((app_row.source_detail or {}).get("new_profile"))
            if doc is not None:
                content = documents_service.read_document(doc)
                parsed = parse_cv(content, doc.original_filename)
                quality = _parse_quality(parsed)
                # Only a profile created by THIS submission is enriched from the
                # CV. An existing profile is never modified by an untrusted
                # public form (the new CV is attached for the recruiter instead).
                if enrich:
                    cand.raw_cv_text = parsed.raw_text
                    cand.headline = cand.headline or parsed.headline
                    cand.location = cand.location or parsed.location
                    cand.summary = cand.summary or parsed.summary
                    if cand.years_experience is None:
                        cand.years_experience = parsed.years_experience
                    if not cand.skills:
                        add_parsed_details(db, cand, parsed)
                    db.flush()
                    db.refresh(cand)
                    cand.embedding = embed_candidate(cand)

            role = db.get(Role, app_row.role_id) if app_row.role_id else None
            explanation: dict = {"parse_quality": quality}
            if role is not None and role.agency_id == app_row.agency_id:
                res = score_candidate(role, cand, role.embedding or embed_role(role))
                app_row.fit_score = res.fit_score
                explanation.update(
                    {"reasons": res.reasons, "gaps": res.gaps, "breakdown": res.breakdown}
                )
            app_row.match_explanation = explanation
            app_row.processing_status = "done"
            app_row.processing_error = None
            app_row.last_activity_at = datetime.utcnow()
            webhooks.emit(
                db,
                app_row.agency_id,
                "application.processed",
                {
                    "application_id": app_row.id,
                    "job_id": app_row.role_id,
                    "candidate_id": app_row.candidate_id,
                    "fit_score": app_row.fit_score,
                    "parse_quality": (quality or {}).get("score"),
                },
                client_id=role.client_id if role else None,
                intake_client_id=app_row.intake_client_id,
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("partner_process_application_failed id=%s error=%s", application_id, exc)
            app_row = db.get(Application, application_id)
            if app_row is not None:
                app_row.processing_status = "failed"
                app_row.processing_error = "Processing failed — the recruiter can re-run parsing."
                db.commit()
    deliver_webhooks_now()


def process_candidate(candidate_id: int) -> None:
    """Parse a pushed candidate's CV and enrich the (newly created) profile."""
    from app.recruiter.models import CandidateDocument, CandidateProfile
    from app.recruiter.services import documents as documents_service
    from app.recruiter.services.ingestion import add_parsed_details
    from app.recruiter.services.matching import embed_candidate
    from app.recruiter.services.parsing import parse_cv

    with session_scope() as db:
        cand = db.get(CandidateProfile, candidate_id)
        if cand is None:
            return
        try:
            doc = (
                db.query(CandidateDocument)
                .filter(CandidateDocument.agency_id == cand.agency_id,
                        CandidateDocument.candidate_id == cand.id, CandidateDocument.kind == "cv")
                .order_by(CandidateDocument.id.desc())
                .first()
            )
            if doc is not None:
                parsed = parse_cv(documents_service.read_document(doc), doc.original_filename)
                cand.raw_cv_text = parsed.raw_text
                cand.full_name = cand.full_name or parsed.full_name
                cand.headline = cand.headline or parsed.headline
                cand.location = cand.location or parsed.location
                cand.summary = cand.summary or parsed.summary
                if cand.years_experience is None:
                    cand.years_experience = parsed.years_experience
                if not cand.skills:
                    add_parsed_details(db, cand, parsed)
                db.flush()
                db.refresh(cand)
            cand.embedding = embed_candidate(cand)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("partner_process_candidate_failed id=%s error=%s", candidate_id, exc)


def enrich_role_from_description(role_id: int) -> None:
    """Fill a partner-created role's EMPTY structured fields from its description."""
    from app.recruiter.models import Role
    from app.recruiter.services.jd_parse import parse_jd
    from app.recruiter.services.matching import embed_role
    from app.recruiter.services.skills import normalize_skill

    with session_scope() as db:
        role = db.get(Role, role_id)
        if role is None or not role.description:
            return
        try:
            draft = parse_jd(role.description)
            if not draft.get("used_llm"):
                return
            from app.recruiter.enums import UsageKind
            from app.recruiter.services import usage as usage_service

            usage_service.record(db, role.agency_id, UsageKind.api_jd_parsed)
            if not role.required_skills and draft.get("required_skills"):
                role.required_skills = [normalize_skill(s) for s in draft["required_skills"]][:50]
            if not role.preferred_skills and draft.get("preferred_skills"):
                role.preferred_skills = [normalize_skill(s) for s in draft["preferred_skills"]][:50]
            if role.min_years_experience is None and draft.get("min_years_experience") is not None:
                role.min_years_experience = draft["min_years_experience"]
            if not role.seniority and draft.get("seniority"):
                role.seniority = str(draft["seniority"])[:80]
            role.embedding = embed_role(role)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("partner_enrich_role_failed id=%s error=%s", role_id, exc)


def deliver_webhooks_now(agency_id: int | None = None) -> None:
    from app.recruiter.partner import webhooks

    with session_scope() as db:
        webhooks.deliver_pending(db, agency_id=agency_id)


def purge_old_logs() -> int:
    """Retention for request logs + idempotency records."""
    from datetime import timedelta

    from app.recruiter.models import ApiRequestLog, IdempotencyRecord

    with session_scope() as db:
        cutoff = datetime.utcnow() - timedelta(days=settings.partner_log_retention_days)
        n = db.query(ApiRequestLog).filter(ApiRequestLog.created_at < cutoff).delete()
        db.query(IdempotencyRecord).filter(
            IdempotencyRecord.created_at < datetime.utcnow() - timedelta(hours=24)
        ).delete()
        db.commit()
        return n


# ── Dispatch ────────────────────────────────────────────────────────────
def _use_celery() -> bool:
    return (settings.partner_jobs_backend or "inline").lower() == "celery"


def dispatch_process_application(background: BackgroundTasks, application_id: int) -> None:
    if _use_celery():
        try:
            from app.recruiter.partner.tasks import process_application_task

            process_application_task.delay(application_id)
            return
        except Exception as exc:  # broker down → don't lose the work
            logger.warning("partner_celery_dispatch_failed falling back inline: %s", exc)
    background.add_task(process_application, application_id)


def dispatch_webhooks(background: BackgroundTasks | None, agency_id: int | None = None) -> None:
    if _use_celery():
        try:
            from app.recruiter.partner.tasks import deliver_webhooks_task

            deliver_webhooks_task.delay(agency_id)
            return
        except Exception as exc:
            logger.warning("partner_celery_dispatch_failed falling back inline: %s", exc)
    if background is not None:
        background.add_task(deliver_webhooks_now, agency_id)
