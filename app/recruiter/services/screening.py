"""
LLM-drafted screening questions for a specific candidate-in-role.

Priorities, in order:
  1. Confirm gaps the SWOT flagged (missing required skills, experience delta).
  2. Probe seniority signals (scope, ownership, leadership).
  3. Verify claimed skills with concrete "give me an example" prompts.
  4. Cover motivation + compensation reconciliation.

If the LLM is unavailable we return a small deterministic set built off the
role's required skills and the candidate's gaps so the recruiter never sees a
blank tab.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.recruiter.models import Application, CandidateProfile, Role
from app.recruiter.services import ai_support
from app.recruiter.services.skills import normalize_skill


_SYSTEM = (
    "You are a senior technical recruiter drafting a candidate screening call. "
    "You produce 6-8 sharp questions that probe fit for a specific role, "
    "grounded in the candidate's background and the role's requirements. "
    "Questions are open-ended, concrete, and answerable in 1-2 minutes. "
    "Return STRICT JSON only — no prose, no markdown."
)

_SCHEMA_HINT = (
    'Return JSON: {"questions": [{"text": string, "intent": string}, ...]}\n'
    "  - intent: a short label like 'gap probe', 'seniority check', 'motivation', "
    "'compensation', 'skill verification'.\n"
    "  - Prefer 'gap probe' questions when required skills are missing.\n"
    "  - Cap at 8 questions total."
)


def _candidate_skill_set(cand: CandidateProfile) -> set[str]:
    return {normalize_skill(s.name) for s in cand.skills if s.name}


def _missing_required(role: Role, cand_skills: set[str]) -> list[str]:
    req = [normalize_skill(s) for s in (role.required_skills or [])]
    return [s for s in req if s and s not in cand_skills]


def _heuristic_questions(role: Role, cand: CandidateProfile) -> list[dict[str, str]]:
    cand_skills = _candidate_skill_set(cand)
    missing = _missing_required(role, cand_skills)
    out: list[dict[str, str]] = []
    for m in missing[:3]:
        out.append({"text": f"Walk me through your exposure to {m}, even indirect.", "intent": "gap probe"})
    if role.seniority:
        out.append({
            "text": f"What's the largest {role.seniority.lower()}-level piece of work you've owned end-to-end?",
            "intent": "seniority check",
        })
    if role.required_skills:
        primary = role.required_skills[0]
        out.append({
            "text": f"Describe a recent project where {primary} was central. What broke, and how did you fix it?",
            "intent": "skill verification",
        })
    out.append({"text": "What's driving your job search right now?", "intent": "motivation"})
    if role.budget_max is not None:
        out.append({
            "text": "What compensation range are you targeting, and how flexible is it?",
            "intent": "compensation",
        })
    if role.location:
        out.append({
            "text": f"How do you feel about the {role.location} location or remote arrangements for this role?",
            "intent": "logistics",
        })
    return out[:8]


def draft_screening_questions(
    role: Role, cand: CandidateProfile, app_row: Application
) -> dict[str, Any]:
    """
    Returns a dict with `questions: [...], used_llm: bool, generated_at: iso`.
    Always returns something the UI can render.
    """
    heuristic = _heuristic_questions(role, cand)

    if not ai_support.llm_enabled():
        return {
            "questions": heuristic,
            "used_llm": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    cand_skills = _candidate_skill_set(cand)
    missing = _missing_required(role, cand_skills)

    ctx_lines = [
        f"Role: {role.title or 'Untitled'}" + (f" ({role.seniority})" if role.seniority else ""),
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
    ctx_lines.append(f"Candidate: {cand.full_name or 'unnamed'}")
    if cand.headline:
        ctx_lines.append(f"Headline: {cand.headline}")
    ctx_lines.append(
        f"Experience: {cand.years_experience} yrs" if cand.years_experience is not None else "Experience: unknown"
    )
    if cand.summary:
        ctx_lines.append(f"Summary: {cand.summary[:400]}")
    ctx_lines.append(f"Skills: {', '.join(sorted(s for s in cand_skills if s)) or 'unknown'}")
    if missing:
        ctx_lines.append(f"Required skills the candidate is missing: {', '.join(missing)}")
    if app_row.swot:
        for key in ("weaknesses", "threats"):
            items = app_row.swot.get(key) if isinstance(app_row.swot, dict) else None
            if items:
                ctx_lines.append(f"{key.title()}: {'; '.join(items[:4])}")

    user = _SCHEMA_HINT + "\n\nContext:\n" + "\n".join(ctx_lines)
    data = ai_support.generate_json(_SYSTEM, user)

    questions: list[dict[str, str]] = []
    if isinstance(data, dict) and isinstance(data.get("questions"), list):
        for q in data["questions"]:
            if isinstance(q, dict) and isinstance(q.get("text"), str) and q["text"].strip():
                questions.append(
                    {
                        "text": q["text"].strip(),
                        "intent": (q.get("intent") or "").strip() or None,
                    }
                )

    if not questions:
        return {
            "questions": heuristic,
            "used_llm": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "questions": questions[:8],
        "used_llm": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Recruiter OS Phase 1: AI screening copilot ──────────────────────────

_IMPROVE_SYSTEM = (
    "You are a senior technical recruiter. Rewrite the recruiter's rough notes "
    "into a concise, professional summary suitable for a client. Preserve "
    "every claim the recruiter made — never invent skills, salary, notice, or "
    "experience the recruiter did not state. If a fact is not present, omit "
    "it. Return STRICT JSON only."
)

_IMPROVE_SCHEMA = (
    'Return JSON: {"summary": string, "unsupported_gaps": [string, ...]}\n'
    "  - summary: 2-4 sentences, third person, client-safe, no bullet points.\n"
    "  - unsupported_gaps: any topic the client will likely ask about but the "
    "notes do not cover (compensation, availability, notice, technical depth).\n"
)


def improve_summary(rough_notes: str, cand_name: str | None = None, role_title: str | None = None) -> dict[str, Any]:
    """
    Cleans rough recruiter notes into a client-ready summary. Never persists;
    the caller must save via PATCH .../screening after review. Falls back to a
    trimmed version of the input when the LLM is unavailable.
    """
    text = (rough_notes or "").strip()
    if not text:
        return {"summary": "", "unsupported_gaps": [], "used_llm": False,
                "generated_at": datetime.now(timezone.utc).isoformat()}

    if not ai_support.llm_enabled():
        # Deterministic fallback: normalise whitespace + capitalise sentences.
        cleaned = " ".join(text.split())
        if cleaned and cleaned[-1] not in ".!?":
            cleaned += "."
        return {
            "summary": cleaned,
            "unsupported_gaps": [],
            "used_llm": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    ctx = [
        f"Candidate: {cand_name or 'unnamed'}",
        f"Role: {role_title or 'unspecified'}",
        "",
        "Recruiter rough notes:",
        text,
    ]
    data = ai_support.generate_json(_IMPROVE_SYSTEM, _IMPROVE_SCHEMA + "\n\n" + "\n".join(ctx))
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return {
            "summary": text,
            "unsupported_gaps": [],
            "used_llm": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "summary": data["summary"].strip(),
        "unsupported_gaps": [str(x).strip() for x in (data.get("unsupported_gaps") or []) if isinstance(x, str)],
        "used_llm": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


_EXTRACT_SYSTEM = (
    "You are a senior technical recruiter. Given rough screening notes, "
    "extract structured screening fields the recruiter mentioned. NEVER "
    "invent values. When a field is not mentioned, return null. Return "
    "STRICT JSON only."
)

_EXTRACT_SCHEMA = (
    'Return JSON with these keys (any may be null):\n'
    '  {\n'
    '    "expected_compensation": {"amount": int, "currency": string, "period": "YEAR"|"MONTH"|"DAY"|"HOUR"} | null,\n'
    '    "current_compensation":  {"amount": int, "currency": string, "period": "YEAR"|"MONTH"|"DAY"|"HOUR"} | null,\n'
    '    "notice_period":         {"value": int, "unit": "DAY"|"WEEK"|"MONTH", "negotiable": bool} | null,\n'
    '    "availability_immediate": bool | null,\n'
    '    "preferred_work_model":   "remote"|"hybrid"|"onsite"|"flexible" | null,\n'
    '    "preferred_location":     string | null,\n'
    '    "candidate_motivation":   string | null,\n'
    '    "motivation_categories":  [string, ...] | null\n'
    '  }\n'
    "Use ISO currency codes (GBP, INR, AED, USD, EUR, CAD, AUD, SGD).\n"
    "For INR salaries expressed as LPA, convert to annual INR (multiply by 100000).\n"
)


def extract_structured_notes(rough_notes: str, country_code: str | None = None) -> dict[str, Any]:
    """
    Parses rough recruiter notes into a proposed structured payload. The UI
    surfaces each field with a confirm/edit affordance — none of this is
    persisted until the recruiter PATCHes .../screening.
    """
    text = (rough_notes or "").strip()
    empty = {
        "expected_compensation": None,
        "current_compensation": None,
        "notice_period": None,
        "availability_immediate": None,
        "preferred_work_model": None,
        "preferred_location": None,
        "candidate_motivation": None,
        "motivation_categories": None,
    }
    if not text or not ai_support.llm_enabled():
        return {**empty, "used_llm": False,
                "generated_at": datetime.now(timezone.utc).isoformat()}

    ctx = _EXTRACT_SCHEMA + f"\n\nCountry: {country_code or 'unspecified'}\n\nRecruiter rough notes:\n{text}"
    data = ai_support.generate_json(_EXTRACT_SYSTEM, ctx)
    if not isinstance(data, dict):
        return {**empty, "used_llm": False,
                "generated_at": datetime.now(timezone.utc).isoformat()}

    result: dict[str, Any] = dict(empty)
    for k in empty.keys():
        if k in data:
            result[k] = data[k]
    result["used_llm"] = True
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    return result
