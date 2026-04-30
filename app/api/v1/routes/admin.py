"""
Admin API routes — all protected by require_admin dependency.

Endpoints:
  GET  /admin/users              — paginated list with usage summary
  GET  /admin/users/{id}         — full user detail
  PATCH /admin/users/{id}        — update role/plan/budget/status
  POST /admin/users/{id}/features — grant or revoke a feature
  GET  /admin/usage              — platform-wide usage stats
  POST /admin/users/{id}/activate   — re-enable a disabled user
  POST /admin/users/{id}/deactivate — disable a user
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.enums import FeatureFlag, PlanTier
from app.models.models import (
    PLAN_DEFAULT_FEATURES, PLAN_TOKEN_BUDGETS,
    UsageEvent, UsageLedger, User, UserFeature,
)
from app.schemas.admin import (
    AdminUserOut,
    AdminUserUpdate,
    FeatureToggleRequest,
    PlatformUsageStats,
    UserUsageSummary,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _current_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def _build_admin_user(user: User, db: Session, include_usage: bool = True) -> AdminUserOut:
    """Serialize a User to AdminUserOut with features and optional usage summary."""
    features = [
        f.feature.value
        for f in db.query(UserFeature).filter_by(user_id=user.id, enabled=True).all()
    ]
    usage = None
    if include_usage:
        month = _current_month()
        ledger = db.query(UsageLedger).filter_by(user_id=user.id, month_year=month).first()
        if ledger:
            usage = UserUsageSummary(
                month_year=ledger.month_year,
                tokens_used=ledger.tokens_used,
                api_calls=ledger.api_calls,
            )
    return AdminUserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        plan=user.plan,
        subscription_status=user.subscription_status,
        token_budget_monthly=user.token_budget_monthly,
        is_active=user.is_active,
        created_at=user.created_at,
        features=features,
        usage_current_month=usage,
    )


# ── User Management ────────────────────────────────────────────────────────

@router.get("/users", response_model=List[AdminUserOut])
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    plan: Optional[PlanTier] = Query(default=None),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all users with pagination, search, and plan filter."""
    q = db.query(User)
    if search:
        q = q.filter(User.email.ilike(f"%{search}%"))
    if plan:
        q = q.filter_by(plan=plan)
    total = q.count()
    users = q.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return [_build_admin_user(u, db, include_usage=True) for u in users]


@router.get("/users/{user_id}", response_model=AdminUserOut)
def get_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get full user details including features and usage."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _build_admin_user(user, db, include_usage=True)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: int,
    payload: AdminUserUpdate,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update a user's role, plan, token budget, subscription status, or active flag."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.role is not None:
        user.role = payload.role
    if payload.plan is not None:
        user.plan = payload.plan
        # Auto-update token budget when plan changes (unless overridden explicitly)
        if payload.token_budget_monthly is None:
            user.token_budget_monthly = PLAN_TOKEN_BUDGETS[payload.plan]
        # Auto-grant default features for the new plan
        new_features = PLAN_DEFAULT_FEATURES[payload.plan]
        for feature in new_features:
            existing = db.query(UserFeature).filter_by(user_id=user.id, feature=feature).first()
            if not existing:
                db.add(UserFeature(user_id=user.id, feature=feature, enabled=True))
            elif not existing.enabled:
                existing.enabled = True
    if payload.token_budget_monthly is not None:
        user.token_budget_monthly = payload.token_budget_monthly
    if payload.subscription_status is not None:
        user.subscription_status = payload.subscription_status
    if payload.is_active is not None:
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)
    return _build_admin_user(user, db)


@router.post("/users/{user_id}/features", response_model=AdminUserOut)
def toggle_feature(
    user_id: int,
    payload: FeatureToggleRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Enable or disable a specific feature for a user."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing = db.query(UserFeature).filter_by(user_id=user_id, feature=payload.feature).first()
    if existing:
        existing.enabled = payload.enabled
    else:
        db.add(UserFeature(user_id=user_id, feature=payload.feature, enabled=payload.enabled))
    db.commit()
    return _build_admin_user(user, db)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Permanently delete a user and all associated data."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role.value == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete an admin account")
    db.delete(user)
    db.commit()


@router.post("/users/{user_id}/activate", status_code=status.HTTP_200_OK)
def activate_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = True
    db.commit()
    return {"message": "User activated"}


@router.post("/users/{user_id}/deactivate", status_code=status.HTTP_200_OK)
def deactivate_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.commit()
    return {"message": "User deactivated"}


# ── Platform Usage Stats ───────────────────────────────────────────────────

@router.get("/usage", response_model=PlatformUsageStats)
def platform_usage(
    month: Optional[str] = Query(default=None, description="Month in YYYY-MM format"),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Platform-wide usage statistics for the given month (defaults to current month)."""
    month_str = month or _current_month()

    total_users = db.query(func.count(User.id)).scalar()

    # Active users = users who made at least one API call this month
    active_users = (
        db.query(func.count(func.distinct(UsageLedger.user_id)))
        .filter_by(month_year=month_str)
        .scalar()
    )

    totals = (
        db.query(
            func.coalesce(func.sum(UsageLedger.tokens_used), 0),
            func.coalesce(func.sum(UsageLedger.api_calls), 0),
        )
        .filter_by(month_year=month_str)
        .first()
    )
    total_tokens = int(totals[0]) if totals else 0
    total_calls = int(totals[1]) if totals else 0

    # Tokens broken down by feature
    feature_rows = (
        db.query(
            UsageEvent.feature,
            func.coalesce(func.sum(UsageEvent.tokens_in + UsageEvent.tokens_out), 0).label("tokens"),
        )
        .filter(func.date_format(UsageEvent.created_at, "%Y-%m") == month_str)
        .group_by(UsageEvent.feature)
        .all()
    )
    tokens_by_feature = {
        (row.feature.value if row.feature else "unknown"): int(row.tokens)
        for row in feature_rows
    }

    # Top 10 users by tokens this month
    top_user_rows = (
        db.query(UsageLedger.user_id, UsageLedger.tokens_used, UsageLedger.api_calls, User.email)
        .join(User, User.id == UsageLedger.user_id)
        .filter(UsageLedger.month_year == month_str)
        .order_by(UsageLedger.tokens_used.desc())
        .limit(10)
        .all()
    )
    top_users = [
        {"user_id": r.user_id, "email": r.email, "tokens_used": r.tokens_used, "api_calls": r.api_calls}
        for r in top_user_rows
    ]

    return PlatformUsageStats(
        total_users=total_users,
        active_users_this_month=active_users,
        total_tokens_this_month=total_tokens,
        total_api_calls_this_month=total_calls,
        tokens_by_feature=tokens_by_feature,
        top_users=top_users,
    )
