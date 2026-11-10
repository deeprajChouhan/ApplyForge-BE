"""
Candidate erasure (UK GDPR Art. 17) — one code path for the recruiter app and
the Partner API.

Deletes stored files first (the storage backend has no FK cascade), then every
rec_* row that references the candidate or one of their applications, children
before parents (metadata dependency order), so it behaves the same on MySQL
(where FK cascades would also fire) and on SQLite (where they may not).
"""
from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.recruiter.models import RECRUITER_TABLES, AiInsightCache, Application, CandidateProfile
from app.recruiter.services import documents as documents_service

_REC_TABLE_NAMES = {t.name for t in RECRUITER_TABLES}


def _recruiter_tables_children_first():
    return [t for t in reversed(Base.metadata.sorted_tables) if t.name in _REC_TABLE_NAMES]


def erase_candidate(db: Session, agency_id: int, cand: CandidateProfile) -> dict:
    """
    Erase a candidate and everything hanging off them. Caller commits.

    Stored files go first and any storage failure raises (DocumentDeleteError):
    the caller rolls back and reports the erasure as incomplete rather than
    claiming success while a CV is still sitting in storage.
    """
    assert cand.agency_id == agency_id
    files_removed = documents_service.delete_candidate_files(db, agency_id, cand.id)
    db.flush()

    app_ids = list(
        db.execute(
            select(Application.id).where(
                Application.agency_id == agency_id, Application.candidate_id == cand.id
            )
        ).scalars()
    )

    # Loose references (parent_kind/parent_id) have no FK, so collect every
    # parent the candidate owns before the rows disappear.
    parents: dict[str, set[int]] = {"candidate": {cand.id}, "application": set(app_ids)}
    for kind, table_name in (
        ("interview", "rec_interviews"), ("offer", "rec_offers"),
        ("placement", "rec_placements"), ("submission", "rec_client_submissions"),
    ):
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        cond = []
        if "candidate_id" in table.c:
            cond.append(table.c.candidate_id == cand.id)
        if app_ids and "application_id" in table.c:
            cond.append(table.c.application_id.in_(app_ids))
        if cond:
            parents[kind] = set(db.execute(select(table.c.id).where(or_(*cond))).scalars())
    for table_name in ("rec_notifications", "rec_recruiter_tasks"):
        table = Base.metadata.tables.get(table_name)
        if table is None or "parent_kind" not in table.c:
            continue
        for kind, ids in parents.items():
            if ids:
                db.execute(
                    delete(table).where(
                        table.c.agency_id == agency_id,
                        table.c.parent_kind == kind,
                        table.c.parent_id.in_(ids),
                    )
                )
    # AI insight cache payloads can quote candidate names; it is a cache, so
    # drop the agency's entries and let them regenerate.
    db.execute(delete(AiInsightCache.__table__).where(AiInsightCache.__table__.c.agency_id == agency_id))

    for table in _recruiter_tables_children_first():
        cols = table.c
        if table.name in ("rec_candidate_profiles", "rec_applications"):
            continue
        if app_ids and "application_id" in cols:
            db.execute(delete(table).where(cols.application_id.in_(app_ids)))
        if "candidate_id" in cols:
            stmt = delete(table).where(cols.candidate_id == cand.id)
            if "agency_id" in cols:
                stmt = stmt.where(cols.agency_id == agency_id)
            db.execute(stmt)

    if app_ids:
        db.execute(delete(Application.__table__).where(Application.__table__.c.id.in_(app_ids)))
    cand_id = cand.id
    db.expire(cand)  # child rows were removed above; don't let ORM cascade re-delete stale copies
    db.delete(cand)
    db.flush()
    from app.recruiter.partner import webhooks

    webhooks.emit(db, agency_id, "candidate.deleted", {"candidate_id": cand_id})
    return {"files_removed": files_removed, "applications_removed": len(app_ids)}
