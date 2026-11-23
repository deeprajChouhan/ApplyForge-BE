"""
Load the candidate's source resume for one application, with stable refs.

Source-of-truth rules mirror the preview (useResumeData) and the exporter:
  1. The resume pinned to the application (selected_resume_id), else the
     newest parsed resume.
  2. DB profile tables only fill sections the parse is missing.
  3. Suggestions the user already accepted for this application
     (ApplicationCustomization) are layered on top — they are the user's
     own edits, so the tailoring starts from them, never overwrites them blindly.

Every bullet gets a ref ("E1.3" = experience 1, bullet 3; "P2.1" = project 2,
bullet 1) so the LLM can cite evidence and we can verify each citation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import (
    ApplicationCustomization, Certification, Education, JobApplication, ParsedResumeData, Project,
    Skill, User, UserProfile, WorkExperience,
)
from app.services.resume_tailoring.terms import norm, split_bullets


@dataclass
class SrcExperience:
    ref: str
    role: str
    company: str
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class SrcProject:
    ref: str
    name: str
    technologies: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class SourceResume:
    resume_id: int | None
    parse_confidence: float | None
    name: str
    contact: list[str]
    headline: str
    summary: str
    skills: list[str]
    experiences: list[SrcExperience]
    projects: list[SrcProject]
    certifications: list[str]
    education: list[str]
    raw_text: str = ""

    # ── lookups ────────────────────────────────────────────────────────
    def bullet(self, ref: str) -> str | None:
        try:
            head, idx = ref.split(".", 1)
            i = int(idx) - 1
        except (ValueError, AttributeError):
            return None
        for coll in (self.experiences, self.projects):
            for item in coll:
                if item.ref == head and 0 <= i < len(item.bullets):
                    return item.bullets[i]
        return None

    def valid_refs(self) -> set[str]:
        refs = {"HEADLINE", "SUMMARY", "SKILLS"}
        for coll in (self.experiences, self.projects):
            for item in coll:
                refs.add(item.ref)
                refs.update(f"{item.ref}.{i + 1}" for i in range(len(item.bullets)))
        return refs

    def label(self, ref: str) -> str:
        """Human label for a ref, for the UI ('Experience · Engineer @ Acme · bullet 2')."""
        if ref in ("HEADLINE", "SUMMARY", "SKILLS"):
            return ref.title()
        head, _, idx = ref.partition(".")
        for e in self.experiences:
            if e.ref == head:
                base = f"{e.role} @ {e.company}".strip(" @")
                return f"{base} · bullet {idx}" if idx else base
        for p in self.projects:
            if p.ref == head:
                return f"Project {p.name} · bullet {idx}" if idx else f"Project {p.name}"
        return ref

    # ── text views ─────────────────────────────────────────────────────
    def corpus(self) -> str:
        """Everything the candidate has actually written — the grounding set."""
        parts = [self.name, self.headline, self.summary, ", ".join(self.skills)]
        for e in self.experiences:
            parts += [e.role, e.company, e.location, *e.bullets]
        for p in self.projects:
            parts += [p.name, p.technologies, *p.bullets]
        parts += self.certifications + self.education + [self.raw_text]
        return "\n".join(x for x in parts if x)

    def resume_text(self) -> str:
        """Only the content that appears on the rendered resume (for 'before' coverage)."""
        parts = [self.headline, self.summary, ", ".join(self.skills)]
        for e in self.experiences:
            parts += [e.role, *e.bullets]
        for p in self.projects:
            parts += [p.name, p.technologies, *p.bullets]
        parts += self.certifications + self.education
        return "\n".join(x for x in parts if x)

    def for_prompt(self) -> str:
        lines = []
        if self.headline:
            lines.append(f"[HEADLINE] {self.headline}")
        if self.summary:
            lines.append(f"[SUMMARY] {self.summary}")
        if self.skills:
            lines.append(f"[SKILLS] {', '.join(self.skills)}")
        for e in self.experiences:
            dates = f"{e.start_date or '?'} – {e.end_date or 'Present'}"
            lines.append(f"\n[{e.ref}] {e.role} — {e.company} ({dates})")
            for i, b in enumerate(e.bullets, 1):
                lines.append(f"  [{e.ref}.{i}] {b}")
        for p in self.projects:
            tech = f" (tech: {p.technologies})" if p.technologies else ""
            lines.append(f"\n[{p.ref}] Project: {p.name}{tech}")
            for i, b in enumerate(p.bullets, 1):
                lines.append(f"  [{p.ref}.{i}] {b}")
        if self.certifications:
            lines.append("\n[CERTIFICATIONS] " + "; ".join(self.certifications))
        if self.education:
            lines.append("[EDUCATION] " + "; ".join(self.education))
        return "\n".join(lines)


def _prefer(primary: Any, fallback: Any) -> list:
    if isinstance(primary, list) and primary:
        return primary
    if isinstance(fallback, list) and fallback:
        return fallback
    return []


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def resolve_parsed_resume(db: Session, user_id: int, app: JobApplication) -> ParsedResumeData | None:
    q = db.query(ParsedResumeData).filter(
        ParsedResumeData.user_id == user_id, ParsedResumeData.deleted_at.is_(None)
    )
    row = None
    if app.selected_resume_id:
        row = q.filter(ParsedResumeData.id == app.selected_resume_id).first()
    if row is None:
        row = q.order_by(ParsedResumeData.created_at.desc(), ParsedResumeData.id.desc()).first()
    return row


def load_customizations(db: Session, user_id: int, app_id: int) -> tuple[dict, Any]:
    row = db.query(ApplicationCustomization).filter_by(user_id=user_id, application_id=app_id).first()
    if not row:
        return {}, None
    try:
        data = json.loads(row.customizations_json or "{}")
        return (data if isinstance(data, dict) else {}), row.updated_at
    except (json.JSONDecodeError, TypeError):
        return {}, row.updated_at


def load_source_resume(db: Session, user_id: int, app: JobApplication) -> SourceResume:
    parsed_row = resolve_parsed_resume(db, user_id, app)
    rd: dict = {}
    if parsed_row and parsed_row.structured_json:
        try:
            raw = json.loads(parsed_row.structured_json)
            rd = raw if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, TypeError):
            rd = {}

    profile = db.query(UserProfile).filter_by(user_id=user_id).first()
    user = db.query(User).filter_by(id=user_id).first()
    cust, _ = load_customizations(db, user_id, app.id)

    # Experiences
    db_exps = [
        {"id": e.id, "role": e.role, "company": e.company, "location": getattr(e, "location", None),
         "start_date": e.start_date, "end_date": e.end_date, "description": e.description}
        for e in db.query(WorkExperience).filter_by(user_id=user_id).order_by(WorkExperience.start_date.desc()).all()
    ]
    overrides: dict = cust.get("experiences_update") or {}
    by_rc = {
        f"{norm(o.get('role'))}::{norm(o.get('company'))}": o
        for o in overrides.values() if isinstance(o, dict) and (o.get("role") or o.get("company"))
    }
    experiences: list[SrcExperience] = []
    for e in _prefer(rd.get("work_experience"), db_exps):
        if not isinstance(e, dict):
            continue
        ov = (overrides.get(str(e["id"])) if e.get("id") is not None else None) \
            or by_rc.get(f"{norm(e.get('role'))}::{norm(e.get('company'))}")
        desc = (ov or {}).get("description") or e.get("description")
        experiences.append(SrcExperience(
            ref=f"E{len(experiences) + 1}",
            role=_s(e.get("role")), company=_s(e.get("company")), location=_s(e.get("location")),
            start_date=_s(e.get("start_date")), end_date=_s(e.get("end_date")),
            bullets=split_bullets(desc),
        ))

    # Projects
    db_projs = [{"name": p.name, "description": p.description, "technologies": p.technologies}
                for p in db.query(Project).filter_by(user_id=user_id).all()]
    projects: list[SrcProject] = []
    seen: set[str] = set()
    for p in list(_prefer(rd.get("projects"), db_projs)) + list(cust.get("projects_add") or []):
        if not isinstance(p, dict):
            continue
        k = norm(p.get("name"))
        if not k or k in seen:
            continue
        seen.add(k)
        projects.append(SrcProject(
            ref=f"P{len(projects) + 1}", name=_s(p.get("name")),
            technologies=_s(p.get("technologies")), bullets=split_bullets(p.get("description")),
        ))

    # Skills
    db_skills = [s.name for s in db.query(Skill).filter_by(user_id=user_id).all()]
    skills: list[str] = []
    seen = set()
    for s in list(_prefer(rd.get("skills"), db_skills)) + list(cust.get("skills_add") or []):
        name = s if isinstance(s, str) else (s.get("name") if isinstance(s, dict) else None)
        k = norm(name)
        if k and k not in seen:
            seen.add(k)
            skills.append(_s(name))

    # Certifications / education (grounding only — never rewritten)
    certs_src = _prefer(rd.get("certifications"),
                        [{"name": c.name, "issuer": c.issuer}
                         for c in db.query(Certification).filter_by(user_id=user_id).all()])
    certifications = [
        " — ".join(x for x in (_s(c.get("name")), _s(c.get("issuer"))) if x)
        for c in certs_src if isinstance(c, dict) and c.get("name")
    ]
    edu_src = _prefer(rd.get("education"),
                      [{"institution": e.institution, "degree": e.degree, "field_of_study": e.field_of_study}
                       for e in db.query(Education).filter_by(user_id=user_id).all()])
    education = [
        ", ".join(x for x in (_s(e.get("degree")), _s(e.get("field_of_study")), _s(e.get("institution"))) if x)
        for e in edu_src if isinstance(e, dict)
    ]

    return SourceResume(
        resume_id=parsed_row.id if parsed_row else None,
        parse_confidence=float(parsed_row.confidence_score) if parsed_row and parsed_row.confidence_score is not None else None,
        name=_s(rd.get("full_name")) or _s(profile.full_name if profile else ""),
        contact=[x for x in (
            _s(rd.get("email")) or _s(user.email if user else ""),
            _s(rd.get("phone")) or _s(getattr(profile, "phone_number", "") if profile else ""),
            _s(rd.get("location")) or _s(profile.location if profile else ""),
            _s(rd.get("linkedin_url")) or _s(rd.get("linkedin")),
            _s(rd.get("website_url")) or _s(rd.get("website")) or _s(rd.get("github")),
        ) if x],
        headline=_s(rd.get("headline")) or _s(profile.headline if profile else ""),
        summary=_s(rd.get("summary")) or _s(profile.summary if profile else ""),
        skills=skills,
        experiences=experiences,
        projects=projects,
        certifications=[c for c in certifications if c],
        education=[e for e in education if e],
        raw_text=(parsed_row.raw_text or "")[:12000] if parsed_row else "",
    )
