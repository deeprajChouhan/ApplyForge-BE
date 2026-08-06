"""Run inverted matching for a role and persist the ranked result."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.recruiter.models import CandidateProfile, Role, Shortlist, ShortlistEntry
from app.recruiter.services.matching import rank_candidates


def generate_shortlist(db: Session, role: Role, limit: int | None = None) -> Shortlist:
    candidates = (
        db.query(CandidateProfile)
        .filter(CandidateProfile.agency_id == role.agency_id)
        .all()
    )
    results = rank_candidates(role, candidates)
    if limit is not None:
        results = results[:limit]

    shortlist = Shortlist(agency_id=role.agency_id, role_id=role.id)
    db.add(shortlist)
    db.flush()

    for i, res in enumerate(results, start=1):
        db.add(
            ShortlistEntry(
                shortlist_id=shortlist.id,
                candidate_id=res.candidate_id,
                rank=i,
                fit_score=res.fit_score,
                reasons=res.reasons,
                gaps=res.gaps,
                score_breakdown=res.breakdown,
            )
        )

    db.commit()
    db.refresh(shortlist)
    return shortlist
