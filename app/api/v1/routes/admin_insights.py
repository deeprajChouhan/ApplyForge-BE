"""
Operator console — overview, cost and quality views (consumer product).

  GET  /admin/overview              — MTD KPIs vs same point last month, 30-day trend, needs-attention list
  GET  /admin/costs?month=YYYY-MM   — estimated LLM spend by model / feature / user / day
  GET  /admin/quality?days=30       — hallucination-flag rate, parse confidence, outcomes
  POST /admin/quality/spot-check    — run the regex hallucination checker over recent generated docs

Recruiter data is never read here (data wall); the admin UI merges recruiter
attention items from /recruiter/admin/* endpoints client-side.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.models import User
from app.schemas.admin_insights import (
    AdminCostsOut,
    AdminOverviewOut,
    AdminQualityOut,
    SpotCheckRequest,
    SpotCheckResult,
)
from app.services.admin.audit import log_admin_action
from app.services.admin.costs import cost_breakdown, month_bounds, previous_month, total_cost
from app.services.admin.overview import build_overview
from app.services.admin.quality import quality_summary, run_spot_check

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview", response_model=AdminOverviewOut)
def admin_overview(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return build_overview(db)


@router.get("/costs", response_model=AdminCostsOut)
def admin_costs(
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM; default current month"),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()
    month = month or now.strftime("%Y-%m")
    start, end = month_bounds(month)
    is_current = month == now.strftime("%Y-%m")
    data = cost_breakdown(db, start, min(end, now + timedelta(seconds=1)) if is_current else end)

    prev_start, prev_end = month_bounds(previous_month(month))
    prev_total = round(total_cost(db, prev_start, prev_end).cost_usd, 4)

    projected = None
    if is_current:
        elapsed_days = max((now - start).total_seconds() / 86400, 1.0)
        month_days = (end - start).days
        projected = round(data["total"]["cost_usd"] / elapsed_days * month_days, 2)

    return AdminCostsOut(
        month=month,
        is_current_month=is_current,
        previous_month_total_usd=prev_total,
        projected_month_end_usd=projected,
        **data,
    )


@router.get("/quality", response_model=AdminQualityOut)
def admin_quality(
    days: int = Query(default=30, ge=1, le=365),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return quality_summary(db, days=days)


@router.post("/quality/spot-check", response_model=SpotCheckResult)
def admin_quality_spot_check(
    payload: SpotCheckRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    result = run_spot_check(db, sample_size=payload.sample_size, days=payload.days)
    log_admin_action(
        db, admin, "admin.quality.spot_check", "generated_documents",
        after=result, metadata=payload.model_dump(), request=request,
    )
    return result
