"""
Ask-about-this-candidate — grounded QA over one candidate's evidence.

Volumes per candidate are small (one CV, a handful of experiences, N notes),
so we don't need a vector index for this scope. We build a compact evidence
bundle and ground the LLM on it directly. The bundle keeps section markers so
the model's answer can cite by section id, and we surface those citations back
to the UI as chips the recruiter can inspect.

If the LLM isn't configured we return a deterministic "keyword-lookup" answer
that grep-scans the evidence bundle for the question's keywords and returns the
top matching lines — enough to be useful, honest that no AI ran.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.recruiter.models import (
    Application,
    ApplicationNote,
    CandidateProfile,
    Role,
)
from app.recruiter.services import ai_support


_SYSTEM = (
    "You are a recruiter's research assistant. You answer questions about ONE "
    "candidate strictly from the evidence bundle you are given. Never invent "
    "facts; if the evidence doesn't answer it, say so clearly. Reply in 1-4 "
    "sentences unless the recruiter asks for detail. Cite the sections you "
    "used by their id, in square brackets like [cv], [role], [note-3], at the "
    "end of the sentence they support."
)


@dataclass
class EvidenceSection:
    id: str
    label: str
    text: str


def _experiences_section(cand: CandidateProfile) -> str | None:
    lines = []
    for e in cand.experiences or []:
        when = ""
        if e.start_date or e.end_date:
            when = f" ({e.start_date or '?'} – {e.end_date or 'present'})"
        head = f"- {e.title or 'Role'}"
        if e.company:
            head += f" at {e.company}"
        head += when
        lines.append(head)
        if e.description:
            lines.append(f"    {e.description}")
    return "\n".join(lines) if lines else None


def _build_evidence(
    cand: CandidateProfile,
    role: Role | None,
    notes: list[ApplicationNote],
) -> list[EvidenceSection]:
    sections: list[EvidenceSection] = []

    profile_bits = [
        f"Name: {cand.full_name or 'unknown'}",
        f"Headline: {cand.headline}" if cand.headline else None,
        f"Location: {cand.location}" if cand.location else None,
        f"Experience: {cand.years_experience} yrs" if cand.years_experience is not None else None,
        f"Email: {cand.email}" if cand.email else None,
        f"Phone: {cand.phone}" if cand.phone else None,
    ]
    sections.append(
        EvidenceSection(
            id="profile",
            label="Profile",
            text="\n".join(x for x in profile_bits if x),
        )
    )

    if cand.summary:
        sections.append(EvidenceSection(id="summary", label="Summary", text=cand.summary))

    skills = sorted({(s.name or "").strip() for s in (cand.skills or []) if s.name})
    if skills:
        sections.append(
            EvidenceSection(id="skills", label="Skills", text=", ".join(skills))
        )

    exp = _experiences_section(cand)
    if exp:
        sections.append(EvidenceSection(id="experiences", label="Experiences", text=exp))

    if cand.raw_cv_text:
        # Cap the raw CV so the prompt stays under model limits.
        sections.append(
            EvidenceSection(id="cv", label="Raw CV", text=cand.raw_cv_text[:8000])
        )

    if role is not None:
        role_bits = [
            f"Title: {role.title}",
            f"Seniority: {role.seniority}" if role.seniority else None,
            f"Location: {role.location}" if role.location else None,
            f"Required: {', '.join(role.required_skills or []) or 'unspecified'}",
            f"Preferred: {', '.join(role.preferred_skills or []) or 'unspecified'}",
            f"Min experience: {role.min_years_experience} yrs" if role.min_years_experience else None,
        ]
        if role.description:
            role_bits.append(f"Description: {role.description[:2000]}")
        sections.append(
            EvidenceSection(
                id="role",
                label="Role context",
                text="\n".join(x for x in role_bits if x),
            )
        )

    for i, n in enumerate(notes[:20]):
        author = n.author_name or ("system" if n.kind == "system" else "recruiter")
        when = n.created_at.strftime("%Y-%m-%d") if n.created_at else "?"
        sections.append(
            EvidenceSection(
                id=f"note-{i + 1}",
                label=f"Note ({when}, {author}, {n.kind})",
                text=n.body,
            )
        )

    return sections


def _fallback_answer(question: str, sections: list[EvidenceSection]) -> dict[str, Any]:
    """Deterministic grep-based fallback when the LLM isn't configured."""
    tokens = [t for t in re.findall(r"[a-zA-Z0-9\-\+]+", question.lower()) if len(t) > 2]
    hits: list[tuple[str, str]] = []
    for s in sections:
        low = s.text.lower()
        if any(t in low for t in tokens):
            snippet = s.text[:280] + ("…" if len(s.text) > 280 else "")
            hits.append((s.id, snippet))
        if len(hits) >= 4:
            break
    if not hits:
        return {
            "answer": (
                "I couldn't run an AI search (LLM isn't configured), and a keyword "
                "scan of the evidence didn't turn up anything. Try rephrasing, or "
                "browse the profile directly."
            ),
            "citations": [],
            "used_llm": False,
        }
    body = "\n\n".join(f"[{sid}] {snip}" for sid, snip in hits)
    return {
        "answer": f"AI is off — here are the closest matches from the evidence:\n\n{body}",
        "citations": [sid for sid, _ in hits],
        "used_llm": False,
    }


def _extract_citations(answer: str, section_ids: set[str]) -> list[str]:
    found = re.findall(r"\[([a-zA-Z0-9\-]+)\]", answer)
    seen, out = set(), []
    for c in found:
        if c in section_ids and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def ask_about_candidate(
    db: Session,
    cand: CandidateProfile,
    question: str,
    *,
    role: Role | None = None,
) -> dict[str, Any]:
    """Return {answer, citations, evidence, used_llm, generated_at}."""
    # Pull every application-note for this candidate across all roles — the
    # recruiter's shortlist can cite screen-call feedback even when they're
    # asking without a role in mind.
    notes = (
        db.query(ApplicationNote)
        .join(Application, ApplicationNote.application_id == Application.id)
        .filter(Application.candidate_id == cand.id, Application.agency_id == cand.agency_id)
        .order_by(ApplicationNote.id.desc())
        .all()
    )

    sections = _build_evidence(cand, role, notes)
    section_index = {s.id: s for s in sections}

    if not ai_support.llm_enabled():
        fallback = _fallback_answer(question, sections)
        fallback["evidence"] = [{"id": s.id, "label": s.label} for s in sections]
        fallback["generated_at"] = datetime.now(timezone.utc).isoformat()
        return fallback

    bundle = "\n\n".join(f"[{s.id}] {s.label}\n{s.text}" for s in sections)
    user = (
        f"Evidence bundle:\n\n{bundle}\n\n"
        f"Question: {question.strip()}\n\n"
        "Answer strictly from the evidence. Cite section ids in square brackets "
        "at the end of the sentence(s) they support."
    )
    answer = ai_support.generate(_SYSTEM, user)
    if not answer:
        fallback = _fallback_answer(question, sections)
        fallback["evidence"] = [{"id": s.id, "label": s.label} for s in sections]
        fallback["generated_at"] = datetime.now(timezone.utc).isoformat()
        return fallback

    citations = _extract_citations(answer, set(section_index.keys()))
    return {
        "answer": answer.strip(),
        "citations": citations,
        "evidence": [{"id": s.id, "label": s.label} for s in sections],
        "used_llm": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
