"""
LLM-based structured CV extraction (Phase 6 / intelligence upgrade).

Turns raw CV text into rich structured fields — far beyond what the regex
heuristic can reach: real skills (not just a fixed dictionary), dated work
history, inferred seniority, a clean summary. Returns a plain dict so the parser
can merge it over the heuristic baseline; returns None whenever a real LLM isn't
configured or the output can't be trusted, so ingestion always has a fallback.
"""
from __future__ import annotations

from typing import Any

from app.recruiter.services import ai_support

_SYSTEM = (
    "You are an expert technical recruiter and precise resume parser. You extract "
    "structured data from CVs and return STRICT JSON only — no prose, no markdown. "
    "Never invent facts: if a field isn't present, use null (or an empty list for "
    "skills/experiences). Skills must be concrete technologies, tools, or "
    "professional competencies as short canonical names (e.g. 'python', 'react', "
    "'project management'), lowercased, deduplicated."
)

_SCHEMA_HINT = (
    "Return JSON with exactly these keys:\n"
    "{\n"
    '  "full_name": string|null,\n'
    '  "email": string|null,\n'
    '  "phone": string|null,\n'
    '  "headline": string|null,           // current title / one-line positioning\n'
    '  "location": string|null,\n'
    '  "seniority": string|null,          // e.g. junior, mid, senior, lead, principal\n'
    '  "years_experience": number|null,   // total years, best estimate\n'
    '  "summary": string|null,            // 1-3 sentence professional summary\n'
    '  "skills": string[],\n'
    '  "experiences": [\n'
    '    {"title": string|null, "company": string|null,\n'
    '     "start_date": string|null,      // "YYYY" or "YYYY-MM" if known\n'
    '     "end_date": string|null,        // "YYYY"/"YYYY-MM" or null if current\n'
    '     "description": string|null}\n'
    "  ]\n"
    "}"
)


def llm_parse_cv(raw_text: str) -> dict[str, Any] | None:
    if not ai_support.llm_enabled():
        return None
    text = (raw_text or "").strip()
    if not text:
        return None

    user = f"{_SCHEMA_HINT}\n\nCV text:\n\"\"\"\n{text[:9000]}\n\"\"\""
    data = ai_support.generate_json(_SYSTEM, user)
    if not isinstance(data, dict):
        return None
    return data
