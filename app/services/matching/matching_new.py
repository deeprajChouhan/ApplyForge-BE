"""Drop-in replacement for the old `score_job_for_user` heuristic.

Same signature. Internally runs the Role-Conditional Match Scoring
(RCMS) algorithm. Returns a shape compatible with the existing
`match_reasons_json` column, plus a rich `breakdown` field the UI can
render to explain the score.
"""
from __future__ import annotations

import json
import time
from threading import Lock
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auto_apply import AutoApplySettings
from app.models.models import ParsedResumeData
from app.services.matching.jd_features import (
    extract_jd_features,
    features_from_json as jd_from_json,
)
from app.services.matching.resume_features import (
    extract_from_parsed,
    features_from_json as resume_from_json,
)
from app.services.matching.scoring import ResumeFeatures, score_rcms


# ── Resume-features cache ───────────────────────────────────────────────
#
# tick_user calls score_job_for_user hundreds of times back-to-back for
# one user; we do NOT want to hit the DB for the parsed resume each
# time. Cache per-user with a short TTL (5 min) so an updated resume
# still propagates promptly.

_CACHE_TTL_SECONDS = 300
_resume_cache: dict[int, tuple[float, ResumeFeatures]] = {}
_cache_lock = Lock()


def _load_resume_features_for_user_id(user_id: int) -> ResumeFeatures:
    now = time.time()
    with _cache_lock:
        cached = _resume_cache.get(user_id)
        if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

    features = _fetch_resume_features(user_id)

    with _cache_lock:
        _resume_cache[user_id] = (now, features)
    return features


def _fetch_resume_features(user_id: int) -> ResumeFeatures:
    """Load AutoApplySettings.default_resume_parse_id → ParsedResumeData →
    ResumeFeatures. Falls back through several layers if data is missing."""
    with SessionLocal() as db:
        settings = (
            db.execute(select(AutoApplySettings).where(AutoApplySettings.user_id == user_id))
            .scalars()
            .first()
        )
        parse_id = getattr(settings, "default_resume_parse_id", None) if settings else None

        parsed: Optional[ParsedResumeData] = None
        if parse_id:
            parsed = db.get(ParsedResumeData, parse_id)
        if parsed is None:
            # Fall back to the user's most recent parsed resume.
            parsed = (
                db.execute(
                    select(ParsedResumeData)
                    .where(ParsedResumeData.user_id == user_id, ParsedResumeData.deleted_at.is_(None))
                    .order_by(ParsedResumeData.id.desc())
                )
                .scalars()
                .first()
            )
        if parsed is None:
            return ResumeFeatures()

        structured: dict[str, Any] = {}
        raw_structured = getattr(parsed, "structured_json", None)
        if raw_structured:
            try:
                structured = json.loads(raw_structured) if isinstance(raw_structured, str) else raw_structured
            except (json.JSONDecodeError, TypeError):
                structured = {}

        # Some parsers cache extracted features directly; prefer that if present.
        if isinstance(structured, dict) and "rcms_features" in structured:
            return resume_from_json(structured["rcms_features"])

        # Otherwise adapt whatever structured shape we have to RCMS's
        # ResumeFeatures via extract_from_parsed's best-effort mapping.
        adapter = {
            "skills": structured.get("skills") if isinstance(structured, dict) else None,
            "experiences": (
                structured.get("experiences") or structured.get("work_experience")
                if isinstance(structured, dict) else None
            ),
            "total_years": structured.get("total_years") if isinstance(structured, dict) else None,
            "text": getattr(parsed, "raw_text", "") or "",
        }
        return extract_from_parsed(adapter)


def invalidate_resume_cache(user_id: int) -> None:
    """Call after a user uploads a new resume so scoring reflects it fast."""
    with _cache_lock:
        _resume_cache.pop(user_id, None)


# ── JD features ─────────────────────────────────────────────────────────

def _jd_features_for_job(job: Any):
    """Read cached jd_features_json off the Job; extract on-the-fly if the
    cache is missing (older rows ingested before the caching landed)."""
    cached = getattr(job, "jd_features_json", None)
    if cached:
        return jd_from_json(cached)
    return extract_jd_features(
        title=getattr(job, "title", "") or "",
        description=getattr(job, "description", "") or "",
    )


# ── Public API ──────────────────────────────────────────────────────────

def score_job_for_user(user: Any, job: Any, settings: Any) -> Dict[str, Any]:
    """Same signature as the legacy matcher. Returns:

        {
          "score": int,        # 0..100
          "band": str,         # top|strong|good|weak
          "reasons": [{"kind","detail","delta"}, ...],
          "breakdown": {...},  # full RCMS payload for the UI
        }
    """
    user_id = getattr(user, "id", None)
    resume = _load_resume_features_for_user_id(user_id) if user_id else ResumeFeatures()
    jd = _jd_features_for_job(job)

    company = getattr(job, "company", None)
    prefs = {
        "remote_only": bool(getattr(settings, "remote_only", False)),
        "job_is_remote": (getattr(job, "remote_mode", "") or "").lower() == "remote",
        "company_name": getattr(company, "name", None) if company is not None else None,
        "excluded_companies": getattr(settings, "excluded_companies_json", None) or [],
        "excluded_keywords": getattr(settings, "excluded_keywords_json", None) or [],
        "jd_text": (getattr(job, "description", "") or "")[:8000],
    }

    result = score_rcms(resume, jd, prefs)

    reasons = list(result.get("adjustments", []))
    cov = result.get("coverage", {})
    if cov.get("skills", {}).get("matched"):
        reasons.append({
            "kind": "skills_matched",
            "detail": "matched skills: " + ", ".join(cov["skills"]["matched"][:8]),
            "delta": 0,
        })
    if cov.get("skills", {}).get("missing"):
        reasons.append({
            "kind": "skills_missing",
            "detail": "missing skills: " + ", ".join(cov["skills"]["missing"][:8]),
            "delta": 0,
        })

    return {
        "score": int(round(result["score"])),
        "band": result["band"],
        "reasons": reasons,
        "breakdown": result,
    }
