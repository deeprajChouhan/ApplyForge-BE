"""
Operator data-repair for roles — the things that used to need a SQL session.

  * find_duplicate_roles   — roles in one agency whose titles normalise to the
                             same slug (typically a careers-site sync that
                             created a second copy of a role built in the app).
  * plan_merge / merge     — move applications (and everything hanging off
                             them) from a duplicate onto the role to keep,
                             optionally move the careers-site reference and
                             delete the duplicate.
  * set_external_ref       — link / unlink a role to a careers-site job id.

Everything is scoped to one agency (rec_* tables only — never crosses the
recruiter/consumer data wall). Callers commit and write the audit row.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.recruiter import models as M

logger = logging.getLogger(__name__)

# Role-scoped rows that belong to a candidate's journey on the role and must
# follow the application when it moves.
FOLLOW_MODELS = ("CandidateConsent", "ClientSubmission", "Interview", "Offer", "Placement")
# Role-level artefacts that stay with the duplicate (and are removed if it's deleted).
ROLE_ONLY_MODELS = ("Shortlist", "RoleShareToken", "RoleFeedback")

_REF_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


class RoleOpsError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def slug(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")


def _model(name: str):
    return getattr(M, name, None)


def _role(db: Session, agency_id: int, role_id: int) -> M.Role:
    r = db.get(M.Role, role_id)
    if r is None or r.agency_id != agency_id:
        raise RoleOpsError("Role not found in this agency.", 404)
    return r


def app_counts(db: Session, role_ids: list[int]) -> dict[int, int]:
    if not role_ids:
        return {}
    rows = (
        db.query(M.Application.role_id, func.count(M.Application.id))
        .filter(M.Application.role_id.in_(role_ids))
        .group_by(M.Application.role_id)
        .all()
    )
    return {rid: n for rid, n in rows}


def role_summary(role: M.Role, applications: int) -> dict:
    return {
        "id": role.id,
        "title": role.title,
        "status": role.status.value if role.status else None,
        "is_draft": bool(role.is_draft),
        "source": role.source or "app",
        "external_ref": role.external_ref,
        "client_id": role.client_id,
        "location": role.location,
        "applications": applications,
        "created_at": role.created_at,
    }


def list_roles(db: Session, agency_id: int, q: str | None = None, limit: int = 200) -> list[dict]:
    query = db.query(M.Role).filter(M.Role.agency_id == agency_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((M.Role.title.ilike(like)) | (M.Role.external_ref.ilike(like)))
    roles = query.order_by(M.Role.id.desc()).limit(limit).all()
    counts = app_counts(db, [r.id for r in roles])
    return [role_summary(r, counts.get(r.id, 0)) for r in roles]


def find_duplicate_roles(db: Session, agency_id: int) -> list[dict]:
    roles = db.query(M.Role).filter(M.Role.agency_id == agency_id).all()
    groups: dict[str, list[M.Role]] = defaultdict(list)
    for r in roles:
        key = slug(r.title)
        if key:
            groups[key].append(r)
    counts = app_counts(db, [r.id for r in roles])
    out = []
    for key, rs in groups.items():
        if len(rs) < 2:
            continue
        rs.sort(key=lambda r: r.id)
        # Suggest keeping the app-built role (the recruiter's spec); fall back to the oldest.
        suggested = next((r for r in rs if (r.source or "app") == "app"), rs[0])
        out.append({
            "key": key,
            "title": rs[0].title,
            "suggested_keep_id": suggested.id,
            "has_portal_copy": any((r.source or "app") == "partner_api" for r in rs),
            "roles": [role_summary(r, counts.get(r.id, 0)) for r in rs],
        })
    out.sort(key=lambda g: (not g["has_portal_copy"], g["title"].lower()))
    return out


# ── Merge ──────────────────────────────────────────────────────────────
@dataclass
class MergePlan:
    source: dict
    target: dict
    move_external_ref: bool
    delete_source: bool
    rescore: bool
    applications_to_move: list[dict] = field(default_factory=list)
    clashes: list[dict] = field(default_factory=list)
    related_rows: dict[str, int] = field(default_factory=dict)
    role_only_rows: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    applied: bool = False
    rescored: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def _app_row(a: M.Application) -> dict:
    cand = a.candidate
    return {
        "application_id": a.id,
        "candidate_id": a.candidate_id,
        "candidate_name": getattr(cand, "full_name", None) if cand else None,
        "stage": a.stage.value if a.stage else None,
        "fit_score": a.fit_score,
        "source": a.source,
    }


def plan_merge(
    db: Session,
    agency_id: int,
    source_role_id: int,
    target_role_id: int,
    *,
    move_external_ref: bool = True,
    delete_source: bool = False,
    rescore: bool = True,
) -> tuple[MergePlan, M.Role, M.Role, list[M.Application]]:
    if source_role_id == target_role_id:
        raise RoleOpsError("Pick two different roles.", 422)
    src = _role(db, agency_id, source_role_id)
    tgt = _role(db, agency_id, target_role_id)
    counts = app_counts(db, [src.id, tgt.id])
    plan = MergePlan(
        source=role_summary(src, counts.get(src.id, 0)),
        target=role_summary(tgt, counts.get(tgt.id, 0)),
        move_external_ref=move_external_ref,
        delete_source=delete_source,
        rescore=rescore,
    )

    apps = db.query(M.Application).filter(M.Application.role_id == src.id).order_by(M.Application.id).all()
    on_target = {
        c for (c,) in db.query(M.Application.candidate_id).filter(M.Application.role_id == tgt.id).all()
    }
    movable = []
    for a in apps:
        if a.candidate_id in on_target:
            plan.clashes.append(_app_row(a))
        else:
            plan.applications_to_move.append(_app_row(a))
            movable.append(a)
    moving_candidates = {a.candidate_id for a in movable}

    for name in FOLLOW_MODELS:
        model = _model(name)
        if model is None or not hasattr(model, "role_id"):
            continue
        q = db.query(model).filter(model.role_id == src.id)
        if hasattr(model, "candidate_id"):
            q = q.filter(model.candidate_id.in_(moving_candidates or {-1}))
        n = q.count()
        if n:
            plan.related_rows[name] = n
    for name in ROLE_ONLY_MODELS:
        model = _model(name)
        if model is None or not hasattr(model, "role_id"):
            continue
        n = db.query(model).filter(model.role_id == src.id).count()
        if n:
            plan.role_only_rows[name] = n

    if move_external_ref:
        if not src.external_ref:
            plan.warnings.append("The duplicate has no careers-site reference — nothing to move.")
        elif tgt.external_ref and tgt.external_ref != src.external_ref:
            raise RoleOpsError(
                f"The role to keep is already linked to '{tgt.external_ref}'. "
                "Untick 'move careers-site reference' or unlink it first."
            )
    if tgt.status is None or tgt.status.value != "open" or tgt.is_draft:
        plan.warnings.append("The role to keep is not open — the careers site will get 'job closed' until it's reopened.")
    if plan.clashes:
        plan.warnings.append(
            f"{len(plan.clashes)} candidate(s) already applied to the role to keep — "
            "those applications stay on the duplicate."
        )
    if delete_source:
        if plan.clashes:
            raise RoleOpsError("Can't delete the duplicate while it still has applications that clash. Untick delete.")
        if plan.role_only_rows:
            plan.warnings.append(
                "Deleting the duplicate also removes its "
                + ", ".join(f"{v} {k}" for k, v in plan.role_only_rows.items())
                + "."
            )
    return plan, src, tgt, movable


def merge(
    db: Session,
    agency_id: int,
    source_role_id: int,
    target_role_id: int,
    *,
    move_external_ref: bool = True,
    delete_source: bool = False,
    rescore: bool = True,
) -> MergePlan:
    plan, src, tgt, movable = plan_merge(
        db, agency_id, source_role_id, target_role_id,
        move_external_ref=move_external_ref, delete_source=delete_source, rescore=rescore,
    )
    moving_candidates = {a.candidate_id for a in movable}

    if move_external_ref and src.external_ref:
        ref = src.external_ref
        src.external_ref = None
        db.flush()  # free the (agency_id, external_ref) unique slot first
        tgt.external_ref = ref

    now = datetime.utcnow()
    for a in movable:
        a.role_id = tgt.id
        a.last_activity_at = now
    for name in FOLLOW_MODELS:
        model = _model(name)
        if model is None or not hasattr(model, "role_id"):
            continue
        q = db.query(model).filter(model.role_id == src.id)
        if hasattr(model, "candidate_id"):
            q = q.filter(model.candidate_id.in_(moving_candidates or {-1}))
        q.update({model.role_id: tgt.id}, synchronize_session=False)
    db.flush()

    if rescore and movable:
        plan.rescored = _rescore(db, tgt, movable)

    if delete_source:
        db.delete(src)
    plan.applied = True
    return plan


def _rescore(db: Session, role: M.Role, apps: list[M.Application]) -> int:
    """Fit scores were computed against the duplicate's spec — recompute against the kept role."""
    from app.recruiter.services.matching import embed_role, score_candidate

    try:
        emb = role.embedding or embed_role(role)
    except Exception as exc:  # embedding provider down: keep old scores, don't fail the merge
        logger.warning("role_ops_rescore_embed_failed role=%s error=%s", role.id, exc)
        return 0
    n = 0
    for a in apps:
        try:
            res = score_candidate(role, a.candidate, emb)
        except Exception as exc:
            logger.warning("role_ops_rescore_failed app=%s error=%s", a.id, exc)
            continue
        a.fit_score = res.fit_score
        expl = dict(a.match_explanation or {})
        expl.update({"reasons": res.reasons, "gaps": res.gaps, "breakdown": res.breakdown})
        a.match_explanation = expl
        n += 1
    return n


# ── Careers-site reference ─────────────────────────────────────────────
def set_external_ref(db: Session, agency_id: int, role_id: int, ref: str | None) -> tuple[M.Role, str | None]:
    role = _role(db, agency_id, role_id)
    ref = (ref or "").strip() or None
    if ref is not None:
        if not _REF_RE.match(ref):
            raise RoleOpsError("Reference may contain letters, digits and . _ : - (max 128).", 422)
        clash = (
            db.query(M.Role)
            .filter(M.Role.agency_id == agency_id, M.Role.external_ref == ref, M.Role.id != role.id)
            .first()
        )
        if clash is not None:
            raise RoleOpsError(f"'{ref}' is already linked to role \"{clash.title}\". Unlink or merge it first.")
    previous = role.external_ref
    role.external_ref = ref
    return role, previous
