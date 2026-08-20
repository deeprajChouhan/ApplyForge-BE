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

            # Analysis + document generation. Split into two try/except
            # blocks so a failure in doc generation doesn't wipe out a
            # successful JD analysis (or vice versa).
            try:
                from app.services.applications.service import ApplicationService

                svc = ApplicationService(db, ja.user_id)
                svc.analyze_jd(ja.id, ja.job_description)
            except Exception as exc:
                emit(db, ja.id, "analysis_error", {"error": str(exc)})

            try:
                from app.services.applications.service import ApplicationService

                svc = ApplicationService(db, ja.user_id)
                svc.generate_docs(ja.id, ["resume", "cover_letter"])
            except Exception as exc:
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
    """Phase 4 real submitter.

    Loads the JobApplication + linked Job + user's profile/resume/cover
    letter, builds a `SubmitContext`, and dispatches to the per-provider
    submitter in `app.services.ats.submitters.registry`.

    Outcome → stage mapping:
      SUBMITTED       → auto_apply_stage="submitted"
      NEEDS_MANUAL    → auto_apply_stage="awaiting_review" (user finishes it)
      NOT_SUPPORTED   → auto_apply_stage="awaiting_review" (no submitter yet)
      FAILED          → auto_apply_stage="failed" (transient; retry later)
    """
    try:
        with SessionLocal() as db:
            ja = db.get(JobApplication, app_id)
            if ja is None:
                return {"error": "application_not_found", "app_id": app_id}

            ctx = _build_submit_context(db, ja)
            if ctx is None:
                # Missing prerequisites (no resume, no linked Job, etc.)
                ja.auto_apply_stage = "awaiting_review"
                db.add(ja)
                db.commit()
                emit(db, ja.id, "submit_prereq_missing", {})
                return {"app_id": app_id, "stage": ja.auto_apply_stage, "reason": "missing_prereqs"}

            from app.services.ats.submitters.registry import get_submitter, get_fallback_submitter
            from app.services.ats.submitters.base import SubmitOutcome

            # Try the provider-native HTTP submitter first. If it doesn't
            # exist for this provider, or if it returns NOT_SUPPORTED for
            # this specific posting, fall through to the Playwright
            # browser-automation submitter which works on almost any form.
            submitter = get_submitter(ctx.ats_provider)
            result = None
            if submitter is not None:
                result = submitter.submit(ctx)
                if result.outcome == SubmitOutcome.NOT_SUPPORTED:
                    # HTTP submitter bailed — try browser automation.
                    result = None

            if result is None:
                emit(db, ja.id, "submit_fallback_playwright", {"provider": ctx.ats_provider})
                result = get_fallback_submitter().submit(ctx)

            if result.outcome == SubmitOutcome.SUBMITTED:
                ja.auto_apply_stage = "submitted"
                ja.submitted_at = datetime.utcnow()
                ja.submit_method = result.method
                if result.evidence_url:
                    ja.submission_evidence_url = result.evidence_url
                db.add(ja)
                db.commit()
                emit(db, ja.id, "submitted", {
                    "submit_method": result.method,
                    "external_reference": result.external_reference,
                })
            elif result.outcome == SubmitOutcome.NEEDS_MANUAL:
                ja.auto_apply_stage = "awaiting_review"
                db.add(ja)
                db.commit()
                emit(db, ja.id, "submit_needs_manual", {
                    "submit_method": result.method,
                    "error": result.error,
                })
            elif result.outcome == SubmitOutcome.FAILED:
                ja.auto_apply_stage = "failed"
                db.add(ja)
                db.commit()
                emit(db, ja.id, "submit_failed", {
                    "submit_method": result.method,
                    "error": result.error,
                })

            return {"app_id": app_id, "stage": ja.auto_apply_stage, "outcome": result.outcome.value}
    except Exception as exc:
        logger.error("auto_apply.submit_application_failed", app_id=app_id, error=str(exc))
        return {"error": str(exc), "app_id": app_id}


def _build_submit_context(db, ja):
    """Assemble a SubmitContext from the DB. Returns None if prerequisites
    (linked Job, applicant email, resume) are missing."""
    from app.models.job import Job
    from app.models.models import GeneratedDocument, ParsedResumeData, UploadedFile, User, UserProfile
    from app.models.auto_apply import AutoApplySettings
    from app.models.enums import DocumentType
    from app.services.ats.submitters.base import SubmitContext

    if not ja.job_id:
        return None
    job = db.get(Job, ja.job_id)
    if job is None or not job.apply_url:
        return None

    user = db.get(User, ja.user_id)
    if user is None or not getattr(user, "email", None):
        return None

    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()

    # Pick the parsed resume:
    # 1. Application-specific selection
    # 2. User's default resume set under Preferences (AutoApplySettings)
    # 3. User's most recent parsed resume fallback
    parsed = None
    sel_id = getattr(ja, "selected_resume_id", None)
    if sel_id:
        parsed = db.get(ParsedResumeData, sel_id)

    if parsed is None:
        auto_settings = db.query(AutoApplySettings).filter(AutoApplySettings.user_id == user.id).first()
        if auto_settings and auto_settings.default_resume_parse_id:
            parsed = db.get(ParsedResumeData, auto_settings.default_resume_parse_id)

    if parsed is None:
        parsed = (
            db.query(ParsedResumeData)
            .filter(ParsedResumeData.user_id == user.id, ParsedResumeData.deleted_at.is_(None))
            .order_by(ParsedResumeData.id.desc())
            .first()
        )

    if parsed is None or not parsed.uploaded_file_id:
        return None

    upload = db.get(UploadedFile, parsed.uploaded_file_id)
    if upload is None:
        return None

    # Resume bytes — read from storage.
    try:
        from app.services.storage import get_storage_service
        storage = get_storage_service()
        resume_bytes = storage.download_bytes(upload.path)
    except Exception as exc:
        logger.warning("submit.resume_download_failed", app_id=ja.id, error=str(exc))
        return None

    # GeneratedDocument uses `version` (no is_current flag)
    cover_doc = (
        db.query(GeneratedDocument)
        .filter(
            GeneratedDocument.application_id == ja.id,
            GeneratedDocument.doc_type == DocumentType.cover_letter,
            GeneratedDocument.deleted_at.is_(None),
        )
        .order_by(GeneratedDocument.version.desc())
        .first()
    )

    company = getattr(job, "company", None)
    company_slug = getattr(company, "ats_slug", None) or ""

    # Parse resume structured_json for contact fallbacks (name, phone, location)
    resume_info = {}
    if parsed and parsed.structured_json:
        try:
            import json
            resume_info = json.loads(parsed.structured_json) if isinstance(parsed.structured_json, str) else (parsed.structured_json or {})
        except Exception:
            resume_info = {}

    contact_info = resume_info.get("contact_info") or resume_info.get("personal_information") or resume_info.get("personal_info") or resume_info

    name_str = (
        (profile.full_name if profile and profile.full_name else None)
        or (contact_info.get("name") if isinstance(contact_info, dict) else None)
        or (contact_info.get("full_name") if isinstance(contact_info, dict) else None)
        or getattr(user, "full_name", None)
        or user.email.split("@")[0]
    )

    phone_str = (
        (profile.phone_number if profile and profile.phone_number else None)
        or (contact_info.get("phone") if isinstance(contact_info, dict) else None)
        or (contact_info.get("phone_number") if isinstance(contact_info, dict) else None)
        or getattr(user, "phone", None)
    )

    location_str = (
        (profile.location if profile and profile.location else None)
        or (contact_info.get("location") if isinstance(contact_info, dict) else None)
        or (contact_info.get("city") if isinstance(contact_info, dict) else None)
        or ""
    )

    extras = {}
    if location_str:
        extras["location"] = location_str
        extras["city"] = location_str

    return SubmitContext(
        apply_url=job.apply_url,
        ats_provider=job.ats_provider or "",
        ats_external_id=job.external_id or "",
        ats_company_slug=company_slug,
        applicant_name=name_str,
        applicant_email=user.email,
        applicant_phone=phone_str,
        resume_bytes=resume_bytes,
        resume_filename=upload.filename or "resume.pdf",
        resume_mime=upload.content_type or "application/pdf",
        cover_letter_text=cover_doc.content if cover_doc else None,
        extras=extras,
    )
