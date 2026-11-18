"""One-line audit logging for operator (admin) actions.

Every state-changing admin endpoint calls :func:`log_admin_action` so the
Audit log screen is a complete record of who changed what. Values are stored as
JSON text; ``entity_id`` is a plain integer with no foreign key, so recruiter
entities (rec_*) can be referenced without crossing the data wall.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.models import User
from app.services.audit.service import AuditService


def log_admin_action(
    db: Session,
    admin: User,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    *,
    before: Any = None,
    after: Any = None,
    metadata: Optional[dict] = None,
    request: Request | None = None,
) -> None:
    AuditService(db).log(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=admin.id,
        actor_role=getattr(admin.role, "value", str(admin.role)),
        before=before,
        after=after,
        metadata=metadata,
        request=request,
    )


def diff(before: dict, after: dict) -> tuple[dict, dict]:
    """Return only the keys whose values changed, as (before, after)."""
    changed = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in changed}, {k: after.get(k) for k in changed}
