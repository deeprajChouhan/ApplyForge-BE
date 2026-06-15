"""ProductEventService — first-party product & website analytics.

A single `product_events` table backs both in-app product analytics
(e.g. "package_generated") and anonymous website analytics
(e.g. "page_view", "cta_clicked") — distinguished by `event_name` and
whether `user_id` is set.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.models import ProductEvent, User

logger = logging.getLogger(__name__)


class ProductEventService:
    def __init__(self, db: Session):
        self.db = db

    def track(
        self,
        event_name: str,
        user: Optional[User] = None,
        user_id: int | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
        properties: Optional[dict] = None,
        request: Request | None = None,
        referrer: str | None = None,
    ) -> ProductEvent:
        ip_address = None
        user_agent = None
        if request is not None:
            client = request.client
            ip_address = client.host if client else None
            user_agent = request.headers.get("user-agent")
            if referrer is None:
                referrer = request.headers.get("referer")

        try:
            props_json = json.dumps(properties, default=str) if properties is not None else None
        except (TypeError, ValueError):
            props_json = None

        event = ProductEvent(
            user_id=user.id if user else user_id,
            event_name=event_name,
            entity_type=entity_type,
            entity_id=entity_id,
            properties=props_json,
            ip_address=ip_address,
            user_agent=user_agent,
            referrer=referrer,
        )
        try:
            self.db.add(event)
            self.db.commit()
            self.db.refresh(event)
        except Exception as exc:
            logger.warning("Failed to track product event (%s): %s", event_name, exc)
            self.db.rollback()
        return event
