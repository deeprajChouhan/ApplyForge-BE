"""Schemas for admin-managed pricing plans (public + admin CRUD)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator


def _parse_json_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return []
    return v


def _parse_json_dict(v: Any) -> dict[str, Any]:
    if v is None:
        return {}
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return {}
    return v


class PlanOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None = None
    price_monthly: float
    price_yearly: float | None = None
    currency: str
    is_active: bool
    is_public: bool
    sort_order: int
    features: list[str] = []
    limits: dict[str, Any] = {}
    cta_label: str | None = None
    highlighted: bool
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("features", mode="before")
    @classmethod
    def _validate_features(cls, v: Any) -> list[Any]:
        return _parse_json_list(v)

    @field_validator("limits", mode="before")
    @classmethod
    def _validate_limits(cls, v: Any) -> dict[str, Any]:
        return _parse_json_dict(v)


class PlanCreate(BaseModel):
    name: str
    slug: str
    description: str | None = None
    price_monthly: float = 0
    price_yearly: float | None = None
    currency: str = "usd"
    is_active: bool = True
    is_public: bool = True
    sort_order: int = 0
    features: list[str] = []
    limits: dict[str, Any] = {}
    cta_label: str | None = None
    highlighted: bool = False


class PlanUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    price_monthly: float | None = None
    price_yearly: float | None = None
    currency: str | None = None
    is_active: bool | None = None
    is_public: bool | None = None
    sort_order: int | None = None
    features: list[str] | None = None
    limits: dict[str, Any] | None = None
    cta_label: str | None = None
    highlighted: bool | None = None
