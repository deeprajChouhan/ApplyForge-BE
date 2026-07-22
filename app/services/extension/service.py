"""ExtensionService — state + scoring helpers for the ApplyForge Job Clipper."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import UserExtensionState


def frontend_base_url() -> str:
    """Best-effort public web-app origin for building open_url links."""
    for origin in settings.cors_origin_list():
        if origin.startswith("https://"):
            return origin.rstrip("/")
    return "https://applyforge.pro"


def score_confidence(description: str, source_site: str = "") -> str:
    """
    Confidence in a preview score based on how much job detail we have.

    Listing cards (short/absent description) score "low"; a partially-loaded
    detail is "medium"; a full job description is "high".
    """
    length = len((description or "").strip())
    if length >= 400:
        return "high"
    if length >= 120:
        return "medium"
    return "low"


class ExtensionService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def get_state(self) -> UserExtensionState | None:
        return (
            self.db.query(UserExtensionState)
            .filter_by(user_id=self.user_id)
            .first()
        )

    def _get_or_create(self) -> UserExtensionState:
        state = self.get_state()
        if not state:
            state = UserExtensionState(user_id=self.user_id)
            self.db.add(state)
            self.db.flush()
        return state

    def mark_seen(self, *, connect: bool = False) -> UserExtensionState:
        """Record activity from the extension; optionally mark it connected."""
        state = self._get_or_create()
        now = datetime.utcnow()
        state.last_seen_at = now
        if connect and state.connected_at is None:
            state.connected_at = now
        self.db.commit()
        self.db.refresh(state)
        return state

    def dismiss_promo(self) -> UserExtensionState:
        state = self._get_or_create()
        state.promo_dismissed_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(state)
        return state
