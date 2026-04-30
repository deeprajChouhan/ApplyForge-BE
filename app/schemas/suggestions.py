"""
Suggestion Engine – Pydantic schemas
=====================================
Defines the shapes returned by POST /applications/{id}/suggestions
and consumed by the frontend suggestion panel.
"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel


SuggestionType = Literal["missing_skill", "bullet_point", "gap"]
SuggestionAction = Literal["add", "update"]
SuggestionSection = Literal["skills", "experiences", "projects"]


class SuggestionOut(BaseModel):
    """A single AI-generated resume improvement suggestion."""

    id: str                        # uuid – stable identifier for the suggestion
    type: SuggestionType           # category of suggestion
    title: str                     # short human-readable label, e.g. "Add skill: Docker"
    reason: str                    # why the AI suggests this (shown as sub-text)

    # ── Where / how to apply ────────────────────────────────────────────────
    section: SuggestionSection     # which profile section to mutate
    action: SuggestionAction       # "add" = create new row; "update" = patch existing row
    payload: dict[str, Any]        # the body to POST/PUT to /profile/{section}[/{id}]

    # Only present when action == "update" (the profile row id to patch)
    target_id: int | None = None
    # Human-readable label of the row being patched, e.g. "Senior Dev @ Acme Corp"
    target_label: str | None = None


class SuggestionsResponse(BaseModel):
    """Envelope returned by the suggestions endpoint."""

    application_id: int
    suggestions: list[SuggestionOut]
