"""AuditService — append-only log of state-changing actions.

Used by routes/services whenever a user or admin performs an action that
changes persisted state (create/update/delete/status-change/etc). Audit
rows are never updated or deleted by application code.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.models import AuditLog

logger = logging.getLogger(__name__)


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(value))


class AuditService:
    def __init__(self, db: Session):
        self.db = db

    def log(
        self,
        action: str,
        entity_type: str,
        entity_id: int | None = None,
        actor_user_id: int | None = None,
        actor_role: str | None = None,
        before: Any = None,
        after: Any = None,
        metadata: Optional[dict] = None,
        request: Request | None = None,
    ) -> AuditLog:
        ip_address = None
        user_agent = None
        if request is not None:
            client = request.client
            ip_address = client.host if client else None
            user_agent = request.headers.get("user-agent")

        entry = AuditLog(
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=_to_json(before),
            after=_to_json(after),
            extra_metadata=_to_json(metadata),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        try:
            self.db.add(entry)
            self.db.commit()
            self.db.refresh(entry)
        except Exception as exc:
            logger.warning("Failed to write audit log entry (%s): %s", action, exc)
            self.db.rollback()
        return entry
