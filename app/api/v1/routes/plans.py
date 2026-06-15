"""
Public pricing plans API.

  GET /plans -> list active, publicly-visible pricing plans (ordered by sort_order)
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.plans import PlanOut
from app.services.plans.service import PlanService

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db)):
    return [PlanOut.model_validate(p) for p in PlanService(db).list_public_active()]
