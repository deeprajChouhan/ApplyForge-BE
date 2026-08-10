"""
Placement — candidate → best-fit roles (Phase 4, Section 3.2, direction 1).

The inverted matcher run the other way: fix a candidate, rank the agency's OPEN
roles by fit. Reuses the exact scoring used for "who fits a role", so a
candidate's ranking here is consistent with how they'd appear on a role's
shortlist. Rule/benchmark-driven; no ML required on day one.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.recruiter.enums import RoleStatus
from app.recruiter.models import CandidateProfile, Role
from app.recruiter.services.matching import embed_role, score_candidate


@dataclass
class RoleMatch:
    role_id: int
    title: str
    seniority: str | None
    status: str
    fit_score: float
    reasons: list[str]
    gaps: list[str]
    breakdown: dict[str, float]


def rank_roles_for_candidate(
    db: Session,
    agency_id: int,
    candidate: CandidateProfile,
    include_closed: bool = False,
    limit: int | None = None,
) -> list[RoleMatch]:
    q = db.query(Role).filter(Role.agency_id == agency_id)
    if not include_closed:
        q = q.filter(Role.status == RoleStatus.open)
    roles = q.all()

    results: list[RoleMatch] = []
    for role in roles:
        role_vec = role.embedding or embed_role(role)
        res = score_candidate(role, candidate, role_vec)
        results.append(
            RoleMatch(
                role_id=role.id,
                title=role.title,
                seniority=role.seniority,
                status=role.status.value,
                fit_score=res.fit_score,
                reasons=res.reasons,
                gaps=res.gaps,
                breakdown=res.breakdown,
            )
        )

    results.sort(key=lambda r: r.fit_score, reverse=True)
    if limit is not None:
        results = results[:limit]
    return results
