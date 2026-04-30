"""
Resume Suggestion Engine
========================
Generates contextual, job-specific resume improvement suggestions by
cross-referencing the user's saved profile against the JD analysis that
was already produced by the JD Analyze step.

Three categories of suggestions are produced:

  missing_skill   – Skills that appear in the JD (required or preferred)
                    and are implied by the user's experience/projects but
                    are NOT explicitly listed in their Skills section.

  bullet_point    – Rewrites of weak experience description bullets with
                    stronger action verbs and (where possible) quantified
                    impact, tailored to the target role.

  gap             – Specific JD requirements the user doesn't currently
                    satisfy; suggestions on what to add (new project,
                    certification note, highlighted experience) to address
                    each gap.

The LLM is asked to return a structured JSON list so we can parse it
deterministically and attach the correct profile API payloads.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import (
    Certification,
    Education,
    JobApplication,
    Project,
    Skill,
    User,
    UserProfile,
    WorkExperience,
)
from app.schemas.suggestions import SuggestionOut, SuggestionsResponse
from app.services.ai.factory import get_llm_provider

logger = structlog.get_logger(__name__)


class SuggestionService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.llm = get_llm_provider()

    # ── Public API ────────────────────────────────────────────────────────

    def generate(self, app_id: int) -> SuggestionsResponse:
        """
        Main entry-point.  Fetches the application + profile, calls the LLM,
        parses the response, and returns a SuggestionsResponse.
        """
        app = self._get_application(app_id)
        jd_analysis = self._parse_jd_analysis(app)

        if not jd_analysis:
            raise HTTPException(
                status_code=400,
                detail="Run JD Analysis first before generating suggestions.",
            )

        profile_block = self._build_profile_block()
        experiences = self._get_experiences()
        existing_skills = self._get_existing_skills()

        raw_suggestions = self._call_llm(
            app=app,
            jd_analysis=jd_analysis,
            profile_block=profile_block,
            existing_skills=existing_skills,
        )

        suggestions = self._hydrate(raw_suggestions, experiences)
        return SuggestionsResponse(application_id=app_id, suggestions=suggestions)

    # ── Private helpers ───────────────────────────────────────────────────

    def _get_application(self, app_id: int) -> JobApplication:
        app = (
            self.db.query(JobApplication)
            .filter_by(id=app_id, user_id=self.user_id)
            .first()
        )
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        return app

    def _parse_jd_analysis(self, app: JobApplication) -> dict | None:
        if not app.jd_analysis_json:
            return None
        raw = app.jd_analysis_json
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return raw

    def _get_existing_skills(self) -> list[str]:
        return [s.name for s in self.db.query(Skill).filter_by(user_id=self.user_id).all()]

    def _get_experiences(self) -> list[WorkExperience]:
        return (
            self.db.query(WorkExperience)
            .filter_by(user_id=self.user_id)
            .order_by(WorkExperience.start_date.desc())
            .all()
        )

    def _build_profile_block(self) -> str:
        """
        Compact text representation of the user's profile for LLM context.
        Includes: skills, experiences (with descriptions), projects.
        """
        lines: list[str] = []

        # Skills
        skills = self._get_existing_skills()
        if skills:
            lines.append(f"CURRENT SKILLS: {', '.join(skills)}")

        # Experiences
        exps = self._get_experiences()
        if exps:
            lines.append("\nWORK EXPERIENCE:")
            for e in exps:
                period = f"{e.start_date or '?'} – {e.end_date or 'Present'}"
                lines.append(f"  [{e.id}] {e.role} @ {e.company} ({period})")
                if e.description:
                    # Indent each bullet
                    for line in e.description.strip().splitlines():
                        lines.append(f"      {line}")

        # Projects
        projs = self.db.query(Project).filter_by(user_id=self.user_id).all()
        if projs:
            lines.append("\nPROJECTS:")
            for p in projs:
                lines.append(f"  [{p.id}] {p.name}")
                if p.technologies:
                    lines.append(f"      Tech: {p.technologies}")
                if p.description:
                    lines.append(f"      {p.description[:200]}")

        return "\n".join(lines) or "No profile data found."

    # ── LLM call ─────────────────────────────────────────────────────────

    _SYSTEM_PROMPT = """\
You are an expert resume coach and career strategist.
You are given:
  1. A job description analysis (keywords, required skills, preferred skills, gaps).
  2. The candidate's current resume profile (skills, work experience with IDs, projects).

Your task: generate a JSON array of resume improvement suggestions.

Each suggestion MUST follow this exact schema:
{
  "type": "missing_skill" | "bullet_point" | "gap",
  "title": "Short action title, e.g. 'Add skill: Docker'",
  "reason": "1-2 sentence explanation of why this improves the candidate's fit",
  "section": "skills" | "experiences" | "projects",
  "action": "add" | "update",
  "payload": { ...the data to apply to the profile... },
  "target_id": <integer experience/project id if action=update, else null>,
  "target_label": "Human-readable label of what is being updated, else null"
}

Rules:
- missing_skill suggestions: Only suggest skills that (a) appear in required_skills or preferred_skills
  AND (b) are NOT already in the candidate's CURRENT SKILLS list.
  Payload must be: { "name": "<skill name>", "level": "intermediate" }
  Set action="add", section="skills", target_id=null.

- bullet_point suggestions: Pick 2-3 experience bullets that could be much stronger.
  Rewrite the ENTIRE experience description with improved bullets (use action verbs, add
  measurable impact where plausible). Use the experience [id] shown in the profile.
  Payload must be: { "description": "<full improved description text>" }
  Set action="update", section="experiences", target_id=<the [id] integer>.

- gap suggestions: For each unsupported gap, suggest a concrete addition.
  If it is a skill: set section="skills", action="add", payload={"name":"...","level":"beginner"}.
  If it is experience/project based: set section="projects", action="add",
  payload={"name":"...","description":"...","technologies":"..."}.

Return ONLY a valid JSON array. No markdown, no commentary. Maximum 8 suggestions total.
"""

    def _call_llm(
        self,
        app: JobApplication,
        jd_analysis: dict,
        profile_block: str,
        existing_skills: list[str],
    ) -> list[dict]:
        jd_ctx = (
            f"Role: {app.role_title} at {app.company_name}\n\n"
            f"Required Skills: {', '.join(jd_analysis.get('required_skills', []))}\n"
            f"Preferred Skills: {', '.join(jd_analysis.get('preferred_skills', []))}\n"
            f"Gaps: {', '.join(jd_analysis.get('unsupported_gaps', []))}\n"
            f"Strengths: {', '.join(jd_analysis.get('strengths', []))}\n"
            f"Fit Summary: {jd_analysis.get('fit_summary', '')}"
        )

        user_prompt = (
            f"=== JOB DESCRIPTION ANALYSIS ===\n{jd_ctx}\n\n"
            f"=== CANDIDATE PROFILE ===\n{profile_block}"
        )

        logger.info("suggestion_llm_call", app_id=app.id, user_id=self.user_id)

        try:
            raw = self.llm.generate(self._SYSTEM_PROMPT, user_prompt).strip()
        except Exception as exc:
            logger.error("suggestion_llm_error", error=str(exc))
            raise HTTPException(status_code=502, detail=f"AI provider error: {exc}")

        # Strip markdown fences if present
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            suggestions = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("suggestion_parse_error", raw=raw[:500], error=str(exc))
            raise HTTPException(
                status_code=502,
                detail="Failed to parse AI response. Please try again.",
            )

        if not isinstance(suggestions, list):
            raise HTTPException(status_code=502, detail="Unexpected AI response shape.")

        return suggestions

    # ── Hydration ─────────────────────────────────────────────────────────

    def _hydrate(
        self,
        raw: list[dict],
        experiences: list[WorkExperience],
    ) -> list[SuggestionOut]:
        """
        Validate and enrich each raw suggestion dict, assigning stable UUIDs.
        Malformed suggestions are skipped with a warning rather than crashing.
        """
        exp_map: dict[int, WorkExperience] = {e.id: e for e in experiences}
        result: list[SuggestionOut] = []

        for item in raw:
            try:
                suggestion_type = item.get("type")
                if suggestion_type not in ("missing_skill", "bullet_point", "gap"):
                    continue

                section = item.get("section")
                if section not in ("skills", "experiences", "projects"):
                    continue

                action = item.get("action")
                if action not in ("add", "update"):
                    continue

                target_id: int | None = item.get("target_id")
                target_label: str | None = item.get("target_label")

                # Auto-generate target_label for experience updates; skip if id invalid
                if action == "update" and section == "experiences" and target_id:
                    exp = exp_map.get(int(target_id))
                    if exp:
                        target_label = target_label or f"{exp.role} @ {exp.company}"
                    else:
                        logger.warning(
                            "suggestion_invalid_exp_id",
                            target_id=target_id,
                            user_id=self.user_id,
                        )
                        continue

                payload: dict[str, Any] = item.get("payload", {})
                if not isinstance(payload, dict) or not payload:
                    continue

                # ExperienceIn requires company + role — merge from the existing
                # DB record so a description-only LLM payload passes validation.
                if action == "update" and section == "experiences" and target_id:
                    exp = exp_map.get(int(target_id))
                    if exp:
                        base: dict[str, Any] = {"company": exp.company, "role": exp.role}
                        if exp.start_date:
                            base["start_date"] = str(exp.start_date)
                        if exp.end_date:
                            base["end_date"] = str(exp.end_date)
                        # LLM payload (improved description etc.) takes precedence
                        base.update(payload)
                        payload = base

                result.append(
                    SuggestionOut(
                        id=str(uuid.uuid4()),
                        type=suggestion_type,
                        title=str(item.get("title", "Improvement suggestion")),
                        reason=str(item.get("reason", "")),
                        section=section,
                        action=action,
                        payload=payload,
                        target_id=int(target_id) if target_id is not None else None,
                        target_label=target_label,
                    )
                )
            except Exception as exc:
                logger.warning("suggestion_hydration_skip", item=item, error=str(exc))
                continue

        return result
