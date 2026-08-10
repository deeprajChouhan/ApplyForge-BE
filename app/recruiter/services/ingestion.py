"""
Bulk-CV ingestion — Phase 1's chosen entry point for the candidate pool.
Recruiters upload a batch of CVs; each is parsed, embedded, and stored as an
agency-owned CandidateProfile.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.recruiter.enums import CandidateSource
from app.recruiter.models import CandidateExperience, CandidateProfile, CandidateSkill
from app.recruiter.services.matching import embed_candidate
from app.recruiter.services.parsing import parse_cv


@dataclass
class IngestedCandidate:
    candidate_id: int
    full_name: str | None
    email: str | None
    skill_count: int


def _persist_source_file(agency_id: int, filename: str, content: bytes) -> str | None:
    try:
        folder = os.path.join(settings.upload_dir, "recruiter", str(agency_id))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "wb") as fh:
            fh.write(content)
        return path
    except Exception:
        return None  # storage is best-effort; the profile still gets created


def ingest_cv(db: Session, agency_id: int, filename: str, content: bytes) -> IngestedCandidate:
    parsed = parse_cv(content, filename)
    source_path = _persist_source_file(agency_id, filename, content)

    profile = CandidateProfile(
        agency_id=agency_id,
        source=CandidateSource.bulk_cv,
        full_name=parsed.full_name,
        email=parsed.email,
        phone=parsed.phone,
        headline=parsed.headline,
        location=parsed.location,
        years_experience=parsed.years_experience,
        summary=parsed.summary,
        raw_cv_text=parsed.raw_text,
        source_file=source_path,
    )
    db.add(profile)
    db.flush()  # assign profile.id

    for skill in parsed.skills:
        db.add(CandidateSkill(candidate_id=profile.id, name=skill))

    # Dated work history — only produced by the LLM parse; heuristic yields none.
    for exp in parsed.experiences:
        db.add(
            CandidateExperience(
                candidate_id=profile.id,
                title=exp.title,
                company=exp.company,
                start_date=exp.start_date,
                end_date=exp.end_date,
                description=exp.description,
            )
        )
    db.flush()
    db.refresh(profile)

    profile.embedding = embed_candidate(profile)
    db.commit()
    db.refresh(profile)

    return IngestedCandidate(
        candidate_id=profile.id,
        full_name=profile.full_name,
        email=profile.email,
        skill_count=len(parsed.skills),
    )


def ingest_batch(
    db: Session, agency_id: int, files: list[tuple[str, bytes]]
) -> list[IngestedCandidate]:
    return [ingest_cv(db, agency_id, name, content) for name, content in files]
