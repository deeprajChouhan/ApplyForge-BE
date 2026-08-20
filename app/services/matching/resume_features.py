"""Extract user features from a parsed resume.

Falls back to raw resume text if no parsed structure is available.
Emits a `ResumeFeatures` object consumed by `scoring.score_rcms`.

Runs once per resume upload (call from your resume-parse pipeline) and
cache the resulting JSON on the parsed-resume row — the scorer reads
that per-tick without re-parsing.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.services.matching.jd_features import (
    DOMAIN_KEYWORDS,
    ROLE_FAMILIES,
    SENIORITY_PATTERNS,
    SKILL_ALIASES,
)
from app.services.matching.scoring import (
    ResumeDomain,
    ResumeExperience,
    ResumeFeatures,
    ResumeSkill,
)


def _months_since(date_str: str | None) -> int:
    """Return months since `date_str` (YYYY-MM or YYYY). None -> 0 (assume current)."""
    if not date_str:
        return 0
    try:
        s = str(date_str).strip()
        if len(s) == 4:
            dt = datetime(int(s), 12, 1)
        else:
            parts = s.split("-")
            dt = datetime(int(parts[0]), int(parts[1]), 1)
        now = datetime.utcnow()
        return max(0, (now.year - dt.year) * 12 + (now.month - dt.month))
    except (ValueError, IndexError):
        return 0


def _skills_from_text(text: str) -> dict[str, dict[str, Any]]:
    """Fallback skill detection when we don't have structured skills.

    Returns {canonical_skill: {"years": float, "recency_months": int}}.
    Since raw text lacks per-skill years/recency, we assume "current"
    (recency_months=0) and estimate years from any nearby "X yrs" mention.
    """
    text_l = text.lower()
    hits: dict[str, dict[str, Any]] = {}
    for canonical, aliases in SKILL_ALIASES.items():
        for alias in aliases:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text_l):
                hits.setdefault(canonical, {"years": 2.0, "recency_months": 0})
                break
    return hits


def _detect_seniority(text: str) -> str:
    text_l = text.lower()
    for level, patterns in SENIORITY_PATTERNS:
        if any(p in text_l for p in patterns):
            return level
    return "mid"


def _detect_domains(text: str) -> list[ResumeDomain]:
    text_l = text.lower()
    domains: list[ResumeDomain] = []
    for name, kws in DOMAIN_KEYWORDS.items():
        hits = sum(1 for k in kws if k in text_l)
        if hits:
            strength = min(1.0, 0.3 + 0.2 * hits)
            domains.append(ResumeDomain(name=name, strength=round(strength, 2)))
    return domains


def _role_family_for(title: str) -> str:
    tl = title.lower()
    for family, patterns in ROLE_FAMILIES:
        if any(p in tl for p in patterns):
            return family
    return ""


def extract_from_parsed(parsed: dict[str, Any]) -> ResumeFeatures:
    """Convert a structured parsed-resume dict into ResumeFeatures.

    Expected keys (all optional — best-effort fill):
      - `skills`: list[str] or list[{"name": str, "years": float, "last_used": "YYYY-MM"}]
      - `experiences`: list[{"title": str, "start": str, "end": str|None}]
      - `total_years`: float
      - `text`: raw resume text (fallback for domain/seniority detection)
    """
    text = parsed.get("text", "") or ""

    # Skills
    skills: list[ResumeSkill] = []
    raw_skills = parsed.get("skills") or []
    if raw_skills and isinstance(raw_skills[0], dict):
        for s in raw_skills:
            name = str(s.get("name", "")).lower().strip()
            if not name:
                continue
            skills.append(ResumeSkill(
                name=name,
                years=float(s.get("years") or 2.0),
                recency_months=_months_since(s.get("last_used")),
            ))
    elif raw_skills:
        for name in raw_skills:
            skills.append(ResumeSkill(name=str(name).lower().strip(), years=2.0, recency_months=0))
    else:
        for name, meta in _skills_from_text(text).items():
            skills.append(ResumeSkill(name=name, years=meta["years"], recency_months=meta["recency_months"]))

    # Experiences
    experiences: list[ResumeExperience] = []
    total_years_calc = 0.0
    for exp in parsed.get("experiences") or []:
        title = str(exp.get("title", "")).strip()
        start = exp.get("start")
        end = exp.get("end")
        # Duration in years, honest floor at 0.
        try:
            start_m = _months_since(start)
            end_m = _months_since(end) if end else 0
            years = max(0.0, (start_m - end_m) / 12)
        except Exception:
            years = 0.0
        experiences.append(ResumeExperience(
            title=title,
            role_family=_role_family_for(title),
            years=years,
            recency_months=end_m if end else 0,
        ))
        total_years_calc += years

    total_years = float(parsed.get("total_years") or total_years_calc or 0.0)

    return ResumeFeatures(
        skills=skills,
        domains=_detect_domains(text),
        experiences=experiences,
        total_years=total_years,
        seniority=_detect_seniority(text),
    )


def features_to_json(features: ResumeFeatures) -> dict:
    return {
        "skills": [{"name": s.name, "years": s.years, "recency_months": s.recency_months} for s in features.skills],
        "domains": [{"name": d.name, "strength": d.strength} for d in features.domains],
        "experiences": [{"title": e.title, "role_family": e.role_family, "years": e.years, "recency_months": e.recency_months} for e in features.experiences],
        "total_years": features.total_years,
        "seniority": features.seniority,
    }


def features_from_json(payload: dict | None) -> ResumeFeatures:
    if not payload:
        return ResumeFeatures()
    return ResumeFeatures(
        skills=[ResumeSkill(**s) for s in payload.get("skills", [])],
        domains=[ResumeDomain(**d) for d in payload.get("domains", [])],
        experiences=[ResumeExperience(**e) for e in payload.get("experiences", [])],
        total_years=payload.get("total_years", 0.0),
        seniority=payload.get("seniority", "mid"),
    )
