"""
Job-listing generation (Phase 3, Section 3.3).

Drafts a job listing that is *grounded* rather than generic: it's built from the
role's own requirements plus the skill patterns actually present in the agency's
candidate pool, so the language reflects the real talent available. Works fully
offline via a deterministic template; when a real LLM is configured it polishes
the prose while keeping the grounded structure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.recruiter.models import CandidateProfile, CandidateSkill, Role


@dataclass
class JobListing:
    role_id: int
    title: str
    seniority: str | None
    location: str | None
    employment_type: str | None
    salary_range: str | None
    summary: str
    responsibilities: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    nice_to_have: list[str] = field(default_factory=list)
    top_pool_skills: list[str] = field(default_factory=list)
    candidate_sample: int = 0
    content_markdown: str = ""
    polished_by_llm: bool = False


def _top_pool_skills(db: Session, role: Role, limit: int = 8) -> tuple[list[str], int]:
    """Most common skills across the agency's candidate pool, + pool size."""
    rows = (
        db.query(CandidateSkill.name, func.count(CandidateSkill.id).label("n"))
        .join(CandidateProfile, CandidateProfile.id == CandidateSkill.candidate_id)
        .filter(CandidateProfile.agency_id == role.agency_id)
        .group_by(CandidateSkill.name)
        .order_by(func.count(CandidateSkill.id).desc())
        .limit(limit)
        .all()
    )
    pool_size = (
        db.query(func.count(CandidateProfile.id))
        .filter(CandidateProfile.agency_id == role.agency_id)
        .scalar()
        or 0
    )
    return [r[0] for r in rows], int(pool_size)


def _salary_range(role: Role) -> str | None:
    if role.salary_min and role.salary_max:
        return f"${role.salary_min:,} – ${role.salary_max:,}"
    if role.salary_min:
        return f"From ${role.salary_min:,}"
    if role.salary_max:
        return f"Up to ${role.salary_max:,}"
    return None


def _dedupe(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _titlecase_skill(s: str) -> str:
    # Preserve tokens like c++, ci/cd, next.js; title-case plain words.
    return s if any(ch in s for ch in "+#./") else s.title()


def _build(role: Role, top_skills: list[str], pool_size: int) -> JobListing:
    required = _dedupe(list(role.required_skills or []))
    preferred = _dedupe(list(role.preferred_skills or []))
    nice = _dedupe([s for s in (preferred + top_skills) if s not in required])

    seniority = role.seniority or ""
    lead = f"{seniority + ' ' if seniority else ''}{role.title}".strip()
    salary = _salary_range(role)

    skill_phrase = ", ".join(_titlecase_skill(s) for s in required[:4]) or "the role's core stack"
    summary = (
        f"We're hiring a {lead} to join our team. You'll work hands-on with "
        f"{skill_phrase}, owning meaningful problems end to end. "
        + (f"Compensation: {salary}. " if salary else "")
        + (role.description.strip() if role.description else "")
    ).strip()

    responsibilities = [
        f"Own and deliver work central to the {role.title} role",
        f"Apply your expertise in {skill_phrase} to ship high-quality outcomes",
        "Collaborate closely with the team and stakeholders",
        "Raise the bar on quality, reliability, and craft",
    ]

    requirements: list[str] = []
    if role.min_years_experience:
        requirements.append(f"{role.min_years_experience:g}+ years of relevant experience")
    if seniority:
        requirements.append(f"Proven track record at a {seniority.lower()} level")
    requirements += [f"Strong experience with {_titlecase_skill(s)}" for s in required]
    if not requirements:
        requirements.append("Relevant professional experience for this role")

    listing = JobListing(
        role_id=role.id,
        title=role.title,
        seniority=role.seniority,
        location=role.location,
        employment_type=role.employment_type.value if role.employment_type else None,
        salary_range=salary,
        summary=summary,
        responsibilities=responsibilities,
        requirements=requirements,
        nice_to_have=[_titlecase_skill(s) for s in nice],
        top_pool_skills=top_skills,
        candidate_sample=pool_size,
    )
    listing.content_markdown = _to_markdown(listing)
    return listing


def _to_markdown(li: JobListing) -> str:
    parts: list[str] = [f"# {li.seniority + ' ' if li.seniority else ''}{li.title}".rstrip()]
    meta = [m for m in [li.location, li.employment_type, li.salary_range] if m]
    if meta:
        parts.append(" · ".join(meta))
    parts.append("\n## About the role\n" + li.summary)
    if li.responsibilities:
        parts.append("\n## What you'll do\n" + "\n".join(f"- {r}" for r in li.responsibilities))
    if li.requirements:
        parts.append("\n## What we're looking for\n" + "\n".join(f"- {r}" for r in li.requirements))
    if li.nice_to_have:
        parts.append("\n## Nice to have\n" + "\n".join(f"- {r}" for r in li.nice_to_have))
    return "\n".join(parts).strip()


def _llm_available() -> bool:
    return settings.llm_provider.lower() == "openai" and bool(settings.ai_api_key_value)


def _polish(listing: JobListing) -> JobListing:
    """Optionally rewrite the prose with a real LLM, keeping the grounded facts."""
    try:
        from app.services.ai.factory import get_llm_provider

        system = (
            "You are an expert technical recruiter. Rewrite the given job listing "
            "so it reads naturally and compellingly. Keep every fact, skill, and the "
            "markdown section structure. Do not invent requirements. Return markdown only."
        )
        out = get_llm_provider().generate(system, listing.content_markdown)
        if out and "[MOCK_GENERATION]" not in out and len(out) > 80:
            listing.content_markdown = out.strip()
            listing.polished_by_llm = True
    except Exception:
        pass  # never let a provider hiccup break generation
    return listing


def generate_listing(db: Session, role: Role) -> JobListing:
    top_skills, pool_size = _top_pool_skills(db, role)
    listing = _build(role, top_skills, pool_size)
    if _llm_available():
        listing = _polish(listing)
    return listing
