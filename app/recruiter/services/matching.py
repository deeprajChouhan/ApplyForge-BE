"""
Inverted matching — "who fits a role best" (Section 3.1).

Fix a role, rank the agency's candidate pool by a fit score in [0, 100] composed
of five independent signals, with human-readable reasons and gaps. Operates
purely over agency-owned data.

Signal budget (max 100):
    semantic 30 · required skills 35 · preferred 10 · experience 15 · title 10

v2 (user-testing fix — scores were reading too low):
  • Skills are normalised + alias-resolved on BOTH sides ("Python" == "py" ==
    "python"); v1 compared raw strings, so case/alias differences scored 0.
  • Evidence beyond the skills list: a required skill named in the CV text,
    headline, summary or experience descriptions earns 0.8 credit; an
    adjacent skill (postgres for SQL, TypeScript for JavaScript…) earns 0.5.
  • Semantic similarity is calibrated to the embedding model's real cosine
    range instead of (cos+1)/2, which squashed every candidate into 55–75%.
  • Neutral fallbacks (consumer Priority Score pattern): no preferred skills
    on the role → full preferred points; unknown candidate YoE → half credit
    instead of zero; experience credit is a soft curve, not a hard ratio.
  • New title signal: role title vs candidate headline + past job titles.
Every partial credit is explained in reasons/gaps so the score stays auditable.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.recruiter.models import CandidateProfile, Role
from app.recruiter.services.embeddings import _use_real_provider, embed
from app.recruiter.services.skills import extract_skills, normalize_skill, related_skills

W_SEMANTIC = 30.0
W_REQUIRED = 35.0
W_PREFERRED = 10.0
W_EXPERIENCE = 15.0
W_TITLE = 10.0

CREDIT_EXPLICIT = 1.0   # listed in the candidate's skills
CREDIT_EVIDENCE = 0.8   # mentioned in CV / experience text
CREDIT_RELATED = 0.5    # adjacent skill present

# Cosine ranges that map to 0 → 1. text-embedding-3-small puts unrelated texts
# around 0.15–0.25 and strong matches around 0.6–0.7; the offline hashed
# bag-of-words fallback runs lower.
_SEM_RANGE_REAL = (0.20, 0.65)
_SEM_RANGE_MOCK = (0.05, 0.55)

_TITLE_STOP = {
    "senior", "junior", "lead", "principal", "staff", "head", "of", "the", "and",
    "a", "an", "i", "ii", "iii", "sr", "jr", "mid", "level", "&", "-", "/", "to",
    "for", "in", "at", "with", "remote", "hybrid",
}
_WORD_RE = re.compile(r"[a-z0-9+#.]+")


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
    exp = " ".join(
        f"{e.title or ''} {e.description or ''}" for e in (getattr(cand, "experiences", None) or [])
    )
    parts = [cand.headline or "", cand.summary or "", skills, exp, cand.raw_cv_text or ""]
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


def calibrate_similarity(cos: float) -> float:
    lo, hi = _SEM_RANGE_REAL if _use_real_provider() else _SEM_RANGE_MOCK
    return max(0.0, min(1.0, (cos - lo) / (hi - lo)))


def embed_role(role: Role) -> list[float]:
    return embed(role_text(role))


def embed_candidate(cand: CandidateProfile) -> list[float]:
    return embed(candidate_text(cand))


def _evidence_text(cand: CandidateProfile) -> str:
    exp = " ".join(
        f"{e.title or ''} {e.description or ''}" for e in (getattr(cand, "experiences", None) or [])
    )
    return " ".join(
        p for p in (cand.headline, cand.summary, exp, cand.raw_cv_text) if p
    )


def _skill_credit(
    skill: str, explicit: set[str], evidenced: set[str]
) -> tuple[float, str]:
    """Return (credit, how) for one role skill against the candidate."""
    if skill in explicit:
        return CREDIT_EXPLICIT, "explicit"
    if skill in evidenced:
        return CREDIT_EVIDENCE, "evidence"
    have = explicit | evidenced
    for rel in related_skills(skill):
        if rel in have:
            return CREDIT_RELATED, f"related:{rel}"
    return 0.0, "missing"


def _title_tokens(text: str | None) -> set[str]:
    return {
        normalize_skill(t) for t in _WORD_RE.findall((text or "").lower())
        if t not in _TITLE_STOP and len(t) > 1
    }


def _title_alignment(role: Role, cand: CandidateProfile) -> float:
    want = _title_tokens(role.title)
    if not want:
        return 1.0
    titles = [cand.headline or ""] + [
        e.title or "" for e in (getattr(cand, "experiences", None) or [])
    ]
    best = 0.0
    for t in titles:
        have = _title_tokens(t)
        if have:
            best = max(best, len(want & have) / len(want))
    return best


def score_candidate(role: Role, cand: CandidateProfile, role_vec: list[float]) -> MatchResult:
    reasons: list[str] = []
    gaps: list[str] = []

    # ── Semantic ──
    cand_vec = cand.embedding or embed_candidate(cand)
    if cand.embedding and role_vec and len(cand.embedding) != len(role_vec):
        cand_vec = embed_candidate(cand)  # stale vector from another provider
    sim = calibrate_similarity(_cosine(role_vec, cand_vec))
    semantic_pts = round(sim * W_SEMANTIC, 2)
    if sim >= 0.6:
        reasons.append("Strong overall profile match to the role")
    elif sim <= 0.2:
        gaps.append("Overall profile is a weak semantic match")

    # ── Skills ──
    explicit = {normalize_skill(s.name) for s in cand.skills if s.name}
    evidenced = set(extract_skills(_evidence_text(cand))) - explicit
    required = list(dict.fromkeys(normalize_skill(s) for s in (role.required_skills or []) if s))
    preferred = [
        s for s in dict.fromkeys(normalize_skill(s) for s in (role.preferred_skills or []) if s)
        if s not in required
    ]

    req_hits: list[str] = []
    req_partial: list[str] = []
    req_missing: list[str] = []
    req_total = 0.0
    for s in required:
        credit, how = _skill_credit(s, explicit, evidenced)
        req_total += credit
        if how == "explicit":
            req_hits.append(s)
        elif how == "evidence":
            req_partial.append(f"{s} (in CV)")
        elif how.startswith("related:"):
            req_partial.append(f"{s} (via {how.split(':', 1)[1]})")
        else:
            req_missing.append(s)

    pref_total = 0.0
    pref_hits: list[str] = []
    for s in preferred:
        credit, how = _skill_credit(s, explicit, evidenced)
        pref_total += credit
        if credit > 0:
            pref_hits.append(s)

    req_cov = (req_total / len(required)) if required else 1.0
    pref_cov = (pref_total / len(preferred)) if preferred else 1.0
    required_pts = round(req_cov * W_REQUIRED, 2)
    preferred_pts = round(pref_cov * W_PREFERRED, 2)

    if req_hits:
        reasons.append(f"Has {len(req_hits)}/{len(required)} required skills: {', '.join(req_hits)}")
    if req_partial:
        reasons.append(f"Partial evidence for: {', '.join(req_partial)}")
    if req_missing:
        gaps.append(f"Missing required skills: {', '.join(req_missing)}")
    if pref_hits:
        reasons.append(f"Also brings preferred skills: {', '.join(pref_hits)}")

    # ── Experience ──
    if role.min_years_experience:
        yoe = cand.years_experience
        if yoe is None:
            ratio = 0.5
            gaps.append("Years of experience not stated on the profile")
        else:
            ratio = min(1.0, yoe / role.min_years_experience) ** 0.75
            if yoe >= role.min_years_experience:
                reasons.append(f"Meets experience bar ({yoe:g}y ≥ {role.min_years_experience:g}y)")
            else:
                gaps.append(f"Below experience bar ({yoe:g}y < {role.min_years_experience:g}y)")
        experience_pts = round(ratio * W_EXPERIENCE, 2)
    else:
        experience_pts = W_EXPERIENCE

    # ── Title ──
    title_ratio = _title_alignment(role, cand)
    title_pts = round(title_ratio * W_TITLE, 2)
    if title_ratio >= 0.6 and role.title:
        reasons.append("Current or past title aligns with the role")

    fit = round(
        min(100.0, semantic_pts + required_pts + preferred_pts + experience_pts + title_pts), 2
    )
    breakdown = {
        "semantic": semantic_pts,
        "required_skills": required_pts,
        "preferred_skills": preferred_pts,
        "experience": experience_pts,
        "title": title_pts,
    }
    return MatchResult(cand.id, fit, reasons, gaps, breakdown)


def rank_candidates(role: Role, candidates: list[CandidateProfile]) -> list[MatchResult]:
    role_vec = role.embedding or embed_role(role)
    results = [score_candidate(role, cand, role_vec) for cand in candidates]
    results.sort(key=lambda r: r.fit_score, reverse=True)
    return results
