"""Role-Conditional Match Scoring (RCMS).

The scoring function is intentionally pure and deterministic — no LLM
calls, no I/O, no DB access. Callers feed in extracted features
(`resume_features` from `resume_features.py`, `jd_features` from
`jd_features.py`) and get back a transparent score plus the reasons.

Kept in one file so the whole algorithm is auditable at a glance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SENIORITY_ORDER = ["intern", "entry", "mid", "senior", "lead", "principal", "staff"]


# ── Feature shapes ──────────────────────────────────────────────────────

@dataclass
class ResumeSkill:
    name: str            # canonical lowercase, e.g. "python"
    years: float         # cumulative years used
    recency_months: int  # months since last used (0 = current)

    @property
    def strength(self) -> float:
        """Skill strength 0..1. Weighted by years and recency decay.
        Half-life ~36 months: a skill unused for 3 years counts half.
        """
        recency_factor = 0.5 ** (self.recency_months / 36) if self.recency_months > 0 else 1.0
        year_factor = min(1.0, self.years / 5.0)  # 5+ years -> full weight
        return round(year_factor * recency_factor, 3)


@dataclass
class ResumeDomain:
    name: str            # "backend", "fintech", "ml", ...
    strength: float      # 0..1, roughly fraction of career in this domain


@dataclass
class ResumeExperience:
    title: str
    role_family: str     # "backend_engineer", "ml_engineer", ...
    years: float
    recency_months: int


@dataclass
class ResumeFeatures:
    skills: List[ResumeSkill] = field(default_factory=list)
    domains: List[ResumeDomain] = field(default_factory=list)
    experiences: List[ResumeExperience] = field(default_factory=list)
    total_years: float = 0.0
    seniority: str = "mid"      # from SENIORITY_ORDER

    def skill_strength(self, name: str) -> float:
        for s in self.skills:
            if s.name == name:
                return s.strength
        return 0.0


@dataclass
class JdSkill:
    name: str
    weight: float        # 0..1, how central to the role (frequency + section)


@dataclass
class JdFeatures:
    must_have_skills: List[JdSkill] = field(default_factory=list)
    nice_to_have_skills: List[JdSkill] = field(default_factory=list)
    min_years_experience: int = 0
    seniority: str = "mid"
    domain_tags: List[str] = field(default_factory=list)
    role_family: str = ""


# ── Coverage channels ────────────────────────────────────────────────────

def _skill_coverage(resume: ResumeFeatures, jd: JdFeatures) -> Dict[str, Any]:
    """Weighted coverage of the JD's must-have skills by the resume's
    skill strengths. Nice-to-haves contribute a small bonus but don't
    dominate.
    """
    if not jd.must_have_skills:
        return {"score": 60, "matched": [], "missing": [], "total_weight": 0.0}

    total_weight = sum(s.weight for s in jd.must_have_skills)
    if total_weight == 0:
        return {"score": 60, "matched": [], "missing": [], "total_weight": 0.0}

    covered = 0.0
    matched: List[str] = []
    missing: List[str] = []
    for jd_skill in jd.must_have_skills:
        user_strength = resume.skill_strength(jd_skill.name)
        contribution = min(jd_skill.weight, jd_skill.weight * user_strength)
        covered += contribution
        (matched if user_strength > 0 else missing).append(jd_skill.name)

    # Small nice-to-have bonus, capped at +10.
    nice_bonus = 0.0
    for jd_skill in jd.nice_to_have_skills:
        if resume.skill_strength(jd_skill.name) > 0:
            nice_bonus = min(10.0, nice_bonus + 2.0)

    base = (covered / total_weight) * 100
    return {
        "score": round(min(100.0, base + nice_bonus), 1),
        "matched": matched,
        "missing": missing,
        "total_weight": total_weight,
        "nice_to_have_bonus": nice_bonus,
    }


def _experience_coverage(resume: ResumeFeatures, jd: JdFeatures) -> Dict[str, Any]:
    """Graceful decay if the user is under the required years."""
    required = jd.min_years_experience or 0
    if required == 0:
        return {"score": 80, "user_years": resume.total_years, "required": 0}

    ratio = resume.total_years / required
    if ratio >= 1.0:
        score = 100.0
    elif ratio >= 0.7:
        # 70-100% of required: score 70-100 linear
        score = 70 + (ratio - 0.7) / 0.3 * 30
    else:
        # < 70% of required: score 0-70 linear, but never below 20
        score = max(20.0, ratio * 100.0)

    return {
        "score": round(score, 1),
        "user_years": resume.total_years,
        "required": required,
        "ratio": round(ratio, 2),
    }


def _domain_coverage(resume: ResumeFeatures, jd: JdFeatures) -> Dict[str, Any]:
    if not jd.domain_tags:
        return {"score": 70, "matched": [], "missing": []}

    resume_domains = {d.name for d in resume.domains}
    matched = [t for t in jd.domain_tags if t in resume_domains]
    missing = [t for t in jd.domain_tags if t not in resume_domains]
    score = (len(matched) / len(jd.domain_tags)) * 100
    return {"score": round(score, 1), "matched": matched, "missing": missing}


# ── Weight derivation from the JD itself ─────────────────────────────────

def _weights_for(jd: JdFeatures) -> Dict[str, float]:
    """A JD listing many skills weights skills more; one demanding lots of
    years weights experience more; sparse both → domain gets more voice.
    """
    n_skills = len(jd.must_have_skills)
    skills_raw = min(0.7, 0.25 + 0.05 * n_skills)  # 0.25 → 0.7 as skills grow
    exp_raw = 0.35 if jd.min_years_experience >= 5 else (0.25 if jd.min_years_experience >= 3 else 0.15)
    domain_raw = 0.15 + 0.05 * len(jd.domain_tags)  # more tags → more weight

    total = skills_raw + exp_raw + domain_raw
    return {
        "skills": round(skills_raw / total, 3),
        "experience": round(exp_raw / total, 3),
        "domain": round(domain_raw / total, 3),
    }


# ── Adjustments (recency, seniority, prefs) ──────────────────────────────

def _seniority_gap(user: str, required: str) -> int:
    try:
        return SENIORITY_ORDER.index(user) - SENIORITY_ORDER.index(required)
    except ValueError:
        return 0


def _adjustments(resume: ResumeFeatures, jd: JdFeatures, prefs: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    # Recency bonus: currently-active skills that match must-haves add +2 each, cap +10.
    current_matches = sum(
        1
        for js in jd.must_have_skills
        if any(s.name == js.name and s.recency_months <= 6 and s.strength > 0.3 for s in resume.skills)
    )
    if current_matches:
        out.append({"kind": "recency_bonus", "detail": f"{current_matches} must-have skill(s) currently in use", "delta": min(10, current_matches * 2)})

    # Seniority alignment
    gap = _seniority_gap(resume.seniority, jd.seniority)
    if gap == 0:
        out.append({"kind": "seniority_match", "detail": "seniority matches exactly", "delta": +5})
    elif abs(gap) == 1:
        out.append({"kind": "seniority_near", "detail": "seniority within one level", "delta": 0})
    elif gap < -1:
        out.append({"kind": "seniority_junior", "detail": "user seniority is well below required", "delta": -15})
    elif gap > 1:
        out.append({"kind": "seniority_overqualified", "detail": "user seniority is well above required", "delta": -5})

    # Prefs
    if prefs.get("remote_only") and not prefs.get("job_is_remote", False):
        out.append({"kind": "remote_only_mismatch", "detail": "remote_only set but job isn't remote", "delta": -15})

    excluded = [e.lower() for e in prefs.get("excluded_companies", []) or []]
    company_l = (prefs.get("company_name") or "").lower()
    if company_l and company_l in excluded:
        out.append({"kind": "excluded_company", "detail": f"company '{company_l}' is excluded", "delta": -100})

    kw = [k.lower() for k in prefs.get("excluded_keywords", []) or []]
    haystack = (prefs.get("jd_text") or "").lower()
    penalties = 0
    for k in kw:
        if k and k in haystack and penalties < 40:
            penalties += 10
            out.append({"kind": "excluded_keyword", "detail": f"contains excluded keyword '{k}'", "delta": -10})

    return out


# ── Top strengths ranked *for this role* ─────────────────────────────────

def _top_strengths_for_role(resume: ResumeFeatures, jd: JdFeatures) -> Dict[str, Any]:
    jd_skill_names = {s.name for s in jd.must_have_skills} | {s.name for s in jd.nice_to_have_skills}

    ranked_skills = sorted(
        [s for s in resume.skills if s.name in jd_skill_names],
        key=lambda s: s.strength,
        reverse=True,
    )[:5]

    ranked_experiences = sorted(
        [e for e in resume.experiences if e.role_family == jd.role_family],
        key=lambda e: e.years,
        reverse=True,
    )[:3]

    jd_tags = set(jd.domain_tags)
    ranked_domains = sorted(
        [d for d in resume.domains if d.name in jd_tags],
        key=lambda d: d.strength,
        reverse=True,
    )[:3]

    return {
        "skills": [{"name": s.name, "strength": s.strength, "years": s.years, "recency_months": s.recency_months} for s in ranked_skills],
        "experiences": [{"title": e.title, "role_family": e.role_family, "years": e.years} for e in ranked_experiences],
        "domains": [{"name": d.name, "strength": d.strength} for d in ranked_domains],
    }


# ── Public API ───────────────────────────────────────────────────────────

def score_rcms(
    resume: ResumeFeatures,
    jd: JdFeatures,
    prefs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a full RCMS score + reasons payload for one (user, job) pair.

    `prefs` carries the auto-apply settings and per-job context needed
    for adjustments — see `_adjustments` for the accepted keys.
    """
    prefs = prefs or {}

    skills = _skill_coverage(resume, jd)
    exp = _experience_coverage(resume, jd)
    dom = _domain_coverage(resume, jd)
    weights = _weights_for(jd)

    base = (
        weights["skills"] * skills["score"]
        + weights["experience"] * exp["score"]
        + weights["domain"] * dom["score"]
    )

    adjustments = _adjustments(resume, jd, prefs)
    adj_total = sum(a["delta"] for a in adjustments)

    final = max(0.0, min(100.0, round(base + adj_total, 1)))

    if final >= 85:
        band = "top"
    elif final >= 70:
        band = "strong"
    elif final >= 55:
        band = "good"
    else:
        band = "weak"

    return {
        "score": final,
        "band": band,
        "coverage": {"skills": skills, "experience": exp, "domain": dom},
        "weights_used": weights,
        "adjustments": adjustments,
        "top_strengths_for_this_role": _top_strengths_for_role(resume, jd),
    }
