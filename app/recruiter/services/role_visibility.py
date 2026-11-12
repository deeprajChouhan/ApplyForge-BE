"""
Per-recruiter role visibility — enforced once, at the ORM layer.

An agency owner can restrict a recruiter seat to "only selected roles". For a
request made by such a seat, every ORM statement on the request's Session is
rewritten so hidden roles — and everything that hangs off them — simply do not
exist:

  * SELECTs (lists, detail lookups, Session.get, counts/aggregates, joins,
    lazy-loaded relationships, refreshes) get `with_loader_criteria` filters;
  * ORM UPDATE/DELETE statements get the same criteria;
  * flushes that would create or re-point a row at a hidden role/application
    are rejected with 404 (`before_flush` guard);
  * a role the restricted seat creates is granted to it automatically.

Why here and not per endpoint: the recruiter API has ~100 routes touching role
data. A filter in each handler is one forgotten `.filter()` away from a leak;
a session-level rule can't be forgotten.

Scope of the rule (what's filtered for a restricted viewer):
  Role                            id ∈ visible
  any model with role_id          role_id IS NULL or ∈ visible
  any model with a FK to a role-bearing table (application_id, shortlist_id,
  submission_id, interview_id, offer_id, placement_id, …)
                                  fk IS NULL or parent visible
  Notification / RecruiterTask    loose parent_kind/parent_id → parent visible
  AiInsightCache                  hidden entirely (agency-wide aggregates)

Who is never restricted: owners, platform operators (consumer admin token),
partner-API keys, public share links, background jobs — none of them bind a
viewer to their Session.

Everything stays inside the rec_* data wall.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import and_, event, false, or_, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.recruiter import models as M
from app.recruiter.enums import RecruiterSeatRole

VIEWER_KEY = "rec_role_viewer"
SKIP_OPTION = "rec_skip_role_visibility"

# Loose (FK-less) parent references used by notifications and tasks.
_PARENT_KINDS = {
    "application": "Application",
    "interview": "Interview",
    "offer": "Offer",
    "placement": "Placement",
    "submission": "ClientSubmission",
    "shortlist": "Shortlist",
}


class RoleNotVisible(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=404, detail="Role not found")


@dataclass
class RoleViewer:
    recruiter_id: int
    agency_id: int
    _ids: set[int] | None = field(default=None, repr=False)
    _new_roles: list = field(default_factory=list, repr=False)


# ── Binding ────────────────────────────────────────────────────────────
def is_restricted(db: Session, recruiter: M.Recruiter) -> bool:
    if recruiter.role == RecruiterSeatRole.owner:
        return False
    return (
        db.execute(
            select(M.RecruiterRoleRestriction.recruiter_id).where(
                M.RecruiterRoleRestriction.recruiter_id == recruiter.id
            ),
            execution_options={SKIP_OPTION: True},
        ).first()
        is not None
    )


def bind_viewer(db: Session, recruiter: M.Recruiter) -> None:
    """Call once per request after the recruiter is authenticated."""
    if is_restricted(db, recruiter):
        db.info[VIEWER_KEY] = RoleViewer(recruiter_id=recruiter.id, agency_id=recruiter.agency_id)
    else:
        db.info.pop(VIEWER_KEY, None)


def viewer(db: Session) -> RoleViewer | None:
    return db.info.get(VIEWER_KEY)


def visible_role_ids(db: Session) -> set[int] | None:
    """None = unrestricted. Otherwise the role ids this request may see."""
    v = viewer(db)
    if v is None:
        return None
    if v._ids is None:
        rows = db.execute(
            select(M.RecruiterRoleGrant.role_id)
            .join(M.Role, M.Role.id == M.RecruiterRoleGrant.role_id)
            .where(
                M.RecruiterRoleGrant.recruiter_id == v.recruiter_id,
                M.Role.agency_id == v.agency_id,
            ),
            execution_options={SKIP_OPTION: True},
        ).scalars()
        v._ids = set(rows)
    return v._ids


# ── Criteria ───────────────────────────────────────────────────────────
def _mapped_classes() -> list[type]:
    out = []
    for mapper in M.Base.registry.mappers:
        cls = mapper.class_
        if getattr(cls, "__tablename__", "").startswith("rec_"):
            out.append(cls)
    return out


def _has_col(cls, name: str) -> bool:
    return name in cls.__table__.c


def _role_cond(cls, ids: list[int]):
    col = getattr(cls, "role_id")
    return or_(col.is_(None), col.in_(ids))


_PLAN: dict | None = None


def _plan() -> dict:
    """
    Classify every rec_* model once:
      direct  — has role_id
      via_fk  — FK column → (parent class with role_id)
    """
    global _PLAN
    if _PLAN is not None:
        return _PLAN
    classes = _mapped_classes()
    by_table = {c.__tablename__: c for c in classes}
    role_bearing = {c.__tablename__ for c in classes if _has_col(c, "role_id")}
    skip = {M.RecruiterRoleGrant, M.RecruiterRoleRestriction, M.Role}
    direct, via_fk = [], []
    for cls in classes:
        if cls in skip:
            continue
        if _has_col(cls, "role_id"):
            direct.append(cls)
        links = []
        for col in cls.__table__.c:
            for fk in col.foreign_keys:
                tgt = fk.column.table.name
                if tgt in role_bearing and col.name != "role_id":
                    links.append((col.name, by_table[tgt]))
        if links:
            via_fk.append((cls, links))
    _PLAN = {"direct": direct, "via_fk": via_fk}
    return _PLAN


def _parent_cond(cls, col_name: str, parent, ids: list[int]):
    col = getattr(cls, col_name)
    sub = select(parent.id).where(_role_cond(parent, ids))
    return or_(col.is_(None), col.in_(sub))


def _loose_parent_cond(cls, ids: list[int]):
    kind, pid = cls.parent_kind, cls.parent_id
    branches = [kind.is_(None), kind.notin_(list(_PARENT_KINDS) + ["role"]), and_(kind == "role", pid.in_(ids))]
    for k, name in _PARENT_KINDS.items():
        parent = getattr(M, name, None)
        if parent is None or not _has_col(parent, "role_id"):
            continue
        branches.append(and_(kind == k, pid.in_(select(parent.id).where(_role_cond(parent, ids)))))
    return or_(*branches)


def criteria_options(ids: set[int]) -> list:
    ids_l = sorted(ids)
    plan = _plan()
    opts = [with_loader_criteria(M.Role, M.Role.id.in_(ids_l), include_aliases=True)]
    conds: dict[type, list] = {}
    for cls in plan["direct"]:
        conds.setdefault(cls, []).append(_role_cond(cls, ids_l))
    for cls, links in plan["via_fk"]:
        for col_name, parent in links:
            conds.setdefault(cls, []).append(_parent_cond(cls, col_name, parent, ids_l))
    for name in ("Notification", "RecruiterTask"):
        cls = getattr(M, name, None)
        if cls is not None and _has_col(cls, "parent_kind"):
            conds.setdefault(cls, []).append(_loose_parent_cond(cls, ids_l))
    cache = getattr(M, "AiInsightCache", None)
    if cache is not None:
        conds[cache] = [false()]
    for cls, cs in conds.items():
        opts.append(with_loader_criteria(cls, and_(*cs), include_aliases=True))
    return opts


# ── Session events ─────────────────────────────────────────────────────
@event.listens_for(Session, "do_orm_execute")
def _apply_role_visibility(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get(SKIP_OPTION):
        return
    if viewer(state.session) is None:
        return
    ids = visible_role_ids(state.session) or set()
    state.statement = state.statement.options(*criteria_options(ids))


@event.listens_for(Session, "before_flush")
def _guard_writes(session: Session, flush_context, instances) -> None:
    v = viewer(session)
    if v is None:
        return
    ids = visible_role_ids(session) or set()
    new_role_objs = [o for o in session.new if isinstance(o, M.Role)]
    v._new_roles.extend(new_role_objs)
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, (M.Role, M.RecruiterRoleGrant, M.RecruiterRoleRestriction)):
            continue
        rid = getattr(obj, "role_id", None) if _has_col(type(obj), "role_id") else None
        if rid is not None and rid not in ids and not any(r.id == rid for r in new_role_objs if r.id):
            if obj in session.new or _changed(obj, "role_id"):
                raise RoleNotVisible()
        aid = getattr(obj, "application_id", None) if _has_col(type(obj), "application_id") else None
        if aid is not None and (obj in session.new or _changed(obj, "application_id")):
            app_row = session.get(M.Application, aid)  # criteria applied → None if hidden
            if app_row is None:
                raise RoleNotVisible()


def _changed(obj, attr: str) -> bool:
    from sqlalchemy import inspect as sa_inspect

    hist = sa_inspect(obj).attrs[attr].history
    return bool(hist.added or hist.deleted)


@event.listens_for(Session, "after_flush_postexec")
def _grant_created_roles(session: Session, flush_context) -> None:
    """A restricted seat can always see the roles it creates."""
    v = viewer(session)
    if v is None or not v._new_roles:
        return
    pending, v._new_roles = v._new_roles, []
    ids = visible_role_ids(session)
    for role in pending:
        if role.id is None or role.id in ids:
            continue
        session.add(M.RecruiterRoleGrant(
            agency_id=role.agency_id, recruiter_id=v.recruiter_id, role_id=role.id,
            granted_by_recruiter_id=v.recruiter_id,
        ))
        ids.add(role.id)
