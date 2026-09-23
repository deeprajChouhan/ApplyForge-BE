"""Run inverted matching for a role and persist the ranked result."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.recruiter.models import Application, CandidateProfile, Role, Shortlist, ShortlistEntry
from app.recruiter.services.matching import rank_candidates


def generate_shortlist(db: Session, role: Role, limit: int | None = None) -> Shortlist:
    candidates = (
        db.query(CandidateProfile)
        .filter(CandidateProfile.agency_id == role.agency_id)
        .all()
    )
    results = rank_candidates(role, candidates)
    # Keep pipeline cards in lock-step with the ranking: every application on
    # this role gets the freshly computed score (computed for the whole pool
    # before `limit` is applied, so candidates outside the top-N still update).
    sync_application_fit_scores(db, role, {r.candidate_id: r.fit_score for r in results})
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


def sync_application_fit_scores(
    db: Session, role: Role, score_by_candidate: dict[int, float]
) -> int:
    """
    Write the given scores onto every Application in this role's pipeline.
    Application.fit_score is only a cache of the latest ranking — this is the
    single place that refreshes it. Returns the number of rows changed.
    Caller commits.
    """
    changed = 0
    apps = (
        db.query(Application)
        .filter(Application.agency_id == role.agency_id, Application.role_id == role.id)
        .all()
    )
    for a in apps:
        score = score_by_candidate.get(a.candidate_id)
        if score is not None and a.fit_score != score:
            a.fit_score = score
            changed += 1
    return changed


def refresh_role_fit_scores(db: Session, role: Role) -> int:
    """
    Reconcile cached application scores with the role's latest shortlist.
    Candidates missing from that shortlist (e.g. it was generated with a
    `limit`) are scored directly so the pipeline never shows a stale number.
    Caller commits.
    """
    from app.recruiter.services.matching import embed_role, score_candidate

    latest = (
        db.query(Shortlist)
        .filter(Shortlist.agency_id == role.agency_id, Shortlist.role_id == role.id)
        .order_by(Shortlist.id.desc())
        .first()
    )
    if latest is None:
        return 0
    scores = {e.candidate_id: e.fit_score for e in latest.entries}
    missing = [
        a.candidate
        for a in db.query(Application)
        .filter(Application.agency_id == role.agency_id, Application.role_id == role.id)
        .all()
        if a.candidate_id not in scores and a.candidate is not None
    ]
    if missing:
        role_vec = role.embedding or embed_role(role)
        for cand in missing:
            scores[cand.id] = score_candidate(role, cand, role_vec).fit_score
    return sync_application_fit_scores(db, role, scores)
