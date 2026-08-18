"""Small helper for recording ApplicationEvent rows.

Used by the orchestrator and dispatcher tasks to keep an auditable
timeline of what happened to a JobApplication during auto-apply.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import structlog

from app.models.auto_apply import ApplicationEvent

logger = structlog.get_logger(__name__)


def emit(db: Any, application_id: int, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Create and commit an ApplicationEvent row.

    Safe to call multiple times per request; each call is its own commit
    so an event is durable even if the surrounding transaction later fails.
    """
    try:
        event = ApplicationEvent(
            application_id=application_id,
            event_type=event_type,
            payload_json=payload or {},
        )
        db.add(event)
        db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("auto_apply.emit_event_failed", application_id=application_id, event_type=event_type, error=str(exc))
        db.rollback()
