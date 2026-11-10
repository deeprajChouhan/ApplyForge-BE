"""
Bulk-CV ingestion — Phase 1's chosen entry point for the candidate pool.
Recruiters upload a batch of CVs; each is parsed, embedded, and stored as an
agency-owned CandidateProfile.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.recruiter.enums import CandidateSource
from app.recruiter.models import CandidateExperience, CandidateProfile, CandidateSkill
from app.recruiter.services import documents as documents_service
from app.recruiter.services.matching import embed_candidate
from app.recruiter.services.parsing import parse_cv


@dataclass
class IngestedCandidate:
    candidate_id: int
    full_name: str | None
    email: str | None
    skill_count: int


def ingest_cv(db: Session, agency_id: int, filename: str, content: bytes) -> IngestedCandidate:
    # Validate first (size / type / magic bytes / safe name) — raises
    # DocumentRejected with a caller-safe message.
    file = documents_service.validate_upload(filename, content)
    parsed = parse_cv(file.content, file.filename)

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
        # Display-only original filename; the stored object is referenced by
        # the CandidateDocument row, never by a filesystem path.
        source_file=file.filename,
    )
    db.add(profile)
    db.flush()  # assign profile.id

    documents_service.store_document(
        db, agency_id=agency_id, candidate_id=profile.id, file=file, uploaded_via="bulk_cv"
    )

    add_parsed_details(db, profile, parsed)
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




def add_parsed_details(db: Session, profile: CandidateProfile, parsed) -> None:
    """Attach parsed skills + dated work history to a profile. Caller flushes."""
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


def ingest_batch(
    db: Session, agency_id: int, files: list[tuple[str, bytes]]
) -> list[IngestedCandidate]:
    # Validate the whole batch up-front so a bad file never half-ingests it.
    for name, content in files:
        documents_service.validate_upload(name, content)
    return [ingest_cv(db, agency_id, name, content) for name, content in files]
