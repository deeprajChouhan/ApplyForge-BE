"""Schemas for first-party product & website analytics events."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AnalyticsEventIn(BaseModel):
    event_name: str
    properties: dict[str, Any] | None = None
    referrer: str | None = None
