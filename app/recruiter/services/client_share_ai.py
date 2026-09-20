"""
AI-generated client-facing status summary for the client share link.

Design notes:
 - Fully grounded in the client's own analytics (roles, stage counts, top
   skills, recent placements). No candidate PII goes into the prompt so no
   PII can leak into the summary.
 - Fail-soft: if no real LLM is configured, or the model returns nothing,
   we compute a deterministic bullet summary from the same analytics.
 - Every call is metered against the agency's monthly LLM budget through
   the shared UsageEvent ledger (mirrors how intelligence.py meters calls).
 - Output is short (3-5 short bullets) so it renders cleanly for a client
   opening the link on their phone.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.recruiter.models import Client
from app.recruiter.services import ai_support
from app.recruiter.services.client_analytics import compute_client_analytics


def _deterministic_bullets(analytics: dict[str, Any], client_name: str) -> str:
    """Fallback: bullet summary composed from analytics figures alone."""
    lines: list[str] = []
    roles_open = analytics.get("roles_open") or 0
    active = analytics.get("active_pipeline") or 0
    filled = analytics.get("roles_filled") or 0
    ttf = analytics.get("avg_time_to_fill_days")
    top_skills = analytics.get("top_skills") or []
    recent = analytics.get("recent_placements") or []

    if roles_open:
        lines.append(f"- {roles_open} role{'s' if roles_open != 1 else ''} open for {client_name} right now.")
    if active:
        lines.append(f"- {active} candidate{'s' if active != 1 else ''} progressing across the active pipeline.")
    if filled:
        piece = f"- {filled} role{'s' if filled != 1 else ''} filled to date"
        if ttf is not None:
            piece += f", averaging {ttf} days from brief to placement."
        else:
            piece += "."
        lines.append(piece)
    if top_skills:
        lines.append("- Sourcing focus: " + ", ".join(top_skills[:5]) + ".")
    if recent:
        latest = recent[0]
        title = latest.get("role_title") or "role"
        lines.append(f"- Most recent placement: {title}.")
    if not lines:
        lines.append(f"- No activity yet for {client_name}. Roles will appear here as they open.")
    return "\n".join(lines)


def build_summary(db: Session, client: Client) -> tuple[str, bool]:
    """
    Return (summary_text, used_llm). used_llm=False means we fell back to the
    deterministic bullets, which the UI surfaces so the reader knows.
    """
    analytics = compute_client_analytics(db, client)

    # Client-safe fact sheet — no candidate names/emails/phones.
    facts = {
        "client_name": client.name,
        "industry": client.industry,
        "roles_open": analytics.get("roles_open"),
        "roles_filled": analytics.get("roles_filled"),
        "roles_on_hold": analytics.get("roles_on_hold"),
        "roles_draft": analytics.get("roles_draft"),
        "active_pipeline": analytics.get("active_pipeline"),
        "placements_total": analytics.get("placements_total"),
        "avg_time_to_fill_days": analytics.get("avg_time_to_fill_days"),
        "top_skills": (analytics.get("top_skills") or [])[:8],
        "recent_placement_role_titles": [
            (p.get("role_title") or "role") for p in (analytics.get("recent_placements") or [])[:5]
        ],
        "roles": [
            {
                "title": r.get("title"),
                "status": r.get("status"),
                "seniority": r.get("seniority"),
                "active_pipeline": r.get("active_pipeline"),
                "placed": r.get("placed"),
            }
            for r in (analytics.get("roles") or [])[:20]
        ],
    }

    if not ai_support.llm_enabled():
        return _deterministic_bullets(analytics, client.name), False

    system_prompt = (
        "You write short, factual client-facing status updates for a recruitment "
        "agency's client. Rules: 3 to 5 short bullet points; each bullet starts "
        "with '- '; only use figures present in the provided JSON; never invent "
        "candidate names, salaries, or dates that aren't in the JSON; keep the "
        "tone neutral and professional; no marketing language; no closing "
        "sign-off. If a figure isn't in the JSON, don't mention it."
    )
    user_prompt = (
        "Write the client status summary for the JSON facts below. "
        "The reader is the client's hiring manager.\n\nFACTS:\n" + str(facts)
    )
    out = ai_support.generate(system_prompt, user_prompt)
    if not out:
        return _deterministic_bullets(analytics, client.name), False

    text = out.strip()
    # Cheap guardrail: if the model didn't produce bullet output, fall back
    # rather than risk a shape the client-facing page won't render well.
    if not any(line.strip().startswith("-") for line in text.splitlines()):
        return _deterministic_bullets(analytics, client.name), False
    return text, True
