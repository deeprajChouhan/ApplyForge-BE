"""Pydantic schemas for the Job Discovery feed."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class JobFeedItem(BaseModel):
    id: str
    company: str
    role: str
    location: Optional[str] = None
    country: Optional[str] = None
    work_type: Optional[str] = None
    source: str
    source_url: str
    description: Optional[str] = None
    posted_at: Optional[str] = None
    match_score: Optional[float] = None
    is_fallback: bool = False


class JobFeedResponse(BaseModel):
    items: list[JobFeedItem]
    is_fallback: bool
