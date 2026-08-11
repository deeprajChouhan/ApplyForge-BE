"""
LLM-based JD → structured role draft.

Recruiter pastes a job description (or a client email describing the role) and
we return a partial RoleCreate payload the frontend can pre-fill. Fail-soft:
if the LLM isn't configured or returns garbage, the endpoint returns an empty
draft with a `used_llm: False` flag so the UI can nudge the recruiter to fill
it manually.

We deliberately keep the schema tight so the LLM can't drift into narrative
prose — every field is either a string, number, or list of short strings.
"""
from __future__ import annotations

from typing import Any

from app.recruiter.services import ai_support
from app.recruiter.services.skills import normalize_skill


_SYSTEM = (
    "You are an expert technical recruiter. You extract structured role data from "
    "job descriptions or client emails and return STRICT JSON only — no prose, no "
    "markdown fences. Never invent facts: use null (or empty list) when a field "
    "isn't stated. Skill names must be concrete tools/technologies/competencies, "
    "lowercased short canonical names ('python', 'react', 'stakeholder management')."
)


_SCHEMA_HINT = (
    "Return JSON with exactly these keys:\n"
    "{\n"
    '  "title": string|null,\n'
    '  "seniority": string|null,          // junior, mid, senior, lead, principal, ...\n'
    '  "employment_type": string|null,    // one of: full_time, part_time, contract, internship, temporary\n'
    '  "location": string|null,\n'
    '  "min_years_experience": number|null,\n'
    '  "salary_min": number|null,         // integer, no currency symbol\n'
    '  "salary_max": number|null,\n'
    '  "budget_currency": string|null,    // 3-letter, e.g. USD, GBP, EUR\n'
    '  "required_skills": string[],       // must-haves\n'
    '  "preferred_skills": string[],      // nice-to-haves\n'
    '  "description": string|null,        // 2-4 sentence clean summary\n'
    '  "notes": string|null               // internal note from the recruiter angle (blockers, oddities)\n'
    "}"
)


_ALLOWED_EMPLOYMENT = {"full_time", "part_time", "contract", "internship", "temporary"}


def _clean_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _clean_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        n = int(float(v))
        return n if 0 <= n <= 10_000_000 else None
    except (TypeError, ValueError):
        return None


def _clean_num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        n = float(v)
        return n if 0 <= n <= 60 else None
    except (TypeError, ValueError):
        return None


def _clean_skills(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in v:
        s = normalize_skill(str(item))
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out[:30]  # cap to keep drafts sane


def parse_jd(text: str) -> dict[str, Any]:
    """
    Return a dict with the same keys the frontend role form uses, plus
    `used_llm: bool` so the caller knows whether it's a real extraction.
    Always returns a dict (empty draft on failure) so the UI never blocks.
    """
    empty = {
        "title": None,
        "seniority": None,
        "employment_type": None,
        "location": None,
        "min_years_experience": None,
        "salary_min": None,
        "salary_max": None,
        "budget_currency": None,
        "required_skills": [],
        "preferred_skills": [],
        "description": None,
        "notes": None,
        "used_llm": False,
    }

    stripped = (text or "").strip()
    if not stripped:
        return empty

    user = f"{_SCHEMA_HINT}\n\nJob description:\n\"\"\"\n{stripped[:9000]}\n\"\"\""
    data = ai_support.generate_json(_SYSTEM, user)
    if not isinstance(data, dict):
        return empty

    employment = _clean_str(data.get("employment_type"))
    if employment and employment.lower() not in _ALLOWED_EMPLOYMENT:
        employment = None

    currency = _clean_str(data.get("budget_currency"))
    if currency:
        currency = currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            currency = None

    return {
        "title": _clean_str(data.get("title")),
        "seniority": _clean_str(data.get("seniority")),
        "employment_type": employment.lower() if employment else None,
        "location": _clean_str(data.get("location")),
        "min_years_experience": _clean_num(data.get("min_years_experience")),
        "salary_min": _clean_int(data.get("salary_min")),
        "salary_max": _clean_int(data.get("salary_max")),
        "budget_currency": currency,
        "required_skills": _clean_skills(data.get("required_skills")),
        "preferred_skills": _clean_skills(data.get("preferred_skills")),
        "description": _clean_str(data.get("description")),
        "notes": _clean_str(data.get("notes")),
        "used_llm": True,
    }
