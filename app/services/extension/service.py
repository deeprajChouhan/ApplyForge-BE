"""ExtensionService — state + scoring helpers for the ApplyForge Job Clipper."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import (
    Education,
    JobApplication,
    ParsedResumeData,
    Skill,
    UploadedFile,
    UserExtensionState,
    WorkExperience,
)
from app.schemas.extension import ExtensionSettings, ExtensionSettingsUpdate
from app.services.scoring.service import FitScorer


def frontend_base_url() -> str:
    """Best-effort public web-app origin for building open_url links."""
    for origin in settings.cors_origin_list:
        if origin.startswith("https://"):
            return origin.rstrip("/")
    return "https://applyforge.co.uk"


def score_confidence(description: str, source_site: str = "") -> str:
    """
    Confidence in a preview score based on how much job detail we have.

    Listing cards (short/absent description) score "low"; a partially-loaded
    detail is "medium"; a full job description is "high".
    """
    length = len((description or "").strip())
    if length >= 400:
        return "high"
    if length >= 120:
        return "medium"
    return "low"


class ExtensionService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def get_state(self) -> UserExtensionState | None:
        return (
            self.db.query(UserExtensionState)
            .filter_by(user_id=self.user_id)
            .first()
        )

    def _get_or_create(self) -> UserExtensionState:
        state = self.get_state()
        if not state:
            state = UserExtensionState(user_id=self.user_id)
            self.db.add(state)
            self.db.flush()
        return state

    def mark_seen(self, *, connect: bool = False) -> UserExtensionState:
        """Record activity from the extension; optionally mark it connected."""
        state = self._get_or_create()
        now = datetime.utcnow()
        state.last_seen_at = now
        if connect and state.connected_at is None:
            state.connected_at = now
        self.db.commit()
        self.db.refresh(state)
        return state

    def dismiss_promo(self) -> UserExtensionState:
        state = self._get_or_create()
        state.promo_dismissed_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(state)
        return state

    # ── Job Hunting settings ─────────────────────────────────────────────

    def get_settings(self) -> ExtensionSettings:
        state = self.get_state()
        raw = (state.settings_json if state else None) or {}
        try:
            cfg = ExtensionSettings(**raw)
        except Exception:
            cfg = ExtensionSettings()
        # A pinned resume that was archived/deleted silently falls back to latest.
        if cfg.default_resume_id is not None and not self._resume(cfg.default_resume_id):
            cfg.default_resume_id = None
        return cfg

    def update_settings(self, patch: ExtensionSettingsUpdate) -> ExtensionSettings:
        current = self.get_settings().model_dump()
        data = patch.model_dump(exclude_unset=True)
        clear = data.pop("clear_default_resume", False)
        for k, v in data.items():
            if v is not None:
                current[k] = v
        if clear:
            current["default_resume_id"] = None
        if current.get("default_resume_id") is not None and not self._resume(current["default_resume_id"]):
            raise HTTPException(status_code=404, detail="Resume not found")
        cfg = ExtensionSettings(**current)
        state = self._get_or_create()
        state.settings_json = cfg.model_dump()
        self.db.commit()
        return cfg

    # ── Resume helpers (always filtered by user_id) ──────────────────────

    def _resume(self, resume_id: int) -> ParsedResumeData | None:
        return (
            self.db.query(ParsedResumeData)
            .filter_by(id=resume_id, user_id=self.user_id)
            .filter(ParsedResumeData.deleted_at.is_(None))
            .first()
        )

    def _latest_resume(self) -> ParsedResumeData | None:
        return (
            self.db.query(ParsedResumeData)
            .filter_by(user_id=self.user_id)
            .filter(ParsedResumeData.deleted_at.is_(None))
            .order_by(ParsedResumeData.created_at.desc(), ParsedResumeData.id.desc())
            .first()
        )

    def resolve_resume(self, cfg: ExtensionSettings) -> ParsedResumeData | None:
        if cfg.default_resume_id is not None:
            return self._resume(cfg.default_resume_id) or self._latest_resume()
        return self._latest_resume()

    def resume_by_setting(self, cfg: ExtensionSettings) -> ParsedResumeData | None:
        """The explicitly chosen resume, or None (application keeps 'latest' default)."""
        return self._resume(cfg.default_resume_id) if cfg.default_resume_id is not None else None

    def resume_label(self, resume: ParsedResumeData | None) -> str | None:
        if not resume:
            return None
        filename = None
        if resume.uploaded_file_id:
            f = (
                self.db.query(UploadedFile)
                .filter_by(id=resume.uploaded_file_id, user_id=self.user_id)
                .first()
            )
            filename = f.filename if f else None
        when = resume.created_at.strftime("%d %b %Y") if resume.created_at else ""
        return f"{filename or 'Resume #' + str(resume.id)}{' · ' + when if when else ''}"

    @staticmethod
    def _resume_skills(resume: ParsedResumeData | None) -> list[str]:
        if not resume:
            return []
        try:
            rd: dict[str, Any] = json.loads(resume.structured_json) or {}
        except (json.JSONDecodeError, TypeError):
            return []
        out: list[str] = []
        for sk in rd.get("skills", []) or []:
            name = sk.get("name") if isinstance(sk, dict) else sk
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
        return out

    def quick_fit(
        self,
        cfg: ExtensionSettings,
        jd_text: str,
        role_title: str,
        jd_skills: list[str],
    ) -> dict[str, Any]:
        """
        Fast, LLM-free fit for the extension preview.

        Uses the same 8-signal FitScorer as the full match, but feeds it the
        regex-extracted JD skills (no LLM analysis), so the strengths/gaps and
        keyword signals stay neutral. Skills come from the chosen resume; when
        it is the default (latest) resume, profile Skill rows are merged in,
        matching the full-match behaviour. matched/missing are word-boundary
        matches so a chip never claims a skill the resume does not contain.
        """
        resume = self.resolve_resume(cfg)
        skills = self._resume_skills(resume)
        if cfg.default_resume_id is None:
            seen = {s.lower() for s in skills}
            for row in self.db.query(Skill).filter_by(user_id=self.user_id).all():
                if row.name and row.name.lower() not in seen:
                    seen.add(row.name.lower())
                    skills.append(row.name)

        result: dict[str, Any] = {
            "resume": resume,
            "fit_source": "neutral",
            "fit_score": 50.0,
            "fit_breakdown": {},
            "matched_skills": [],
            "missing_skills": [],
        }
        if not skills:
            return result

        matched = [s for s in jd_skills if any(contains_term(s, p) or contains_term(p, s) for p in skills)]
        result["matched_skills"] = matched
        result["missing_skills"] = [s for s in jd_skills if s not in matched]

        if not cfg.quick_fit:
            return result

        exps = (
            self.db.query(WorkExperience)
            .filter_by(user_id=self.user_id)
            .order_by(WorkExperience.start_date.desc())
            .all()
        )
        edus = self.db.query(Education).filter_by(user_id=self.user_id).all()
        fit, breakdown = FitScorer.score(
            {"required_skills": jd_skills},
            skills,
            jd_text=jd_text,
            work_experiences=exps,
            educations=edus,
            role_title=role_title,
        )
        result.update(fit_source="resume_quick", fit_score=fit, fit_breakdown=breakdown)
        return result

    def find_saved(self, source_url: str) -> JobApplication | None:
        url = (source_url or "").strip()
        if not url:
            return None
        variants = {url, url.rstrip("/"), url.rstrip("/") + "/"}
        return (
            self.db.query(JobApplication)
            .filter(JobApplication.user_id == self.user_id)
            .filter(JobApplication.deleted_at.is_(None))
            .filter(JobApplication.jd_link.in_(variants))
            .order_by(JobApplication.created_at.desc())
            .first()
        )


# ── Matching helpers ────────────────────────────────────────────────────────

def contains_term(term: str, text: str) -> bool:
    """Case-insensitive whole-word/phrase match (handles c++, c#, node.js)."""
    term = (term or "").strip().lower()
    if not term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    return re.search(pattern, (text or "").lower()) is not None


def must_haves(keywords: list[str], jd_text: str) -> tuple[list[str], list[str]]:
    found = [k for k in keywords if contains_term(k, jd_text)]
    return found, [k for k in keywords if k not in found]


def is_excluded_company(company: str, excluded: list[str]) -> bool:
    name = (company or "").strip().lower()
    if not name:
        return False
    return any(contains_term(x, name) for x in excluded)
