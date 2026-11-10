"""Integration audit trail (credentials, webhooks, settings). Never stores secrets."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.recruiter.models import IntegrationAuditEvent


def record(
    db: Session,
    agency_id: int,
    actor: str,
    action: str,
    *,
    target: str | None = None,
    detail: dict | None = None,
) -> None:
    """Append an audit row. Caller commits."""
    db.add(
        IntegrationAuditEvent(
            agency_id=agency_id,
            actor=(actor or "system")[:255],
            action=action[:60],
            target=(target or None) and target[:120],
            detail=detail,
        )
    )
