"""
Current user's AI allowance.

  GET /usage/me — rolling 24h + monthly token use, % of each, and whether AI
                  features are currently blocked. Shown in the app sidebar for
                  every plan (including those with unlimited packages).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import PLAN_PACKAGE_LIMITS, User
from app.schemas.usage import UsageStatusOut
from app.services.usage.quota import QuotaService
from app.services.usage.service import UsageTracker

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/me", response_model=UsageStatusOut)
def my_usage(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    status = QuotaService(db).status(user)
    tracker = UsageTracker(db, user.id, model="")
    return UsageStatusOut(
        plan=user.plan.value,
        packages_used_this_month=tracker.packages_used_this_month(),
        monthly_package_limit=PLAN_PACKAGE_LIMITS.get(user.plan, 0),
        **status,
    )
