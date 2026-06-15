"""Shared helpers for soft-deleting and restoring rows.

Soft-deletable models expose a `deleted_at` column (and, on some models,
`deleted_by` / `delete_reason`). Default list/get queries should exclude
soft-deleted rows via `not_deleted()`; admins can still see and restore them.
"""
from __future__ import annotations

from datetime import datetime
from typing import TypeVar

from sqlalchemy import Select
from sqlalchemy.orm import Session

T = TypeVar("T")


def soft_delete(db: Session, obj, deleted_by: int | None = None, reason: str | None = None) -> None:
    """Mark a row as deleted without removing it from the database."""
    obj.deleted_at = datetime.utcnow()
    if hasattr(obj, "deleted_by"):
        obj.deleted_by = deleted_by
    if reason is not None and hasattr(obj, "delete_reason"):
        obj.delete_reason = reason
    db.commit()


def restore(db: Session, obj) -> None:
    """Clear the soft-delete markers on a row."""
    obj.deleted_at = None
    if hasattr(obj, "deleted_by"):
        obj.deleted_by = None
    if hasattr(obj, "delete_reason"):
        obj.delete_reason = None
    db.commit()


def not_deleted(query: Select, model) -> Select:
    """Filter a query/select to exclude soft-deleted rows."""
    return query.filter(model.deleted_at.is_(None))
