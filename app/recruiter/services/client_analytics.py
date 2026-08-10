"""
Per-client analytics for the client detail page.

Everything here is derived from the agency's own data — roles they've opened for
this client, the pipeline attached to those roles, and the placements that came
out of them. No cross-client leakage; the caller must have already asserted the
client belongs to the current agency.

Kept intentionally boring:
- One query per aggregate so the response is O(clients × 1) instead of O(N²).
- Time-to-fill uses the wall clock between role creation and the first
  `placed` application on that role. If a role has multiple placements (e.g.
  agency filled two seats on one requisition), we take the earliest.
- top_skills counts a skill each time it appears on any role for this client;
  it's a "what does this client hire for?" signal, not a per-hire ranking.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.recruiter.enums import ACTIVE_STAGES, ApplicationStage, RoleStatus
from app.recruiter.models import Application, CandidateProfile, Client, Role


@dataclass
class _Placement:
    application_id: int
    candidate_id: int
    candidate_name: str | None
    role_id: int | None
    role_title: str | None
    placed_at: datetime | None


def _time_to_fill_days(role_created: datetime | None, placed_at: datetime | None) -> float | None:
    if role_created is None or placed_at is None:
        return None
    delta = placed_at - role_created
    days = delta.total_seconds() / 86_400
    return round(days, 1) if days >= 0 else None


def compute_client_analytics(db: Session, client: Client) -> dict:
    agency_id = client.agency_id

    roles: list[Role] = (
        db.query(Role)
        .filter(Role.agency_id == agency_id, Role.client_id == client.id)
        .order_by(Role.created_at.desc())
        .all()
    )
    role_ids = [r.id for r in roles]

    apps: list[Application] = []
    if role_ids:
        apps = (
            db.query(Application)
            .filter(Application.agency_id == agency_id, Application.role_id.in_(role_ids))
            .all()
        )

    # Per-role rollups keyed by role.id — active pipeline count and placements.
    active_by_role: dict[int, int] = {}
    placed_by_role: dict[int, int] = {}
    first_placed_at_by_role: dict[int, datetime] = {}

    for a in apps:
        if a.role_id is None:
            continue
        if a.stage in ACTIVE_STAGES:
            active_by_role[a.role_id] = active_by_role.get(a.role_id, 0) + 1
        if a.stage == ApplicationStage.placed:
            placed_by_role[a.role_id] = placed_by_role.get(a.role_id, 0) + 1
            existing = first_placed_at_by_role.get(a.role_id)
            if a.last_activity_at and (existing is None or a.last_activity_at < existing):
                first_placed_at_by_role[a.role_id] = a.last_activity_at

    roles_open = sum(1 for r in roles if r.status == RoleStatus.open and not r.is_draft)
    roles_filled = sum(1 for r in roles if r.status == RoleStatus.filled)
    roles_draft = sum(1 for r in roles if r.is_draft)
    roles_on_hold = sum(1 for r in roles if r.status == RoleStatus.on_hold)

    active_pipeline = sum(active_by_role.values())
    placements_total = sum(placed_by_role.values())

    # avg time-to-fill: only over roles that actually got a placement.
    tt_samples: list[float] = []
    for r in roles:
        placed_at = first_placed_at_by_role.get(r.id)
        d = _time_to_fill_days(r.created_at, placed_at)
        if d is not None:
            tt_samples.append(d)
    avg_ttf = round(sum(tt_samples) / len(tt_samples), 1) if tt_samples else None

    # Top skills — union of required + preferred across this client's roles.
    skill_counter: Counter[str] = Counter()
    for r in roles:
        for s in (r.required_skills or []):
            skill_counter[s] += 1
        for s in (r.preferred_skills or []):
            skill_counter[s] += 1
    top_skills = [s for s, _ in skill_counter.most_common(10)]

    # Recent placements: pull candidate names, ordered newest first.
    placed_apps = [a for a in apps if a.stage == ApplicationStage.placed]
    placed_apps.sort(key=lambda a: a.last_activity_at or datetime.min, reverse=True)
    recent_placements: list[_Placement] = []
    if placed_apps:
        cand_ids = {a.candidate_id for a in placed_apps[:10]}
        cand_map = {
            c.id: c
            for c in db.query(CandidateProfile).filter(CandidateProfile.id.in_(cand_ids)).all()
        }
        role_map = {r.id: r for r in roles}
        for a in placed_apps[:10]:
            cand = cand_map.get(a.candidate_id)
            role = role_map.get(a.role_id) if a.role_id else None
            recent_placements.append(
                _Placement(
                    application_id=a.id,
                    candidate_id=a.candidate_id,
                    candidate_name=cand.full_name if cand else None,
                    role_id=a.role_id,
                    role_title=role.title if role else None,
                    placed_at=a.last_activity_at,
                )
            )

    role_rows = [
        {
            "id": r.id,
            "title": r.title,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "is_draft": r.is_draft,
            "seniority": r.seniority,
            "active_pipeline": active_by_role.get(r.id, 0),
            "placed": placed_by_role.get(r.id, 0),
            "created_at": r.created_at,
        }
        for r in roles
    ]

    return {
        "client_id": client.id,
        "roles_open": roles_open,
        "roles_filled": roles_filled,
        "roles_draft": roles_draft,
        "roles_on_hold": roles_on_hold,
        "active_pipeline": active_pipeline,
        "placements_total": placements_total,
        "avg_time_to_fill_days": avg_ttf,
        "top_skills": top_skills,
        "recent_placements": [p.__dict__ for p in recent_placements],
        "roles": role_rows,
    }
