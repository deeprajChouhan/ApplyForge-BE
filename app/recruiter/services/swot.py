"""
Per-candidate-per-role SWOT generation.

The recruiter opens a candidate's card inside a role's Kanban and asks for a
SWOT to guide the interview/offer conversation. This produces a compact,
role-aware analysis:

  - Strengths:      candidate signals that align with what this role asks for.
  - Weaknesses:     required skills the candidate is missing or thin on.
  - Opportunities:  adjacent capabilities that could unlock the role for them
                    (mentorship, ramp-up areas, related industry moves).
  - Threats:        risks to placement (compensation gap vs client budget,
                    location friction, seniority mismatch, tenure pattern).

Deterministic heuristic core so the endpoint always returns something useful
even without an LLM configured; the returned payload is shaped so an LLM-polish
step can be swapped in later without changing callers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.recruiter.models import Application, CandidateProfile, Role
from app.recruiter.services import ai_support
from app.recruiter.services.skills import normalize_skill


def _candidate_skill_set(cand: CandidateProfile) -> set[str]:
    return {normalize_skill(s.name) for s in cand.skills if s.name}


def _budget_gap(role: Role, cand: CandidateProfile) -> str | None:
    """Flag if the candidate's expected budget exceeds what the client budgeted."""
    if role.budget_max is None or cand.expected_budget_min is None:
        return None
    if cand.expected_budget_min > role.budget_max:
        return (
            f"Expected pay ({cand.expected_budget_currency} "
            f"{cand.expected_budget_min:,}+) exceeds client budget "
            f"({role.budget_currency} {role.budget_max:,})"
        )
    return None


def _experience_gap(role: Role, cand: CandidateProfile) -> str | None:
    if role.min_years_experience is None or cand.years_experience is None:
        return None
    delta = role.min_years_experience - cand.years_experience
    if delta > 1.5:
        return f"~{delta:.0f} yrs below the {role.min_years_experience:g}-yr minimum"
    return None


def _location_note(role: Role, cand: CandidateProfile) -> str | None:
    if not role.location or not cand.location:
        return None
    if role.location.strip().lower() not in cand.location.strip().lower():
        return f"Located in {cand.location} vs role in {role.location}"
    return None


def _matching_skills(role: Role, cand_skills: set[str]) -> list[str]:
    req = {normalize_skill(s) for s in (role.required_skills or [])}
    pref = {normalize_skill(s) for s in (role.preferred_skills or [])}
    matched = sorted(cand_skills & (req | pref))
    return matched


def _missing_required(role: Role, cand_skills: set[str]) -> list[str]:
    req = [normalize_skill(s) for s in (role.required_skills or [])]
    return [s for s in req if s and s not in cand_skills]


def _adjacent_opportunities(role: Role, cand_skills: set[str]) -> list[str]:
    """Preferred (nice-to-have) skills the candidate already has = ramp-up wins."""
    pref = [normalize_skill(s) for s in (role.preferred_skills or [])]
    return sorted({s for s in pref if s and s in cand_skills})


def _sentence(items: Iterable[str], head: str, tail: str = "") -> str | None:
    xs = [x for x in items if x]
    if not xs:
        return None
    body = ", ".join(xs[:6])
    if len(xs) > 6:
        body += f", +{len(xs) - 6} more"
    return f"{head} {body}{tail}"


def compute_swot(role: Role, cand: CandidateProfile, app_row: Application) -> dict:
    cand_skills = _candidate_skill_set(cand)
    matched = _matching_skills(role, cand_skills)
    missing = _missing_required(role, cand_skills)
    adjacent = _adjacent_opportunities(role, cand_skills)

    strengths: list[str] = []
    weaknesses: list[str] = []
    opportunities: list[str] = []
    threats: list[str] = []

    if app_row.fit_score is not None and app_row.fit_score >= 75:
        strengths.append(f"Overall fit score {round(app_row.fit_score)}/100 — top of pool")
    s = _sentence(matched, "Direct skill overlap with role:")
    if s:
        strengths.append(s)
    if cand.years_experience and role.min_years_experience and cand.years_experience >= role.min_years_experience + 2:
        strengths.append(
            f"{cand.years_experience:g} yrs experience vs {role.min_years_experience:g}-yr minimum"
        )
    if cand.headline:
        strengths.append(f"Positioning: “{cand.headline}”")

    s = _sentence(missing, "Missing required skills:")
    if s:
        weaknesses.append(s)
    xg = _experience_gap(role, cand)
    if xg:
        weaknesses.append(xg)
    lg = _location_note(role, cand)
    if lg:
        weaknesses.append(lg)

    s = _sentence(adjacent, "Already has preferred skills we could lean into:")
    if s:
        opportunities.append(s)
    if role.seniority and cand.years_experience and cand.years_experience >= (role.min_years_experience or 0) + 3:
        opportunities.append(f"Could stretch beyond {role.seniority} scope over time")
    if not opportunities:
        opportunities.append("Assess growth trajectory and adjacent-skill velocity in screen")

    bg = _budget_gap(role, cand)
    if bg:
        threats.append(bg)
    if not cand.email:
        threats.append("No email on file — reachability risk")
    if len(missing) >= 3:
        threats.append(f"{len(missing)} required skills missing — ramp-up cost")
    if not threats:
        threats.append("No structural risks flagged from data; verify motivation in screen")

    payload = {
        "strengths": strengths,
        "weaknesses": weaknesses,
        "opportunities": opportunities,
        "threats": threats,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": "heuristic-v1",
    }

    # If a real LLM is configured, polish the bullets through it. The LLM only
    # rewrites; it never invents new items or changes the count. Any failure
    # (rate limit, malformed output, missing keys) leaves the heuristic payload
    # untouched, so the caller always gets a working SWOT.
    polished = _llm_polish(role, cand, app_row, payload)
    return polished or payload


# ── LLM polish ──────────────────────────────────────────────────────────
_SYSTEM = (
    "You are a senior technical recruiter writing internal notes on a candidate "
    "for a specific role. You rewrite terse bullet points into sharp, hiring-manager-"
    "ready lines: precise, evidence-oriented, one line each. You never invent new "
    "facts, never add or remove bullets, and never contradict the input. Return "
    "STRICT JSON only — no prose, no markdown."
)


def _bullet_hint(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "(none)"


def _llm_polish(
    role: Role,
    cand: CandidateProfile,
    app_row: Application,
    heuristic: dict,
) -> dict | None:
    if not ai_support.llm_enabled():
        return None

    ctx_lines = [
        f"Role: {role.title or 'Untitled'}"
        + (f" ({role.seniority})" if role.seniority else ""),
        f"Required skills: {', '.join(role.required_skills or []) or 'unspecified'}",
        f"Preferred skills: {', '.join(role.preferred_skills or []) or 'unspecified'}",
        f"Min experience: {role.min_years_experience} yrs" if role.min_years_experience else "Min experience: unspecified",
        f"Location: {role.location or 'unspecified'}",
    ]
    if role.budget_min or role.budget_max:
        ctx_lines.append(
            f"Client budget: {role.budget_currency} "
            f"{(role.budget_min or 0):,}–{(role.budget_max or 0):,}"
        )
    ctx_lines.append("")
    ctx_lines.append(
        f"Candidate: {cand.full_name or 'unnamed'}"
        + (f" — {cand.headline}" if cand.headline else "")
    )
    ctx_lines.append(
        f"Experience: {cand.years_experience} yrs" if cand.years_experience is not None else "Experience: unknown"
    )
    ctx_lines.append(f"Location: {cand.location or 'unknown'}")
    ctx_lines.append(
        f"Skills: {', '.join(sorted({(s.name or '').strip() for s in (cand.skills or []) if s.name})) or 'unknown'}"
    )
    if cand.expected_budget_min or cand.expected_budget_max:
        ctx_lines.append(
            f"Expected pay: {cand.expected_budget_currency} "
            f"{(cand.expected_budget_min or 0):,}–{(cand.expected_budget_max or 0):,}"
        )
    if app_row.fit_score is not None:
        ctx_lines.append(f"Model fit score: {round(app_row.fit_score)}/100")

    user = (
        "Polish these heuristic bullets into recruiter-ready lines. Keep the same "
        "number of items per section, keep their intent, and stay grounded in the "
        "facts below. Return JSON with keys strengths, weaknesses, opportunities, "
        "threats — each a list of strings.\n\n"
        f"Context:\n{chr(10).join(ctx_lines)}\n\n"
        f"Strengths:\n{_bullet_hint(heuristic['strengths'])}\n\n"
        f"Weaknesses:\n{_bullet_hint(heuristic['weaknesses'])}\n\n"
        f"Opportunities:\n{_bullet_hint(heuristic['opportunities'])}\n\n"
        f"Threats:\n{_bullet_hint(heuristic['threats'])}"
    )

    data = ai_support.generate_json(_SYSTEM, user)
    if not isinstance(data, dict):
        return None

    def _clean(key: str) -> list[str] | None:
        v = data.get(key)
        if not isinstance(v, list):
            return None
        out = [str(x).strip() for x in v if isinstance(x, (str, int, float)) and str(x).strip()]
        return out or None

    polished = {
        "strengths": _clean("strengths") or heuristic["strengths"],
        "weaknesses": _clean("weaknesses") or heuristic["weaknesses"],
        "opportunities": _clean("opportunities") or heuristic["opportunities"],
        "threats": _clean("threats") or heuristic["threats"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": "llm-polish-v1",
    }
    return polished
