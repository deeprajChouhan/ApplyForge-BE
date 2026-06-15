"""PlanService — CRUD for admin-managed pricing plans, plus the public listing."""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models.models import Plan
from app.schemas.plans import PlanCreate, PlanUpdate
from app.services.audit.service import AuditService
from app.services.common.soft_delete import not_deleted, restore, soft_delete


def _dump(value: Any) -> str:
    return json.dumps(value)


class PlanService:
    def __init__(self, db: Session):
        self.db = db

    def list_public_active(self) -> list[Plan]:
        return (
            not_deleted(self.db.query(Plan), Plan)
            .filter(Plan.is_active.is_(True), Plan.is_public.is_(True))
            .order_by(Plan.sort_order.asc())
            .all()
        )

    def list_all(self) -> list[Plan]:
        """All plans, including archived ones, for admin management."""
        return self.db.query(Plan).order_by(Plan.sort_order.asc()).all()

    def get(self, plan_id: int) -> Plan:
        plan = (
            self.db.query(Plan)
            .filter(Plan.id == plan_id, Plan.deleted_at.is_(None))
            .first()
        )
        if not plan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
        return plan

    def _check_slug_unique(self, slug: str, exclude_id: int | None = None) -> None:
        q = self.db.query(Plan).filter(Plan.slug == slug)
        if exclude_id is not None:
            q = q.filter(Plan.id != exclude_id)
        if q.first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A plan with slug '{slug}' already exists",
            )

    def create(self, payload: PlanCreate, actor_user_id: int | None = None, request: Request | None = None) -> Plan:
        self._check_slug_unique(payload.slug)
        plan = Plan(
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            price_monthly=payload.price_monthly,
            price_yearly=payload.price_yearly,
            currency=payload.currency,
            is_active=payload.is_active,
            is_public=payload.is_public,
            sort_order=payload.sort_order,
            features=_dump(payload.features),
            limits=_dump(payload.limits),
            cta_label=payload.cta_label,
            highlighted=payload.highlighted,
        )
        self.db.add(plan)
        self.db.commit()
        self.db.refresh(plan)

        AuditService(self.db).log(
            action="admin_plan_created",
            entity_type="plan",
            entity_id=plan.id,
            actor_user_id=actor_user_id,
            actor_role="admin",
            after={"name": plan.name, "slug": plan.slug, "price_monthly": plan.price_monthly},
            request=request,
        )
        return plan

    def update(self, plan_id: int, payload: PlanUpdate, actor_user_id: int | None = None, request: Request | None = None) -> Plan:
        plan = self.get(plan_id)
        before = {"name": plan.name, "price_monthly": plan.price_monthly, "is_active": plan.is_active, "is_public": plan.is_public}

        data = payload.model_dump(exclude_unset=True)
        if "slug" in data and data["slug"] != plan.slug:
            self._check_slug_unique(data["slug"], exclude_id=plan_id)
        for field in ("features", "limits"):
            if field in data:
                data[field] = _dump(data[field])
        for key, value in data.items():
            setattr(plan, key, value)

        self.db.commit()
        self.db.refresh(plan)

        AuditService(self.db).log(
            action="admin_plan_updated",
            entity_type="plan",
            entity_id=plan.id,
            actor_user_id=actor_user_id,
            actor_role="admin",
            before=before,
            after={"name": plan.name, "price_monthly": plan.price_monthly, "is_active": plan.is_active, "is_public": plan.is_public},
            request=request,
        )
        return plan

    def archive(self, plan_id: int, actor_user_id: int | None = None, request: Request | None = None) -> Plan:
        plan = self.get(plan_id)
        soft_delete(self.db, plan)
        AuditService(self.db).log(
            action="admin_plan_archived",
            entity_type="plan",
            entity_id=plan.id,
            actor_user_id=actor_user_id,
            actor_role="admin",
            request=request,
        )
        return plan

    def restore(self, plan_id: int, actor_user_id: int | None = None, request: Request | None = None) -> Plan:
        plan = self.db.query(Plan).filter(Plan.id == plan_id).first()
        if not plan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
        restore(self.db, plan)
        AuditService(self.db).log(
            action="admin_plan_restored",
            entity_type="plan",
            entity_id=plan.id,
            actor_user_id=actor_user_id,
            actor_role="admin",
            request=request,
        )
        return plan
