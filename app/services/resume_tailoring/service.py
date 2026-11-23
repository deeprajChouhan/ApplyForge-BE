"""
DB layer for resume tailoring: load inputs, run the pipeline, persist.

Every query filters by user_id. The tailored resume lives in its own table
(application_tailorings) as an overlay, so the user's master profile, parsed
resume and accepted suggestions are never mutated — "Show original" is one
toggle away.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

import structlog
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.enums import DocumentType
from app.models.models import ApplicationTailoring, GeneratedDocument, JobApplication
from app.services.ai.factory import get_llm_provider
from app.services.resume_tailoring.pipeline import ResumeTailoringPipeline
from app.services.resume_tailoring.source import (
    load_customizations, load_source_resume, resolve_parsed_resume,
)

logger = structlog.get_logger(__name__)


def _jd_hash(jd: str | None) -> str:
    return hashlib.sha1((jd or "").strip().encode("utf-8")).hexdigest()[:16]


def _loads(s: str | None) -> dict:
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


class ResumeTailoringService:
    def __init__(self, db: Session, user_id: int, llm=None):
        self.db = db
        self.user_id = user_id
        self.llm = llm or get_llm_provider()

    # ── reads ──────────────────────────────────────────────────────────
    def _app(self, app_id: int) -> JobApplication:
        app = (
            self.db.query(JobApplication)
            .filter(JobApplication.id == app_id, JobApplication.user_id == self.user_id,
                    JobApplication.deleted_at.is_(None))
            .first()
        )
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        return app

    def _row(self, app_id: int) -> ApplicationTailoring | None:
        return (
            self.db.query(ApplicationTailoring)
            .filter_by(user_id=self.user_id, application_id=app_id)
            .first()
        )

    def stale_reasons(self, app: JobApplication, row: ApplicationTailoring) -> list[str]:
        reasons: list[str] = []
        report = _loads(row.report_json)
        current = resolve_parsed_resume(self.db, self.user_id, app)
        if (current.id if current else None) != row.source_resume_id:
            reasons.append("The resume selected for this application changed")
        if report.get("source", {}).get("jd_hash") not in (None, _jd_hash(app.job_description)):
            reasons.append("The job description changed")
        _, cust_updated = load_customizations(self.db, self.user_id, app.id)
        if cust_updated and row.updated_at and cust_updated > row.updated_at:
            reasons.append("AI suggestions were applied after tailoring")
        return reasons

    def to_out(self, app: JobApplication, row: ApplicationTailoring) -> dict:
        reasons = self.stale_reasons(app, row)
        return {
            "application_id": app.id,
            "enabled": bool(row.enabled),
            "source_resume_id": row.source_resume_id,
            "generated_document_id": row.generated_document_id,
            "tailored": _loads(row.tailored_json),
            "report": _loads(row.report_json),
            "is_stale": bool(reasons),
            "stale_reasons": reasons,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def get(self, app_id: int) -> dict | None:
        app = self._app(app_id)
        row = self._row(app_id)
        return self.to_out(app, row) if row else None

    def active_overlay(self, app_id: int) -> dict | None:
        """Overlay for the exporter — None when absent or switched off."""
        row = self._row(app_id)
        if not row or not row.enabled:
            return None
        return _loads(row.tailored_json) or None

    # ── writes ─────────────────────────────────────────────────────────
    def tailor(self, app_id: int) -> tuple[dict, GeneratedDocument]:
        app = self._app(app_id)

        if not app.jd_analysis_json:
            # Tailoring is driven by the JD analysis — run it first (also persists scores).
            # If the analysis infrastructure is down (e.g. vector search), carry on: the
            # MAP step extracts the terms from the raw JD itself.
            from app.services.applications.service import ApplicationService
            try:
                ApplicationService(self.db, self.user_id, llm=self.llm).analyze_jd(app.id, app.job_description or "")
            except HTTPException as exc:
                if exc.status_code < 500:
                    raise
                logger.warning("tailoring_jd_analysis_unavailable", application_id=app.id, detail=str(exc.detail)[:200])
                self.db.rollback()
            self.db.refresh(app)

        jd_analysis = _loads(app.jd_analysis_json)
        src = load_source_resume(self.db, self.user_id, app)
        if not (src.experiences or src.skills or src.summary or src.projects):
            raise HTTPException(
                status_code=422,
                detail="No resume data found. Upload a resume (or fill in your profile) before tailoring.",
            )

        jd = app.job_description or ""
        role_title, company = app.role_title or "", app.company_name or ""
        application_id = app.id

        result = ResumeTailoringPipeline(self.llm).run(src, jd, jd_analysis, role_title, company)
        result.report["source"]["jd_hash"] = _jd_hash(jd)
        result.report["generated_at"] = datetime.utcnow().isoformat() + "Z"

        version = (
            self.db.query(GeneratedDocument)
            .filter_by(user_id=self.user_id, application_id=application_id, doc_type=DocumentType.resume)
            .count()
        ) + 1
        doc = GeneratedDocument(
            user_id=self.user_id,
            application_id=application_id,
            doc_type=DocumentType.resume,
            version=version,
            content=result.text,
            format="txt",
        )
        self.db.add(doc)
        self.db.flush()

        row = self._row(application_id)
        if row is None:
            row = ApplicationTailoring(user_id=self.user_id, application_id=application_id)
            self.db.add(row)
        row.source_resume_id = src.resume_id
        row.generated_document_id = doc.id
        row.enabled = True
        row.tailored_json = json.dumps(result.tailored, ensure_ascii=False)
        row.report_json = json.dumps(result.report, ensure_ascii=False, default=str)
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        self.db.refresh(doc)

        cov = result.report["coverage"]
        logger.info(
            "resume_tailored",
            user_id=self.user_id, application_id=application_id,
            coverage_before=cov["before"]["weighted_pct"], coverage_after=cov["after"]["weighted_pct"],
            gaps=len(result.report["gaps"]), warnings=len(result.report["warnings"]),
            llm_calls=result.llm_calls, plan_source=result.report["plan_source"],
        )
        try:
            from app.services.analytics.service import ProductEventService
            ProductEventService(self.db).track(
                "resume_tailored", user_id=self.user_id, entity_type="job_application",
                entity_id=application_id,
                properties={"before": cov["before"]["weighted_pct"], "after": cov["after"]["weighted_pct"],
                            "gaps": len(result.report["gaps"]), "llm_calls": result.llm_calls},
            )
        except Exception:  # analytics must never break generation
            pass
        return self.to_out(app, row), doc

    def set_enabled(self, app_id: int, enabled: bool) -> dict:
        app = self._app(app_id)
        row = self._row(app_id)
        if not row:
            raise HTTPException(status_code=404, detail="This application has not been tailored yet")
        row.enabled = enabled
        self.db.commit()
        self.db.refresh(row)
        return self.to_out(app, row)

    def delete(self, app_id: int) -> None:
        self._app(app_id)
        row = self._row(app_id)
        if row:
            self.db.delete(row)
            self.db.commit()
