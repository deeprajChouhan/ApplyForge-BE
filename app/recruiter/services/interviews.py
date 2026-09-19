"""
Phase 4: interview prep + brief. Scheduling logic (proposed → confirmed) is
in the routes; this module is the AI copilot for interview preparation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.recruiter.models import (
    Application,
    CandidateProfile,
    Interview,
    InterviewFeedback,
    Role,
)
from app.recruiter.services import ai_support
from app.recruiter.services.screening import _candidate_skill_set, _missing_required


def _heuristic_brief(
    role: Role, cand: CandidateProfile, interview: Interview,
    prior_feedback: list[InterviewFeedback],
) -> dict[str, Any]:
    cand_skills = _candidate_skill_set(cand)
    missing = _missing_required(role, cand_skills)
    strengths = [
        s.name for s in (cand.skills or [])
        if s.name and s.name.lower() in {x.lower() for x in (role.required_skills or [])}
    ][:5]
    questions = []
    for m in missing[:3]:
        questions.append({"text": f"Tell me about your experience with {m}.", "intent": "gap probe"})
    if role.seniority:
        questions.append({
            "text": f"Describe a {role.seniority.lower()}-level piece of work you owned end-to-end.",
            "intent": "seniority check",
        })
    questions.append({"text": "What questions do you have about the role?", "intent": "candidate questions"})
    return {
        "likely_topics": (role.required_skills or [])[:6],
        "candidate_strengths": strengths,
        "potential_gaps": missing[:5],
        "suggested_questions": questions[:8],
    }


def interview_brief(
    role: Role, cand: CandidateProfile, app_row: Application,
    interview: Interview, prior_feedback: list[InterviewFeedback],
) -> dict[str, Any]:
    base = _heuristic_brief(role, cand, interview, prior_feedback)
    if not ai_support.llm_enabled():
        return {
            **base,
            "used_llm": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    system = (
        "You are helping a senior recruiter prepare an interviewer brief. "
        "You produce ONLY topics/questions grounded in the supplied context. "
        "Do not invent candidate experience. Return STRICT JSON."
    )
    schema = (
        'Return JSON: {"likely_topics": [string, ...], "candidate_strengths": '
        '[string, ...], "potential_gaps": [string, ...], "suggested_questions": '
        '[{"text": string, "intent": string}, ...]}'
    )
    ctx = [
        f"Role: {role.title or 'Untitled'} ({role.seniority or 'unspecified seniority'})",
        f"Required: {', '.join(role.required_skills or [])}",
        f"Interview stage: {interview.stage.value if interview.stage else 'unspecified'}",
        f"Candidate: {cand.full_name or 'unnamed'} — {cand.headline or ''}",
        f"Skills: {', '.join(s.name for s in (cand.skills or []))}",
    ]
    if app_row.recruiter_summary:
        ctx.append(f"Recruiter summary: {app_row.recruiter_summary}")
    if prior_feedback:
        ctx.append("Prior interview feedback:")
        for f in prior_feedback[:3]:
            if f.comment:
                ctx.append(f"- {f.comment[:200]}")
    data = ai_support.generate_json(system, schema + "\n\n" + "\n".join(ctx))
    if not isinstance(data, dict):
        return {**base, "used_llm": False, "generated_at": datetime.now(timezone.utc).isoformat()}
    return {
        "likely_topics": [str(x) for x in (data.get("likely_topics") or [])][:8] or base["likely_topics"],
        "candidate_strengths": [str(x) for x in (data.get("candidate_strengths") or [])][:6] or base["candidate_strengths"],
        "potential_gaps": [str(x) for x in (data.get("potential_gaps") or [])][:6] or base["potential_gaps"],
        "suggested_questions": [
            {"text": q.get("text", ""), "intent": q.get("intent")}
            for q in (data.get("suggested_questions") or [])
            if isinstance(q, dict) and isinstance(q.get("text"), str) and q["text"].strip()
        ][:8] or base["suggested_questions"],
        "used_llm": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
