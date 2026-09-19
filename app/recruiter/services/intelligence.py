"""
Phase 7: AI intelligence — Ask-the-pool, role quality check, pipeline
health, client intelligence, feedback pattern rollups.

Every function here returns explanations grounded in real agency data plus
optional LLM polish. Nothing writes back to the domain — it caches into
AiInsightCache only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from collections import Counter

from sqlalchemy.orm import Session

from app.recruiter.enums import ApplicationStage
from app.recruiter.models import (
    AiInsightCache,
    Application,
    CandidateProfile,
    ClientSubmission,
    Interview,
    Offer,
    Placement,
    Role,
    SubmissionFeedback,
)
from app.recruiter.services import ai_support
from app.recruiter.services.collaboration import sla_hours_for_client

from app.recruiter.services.skills import normalize_skill


# ── Ask the pool ──────────────────────────────────────────────────────
_ASK_SYSTEM = (
    "You translate a recruiter's natural-language search into structured "
    "filters against a candidate pool. Only extract filters the query "
    "explicitly names. Return STRICT JSON."
)

_ASK_SCHEMA = (
    'Return JSON: {"skills_any": [string, ...], "skills_all": [string, ...], '
    '"location_contains": string | null, "min_years_experience": number | null, '
    '"max_expected_compensation": {"amount": int, "currency": string} | null, '
    '"max_notice_days": int | null, "keywords": [string, ...]}'
)


def _parse_query_with_llm(query: str) -> dict[str, Any]:
    if not ai_support.llm_enabled():
        return {}
    data = ai_support.generate_json(_ASK_SYSTEM, _ASK_SCHEMA + f"\n\nQuery: {query}")
    return data if isinstance(data, dict) else {}


def ask_the_pool(db: Session, agency_id: int, query: str, limit: int = 10) -> dict[str, Any]:
    filters = _parse_query_with_llm(query)
    q = db.query(CandidateProfile).filter(CandidateProfile.agency_id == agency_id)

    loc = (filters.get("location_contains") or "").strip()
    if loc:
        q = q.filter(CandidateProfile.location.ilike(f"%{loc}%"))
    if isinstance(filters.get("min_years_experience"), (int, float)):
        q = q.filter(CandidateProfile.years_experience >= filters["min_years_experience"])

    cands = q.limit(200).all()

    # Skill scoring
    skills_any = {normalize_skill(s) for s in (filters.get("skills_any") or []) if s}
    skills_all = {normalize_skill(s) for s in (filters.get("skills_all") or []) if s}
    kws = [k.lower() for k in (filters.get("keywords") or []) if isinstance(k, str)]

    scored: list[tuple[float, CandidateProfile, str]] = []
    for c in cands:
        cand_skills = {normalize_skill(s.name) for s in (c.skills or []) if s.name}
        if skills_all and not skills_all.issubset(cand_skills):
            continue
        score = 0.0
        matched: list[str] = []
        if skills_any:
            hit = skills_any & cand_skills
            score += 40.0 * (len(hit) / max(1, len(skills_any)))
            matched += list(hit)
        blob = " ".join(filter(None, [c.summary, c.headline])).lower()
        for k in kws:
            if k and k in blob:
                score += 10.0
                matched.append(k)
        if c.years_experience:
            score += min(30.0, c.years_experience * 3.0)
        reason = (
            "Matches " + ", ".join(matched[:4]) if matched
            else "General profile match"
        )
        scored.append((score, c, reason))

    scored.sort(key=lambda t: t[0], reverse=True)
    matches = []
    for _score, c, reason in scored[:limit]:
        matches.append({
            "candidate_id": c.id,
            "display_name": c.full_name,
            "fit_reason": reason,
            "top_skills": [s.name for s in (c.skills or [])[:6]],
            "expected_compensation": None,
            "notice_period": None,
        })

    return {
        "query": query,
        "matches": matches,
        "filters_applied": filters,
        "used_llm": bool(filters),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Role quality check ────────────────────────────────────────────────
def role_quality_check(db: Session, agency_id: int, role: Role) -> dict[str, Any]:
    obs: list[str] = []
    risk = "low"
    if not role.required_skills:
        obs.append("No required skills listed — matching will be weak.")
        risk = "medium"
    if role.required_skills and len(role.required_skills) > 12:
        obs.append("Very broad tech stack (>12 required skills) — may narrow the candidate pool.")
        risk = "medium"
    if role.min_years_experience and role.min_years_experience > 10:
        obs.append(
            f"Minimum {role.min_years_experience} years experience may significantly restrict the candidate pool."
        )
        risk = "high"
    if role.salary_min is None and role.salary_max is None:
        obs.append("No published salary range — candidates may drop off during outreach.")
    if role.budget_min and role.salary_max and role.budget_min > role.salary_max:
        obs.append("Budget minimum exceeds the published salary maximum — inconsistency.")
        risk = "medium"
    if not role.country_code:
        obs.append("No country_code set — market-specific compensation UI will use defaults.")
    return {
        "role_id": role.id,
        "observations": obs,
        "candidate_pool_risk": risk,
        "used_llm": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Role pipeline health ──────────────────────────────────────────────
def role_health(db: Session, agency_id: int, role_id: int) -> dict[str, Any]:
    apps = db.query(Application).filter(
        Application.agency_id == agency_id, Application.role_id == role_id,
    ).all()
    stage_counts: Counter[str] = Counter()
    for a in apps:
        stage_counts[a.stage.value] += 1

    subs = db.query(ClientSubmission).filter(
        ClientSubmission.agency_id == agency_id, ClientSubmission.role_id == role_id,
    ).count()
    ivs = db.query(Interview).filter(
        Interview.agency_id == agency_id, Interview.role_id == role_id,
    ).count()
    offers = db.query(Offer).filter(
        Offer.agency_id == agency_id, Offer.role_id == role_id,
    ).count()
    placements = db.query(Placement).filter(
        Placement.agency_id == agency_id, Placement.role_id == role_id,
    ).count()

    # Rejection reason rollup
    rej = (
        db.query(SubmissionFeedback)
        .join(ClientSubmission, ClientSubmission.id == SubmissionFeedback.submission_id)
        .filter(
            SubmissionFeedback.agency_id == agency_id,
            ClientSubmission.role_id == role_id,
            SubmissionFeedback.decision == "reject",
        )
        .all()
    )
    reasons: Counter[str] = Counter()
    for f in rej:
        for r in (f.reasons or []):
            reasons[r] += 1

    sourced = stage_counts.get(ApplicationStage.sourced.value, 0)
    submitted = stage_counts.get(ApplicationStage.submitted.value, 0) + subs
    screened = sum(1 for a in apps if a.screening_completed_at is not None)

    # Simple risk heuristic
    risk = "low"
    if submitted == 0 and sourced > 20:
        risk = "high"
    elif submitted <= 2 and sourced >= 10:
        risk = "medium"

    suggestion = None
    if reasons:
        top_reason, _n = reasons.most_common(1)[0]
        suggestion = f"Rejections cluster on '{top_reason}'. Consider revisiting role criteria before more sourcing."

    return {
        "role_id": role_id,
        "sourced": sourced,
        "screened": screened,
        "submitted": submitted,
        "interviewed": ivs,
        "offered": offers,
        "placed": placements,
        "rejected": len(rej),
        "pipeline_risk": risk,
        "common_rejection_reasons": [k for k, _ in reasons.most_common(5)],
        "suggestion": suggestion,
        "used_llm": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Client intelligence ───────────────────────────────────────────────
def client_intelligence(db: Session, agency_id: int, client_id: int) -> dict[str, Any]:
    subs = db.query(ClientSubmission).filter(
        ClientSubmission.agency_id == agency_id, ClientSubmission.client_id == client_id,
    ).all()
    ivs = db.query(Interview).filter(
        Interview.agency_id == agency_id, Interview.client_id == client_id,
    ).count()
    offers = db.query(Offer).filter(
        Offer.agency_id == agency_id, Offer.client_id == client_id,
    ).count()
    placements = db.query(Placement).filter(
        Placement.agency_id == agency_id, Placement.client_id == client_id,
    ).count()
    open_roles = db.query(Role).filter(
        Role.agency_id == agency_id, Role.client_id == client_id, Role.status == "open",
    ).count()

    total_delta = 0.0
    responded = 0
    for s in subs:
        if s.client_responded_at and s.submitted_at:
            total_delta += (s.client_responded_at - s.submitted_at).total_seconds() / 3600.0
            responded += 1
    avg = (total_delta / responded) if responded else None

    rej_reasons: Counter[str] = Counter()
    fbs = (
        db.query(SubmissionFeedback)
        .filter(SubmissionFeedback.agency_id == agency_id)
        .join(ClientSubmission, ClientSubmission.id == SubmissionFeedback.submission_id)
        .filter(ClientSubmission.client_id == client_id, SubmissionFeedback.decision == "reject")
        .all()
    )
    for f in fbs:
        for r in (f.reasons or []):
            rej_reasons[r] += 1

    interview_rate = (ivs / len(subs)) if subs else None
    offer_rate = (offers / max(1, ivs)) if ivs else None

    summary = None
    if rej_reasons:
        top, _ = rej_reasons.most_common(1)[0]
        summary = f"This client's rejections cluster on '{top}'."

    return {
        "client_id": client_id,
        "open_roles": open_roles,
        "submissions": len(subs),
        "interviews": ivs,
        "offers": offers,
        "placements": placements,
        "interview_rate": round(interview_rate, 2) if interview_rate is not None else None,
        "offer_rate": round(offer_rate, 2) if offer_rate is not None else None,
        "average_feedback_hours": round(avg, 1) if avg is not None else None,
        "common_rejection_reasons": [k for k, _ in rej_reasons.most_common(5)],
        "summary": summary,
        "used_llm": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
