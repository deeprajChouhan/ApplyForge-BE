"""
Inverted matching — "who fits a role best" (Section 3.1).

Fix a role, rank the agency's candidate pool by a fit score in [0, 100] composed
of four independent signals, with human-readable reasons and gaps. Operates
purely over agency-owned data.

Signal budget (max 100): semantic 40 · required skills 35 · preferred 10 · experience 15.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.recruiter.models import CandidateProfile, Role
from app.recruiter.services.embeddings import embed

W_SEMANTIC = 40.0
W_REQUIRED = 35.0
W_PREFERRED = 10.0
W_EXPERIENCE = 15.0


@dataclass
class MatchResult:
    candidate_id: int
    fit_score: float
    reasons: list[str]
    gaps: list[str]
    breakdown: dict[str, float]


def role_text(role: Role) -> str:
    parts = [
        role.title or "",
        role.seniority or "",
        role.description or "",
        " ".join(role.required_skills or []),
        " ".join(role.preferred_skills or []),
    ]
    return " ".join(p for p in parts if p).strip()


def candidate_text(cand: CandidateProfile) -> str:
    skills = " ".join(s.name for s in cand.skills)
    parts = [cand.headline or "", cand.summary or "", skills, cand.raw_cv_text or ""]
    return " ".join(p for p in parts if p).strip()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def embed_role(role: Role) -> list[float]:
    return embed(role_text(role))


def embed_candidate(cand: CandidateProfile) -> list[float]:
    return embed(candidate_text(cand))


def score_candidate(role: Role, cand: CandidateProfile, role_vec: list[float]) -> MatchResult:
    reasons: list[str] = []
    gaps: list[str] = []

    cand_vec = cand.embedding or embed_candidate(cand)
    sim = max(0.0, min(1.0, (_cosine(role_vec, cand_vec) + 1.0) / 2.0))
    semantic_pts = round(sim * W_SEMANTIC, 2)
    if sim >= 0.6:
        reasons.append("Strong overall profile match to the role")
    elif sim <= 0.35:
        gaps.append("Overall profile is a weak semantic match")

    cand_skills = {s.name for s in cand.skills}
    required = list(dict.fromkeys(role.required_skills or []))
    preferred = list(dict.fromkeys(role.preferred_skills or []))

    req_hits = [s for s in required if s in cand_skills]
    req_missing = [s for s in required if s not in cand_skills]
    pref_hits = [s for s in preferred if s in cand_skills]

    req_cov = (len(req_hits) / len(required)) if required else 1.0
    pref_cov = (len(pref_hits) / len(preferred)) if preferred else 0.0
    required_pts = round(req_cov * W_REQUIRED, 2)
    preferred_pts = round(pref_cov * W_PREFERRED, 2)

    if req_hits:
        reasons.append(f"Has {len(req_hits)}/{len(required)} required skills: {', '.join(req_hits)}")
    if req_missing:
        gaps.append(f"Missing required skills: {', '.join(req_missing)}")
    if pref_hits:
        reasons.append(f"Also brings preferred skills: {', '.join(pref_hits)}")

    if role.min_years_experience:
        yoe = cand.years_experience or 0.0
        ratio = min(1.0, yoe / role.min_years_experience)
        experience_pts = round(ratio * W_EXPERIENCE, 2)
        if yoe >= role.min_years_experience:
            reasons.append(f"Meets experience bar ({yoe:g}y ≥ {role.min_years_experience:g}y)")
        else:
            gaps.append(f"Below experience bar ({yoe:g}y < {role.min_years_experience:g}y)")
    else:
        experience_pts = W_EXPERIENCE

    fit = round(semantic_pts + required_pts + preferred_pts + experience_pts, 2)
    breakdown = {
        "semantic": semantic_pts,
        "required_skills": required_pts,
        "preferred_skills": preferred_pts,
        "experience": experience_pts,
    }
    return MatchResult(cand.id, fit, reasons, gaps, breakdown)


def rank_candidates(role: Role, candidates: list[CandidateProfile]) -> list[MatchResult]:
    role_vec = role.embedding or embed_role(role)
    results = [score_candidate(role, cand, role_vec) for cand in candidates]
    results.sort(key=lambda r: r.fit_score, reverse=True)
    return results
