"""
Admin API routes — all protected by require_admin dependency.

Endpoints:
  GET  /admin/users              — paginated list with usage summary
  GET  /admin/users/{id}         — full user detail
  PATCH /admin/users/{id}        — update role/plan/budget/status
  POST /admin/users/{id}/features — grant or revoke a feature
  GET  /admin/usage              — platform-wide usage stats
  (overview / costs / quality / spot-check live in admin_insights.py)
  GET  /admin/users/{id}/insights   — job search, documents, AI usage + cost for one user
  POST /admin/users/{id}/usage/reset-daily — clear the rolling 24h usage window
  POST /admin/users/{id}/activate   — re-enable a disabled user
  POST /admin/users/{id}/deactivate — disable a user
  GET  /admin/crawled-jobs/cleanup/preview — count jobs older than N hours
  POST /admin/crawled-jobs/cleanup         — delete jobs older than N hours
"""
from collections import defaultdict
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.enums import FeatureFlag, PlanTier, UserRole
from app.models.models import (
    PLAN_DEFAULT_FEATURES, PLAN_TOKEN_BUDGETS,
    AuditLog, CrawledJob, CrawlerConfig, InterviewSession, JobApplication, ProductEvent,
    RefreshToken, SupportTicket, SupportTicketMessage, UsageEvent, UsageLedger, User, UserFeature, UserProfile,
)
from app.schemas.admin import (
    AdminUserDetailOut,
    AdminUserOut,
    AdminUserUpdate,
    AnalyticsOverview,
    AuditLogOut,
    CrawlerConfigOut,
    CrawledJobsCleanupPreview,
    CrawledJobsCleanupRequest,
    CrawledJobsCleanupResult,
    FeatureBulkRequest,
    FeatureBulkResult,
    FeatureFlagsOverview,
    FeatureFlagSummary,
    FeatureOverrideOut,
    FeatureToggleRequest,
    FunnelStep,
    PlatformUsageStats,
    ProductEventOut,
    UserUsageSummary,
)
from app.schemas.plans import PlanCreate, PlanOut, PlanUpdate
from app.schemas.support import AdminTicketUpdate, TicketListItem, TicketMessageCreate, TicketMessageOut, TicketOut
from app.services.admin.audit import diff, log_admin_action
from app.services.admin.user_insights import user_insights
from app.services.usage.quota import QuotaService, daily_limit_for
from app.services.audit.service import AuditService
from app.services.crawler.cleanup import CrawledJobsCleanupService
from app.services.plans.service import PlanService
from app.services.support.service import SupportService

router = APIRouter(prefix="/admin", tags=["admin"])


def _current_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def _month_bounds(month_str: str) -> tuple[datetime, datetime]:
    """[start, end) datetimes for a YYYY-MM string — portable across MySQL/SQLite."""
    year, month = (int(x) for x in month_str.split("-"))
    start = datetime(year, month, 1)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end


def _build_admin_users(users: List[User], db: Session, include_usage: bool = True) -> List[AdminUserOut]:
    """Serialize many users with a fixed number of queries (no N+1)."""
    if not users:
        return []
    ids = [u.id for u in users]
    month = _current_month()

    features: dict[int, list[str]] = defaultdict(list)
    for uid, feat in (
        db.query(UserFeature.user_id, UserFeature.feature)
        .filter(UserFeature.user_id.in_(ids), UserFeature.enabled.is_(True))
        .all()
    ):
        features[uid].append(feat.value)

    ledgers: dict[int, UsageLedger] = {}
    if include_usage:
        ledgers = {
            row.user_id: row
            for row in db.query(UsageLedger)
            .filter(UsageLedger.user_id.in_(ids), UsageLedger.month_year == month)
            .all()
        }

    # "Last seen" = most recent token issue (login or refresh). Refresh runs
    # every ~30 min of active use, so this tracks real activity closely.
    last_seen = dict(
        db.query(RefreshToken.user_id, func.max(RefreshToken.created_at))
        .filter(RefreshToken.user_id.in_(ids))
        .group_by(RefreshToken.user_id)
        .all()
    )
    names = dict(
        db.query(UserProfile.user_id, UserProfile.full_name)
        .filter(UserProfile.user_id.in_(ids))
        .all()
    )

    out: List[AdminUserOut] = []
    for user in users:
        ledger = ledgers.get(user.id)
        usage = (
            UserUsageSummary(month_year=ledger.month_year, tokens_used=ledger.tokens_used, api_calls=ledger.api_calls)
            if ledger
            else None
        )
        out.append(
            AdminUserOut(
                id=user.id,
                email=user.email,
                full_name=names.get(user.id),
                role=user.role,
                plan=user.plan,
                subscription_status=user.subscription_status,
                token_budget_monthly=user.token_budget_monthly,
                token_budget_daily=daily_limit_for(user),
                daily_limit_is_custom=user.token_budget_daily is not None,
                is_active=user.is_active,
                created_at=user.created_at,
                last_seen_at=last_seen.get(user.id),
                features=sorted(features.get(user.id, [])),
                usage_current_month=usage,
            )
        )
    return out


def _build_admin_user(user: User, db: Session, include_usage: bool = True) -> AdminUserOut:
    return _build_admin_users([user], db, include_usage=include_usage)[0]


def _user_snapshot(user: User) -> dict:
    return {
        "role": getattr(user.role, "value", user.role),
        "plan": getattr(user.plan, "value", user.plan),
        "token_budget_monthly": user.token_budget_monthly,
        "token_budget_daily": user.token_budget_daily,
        "subscription_status": getattr(user.subscription_status, "value", user.subscription_status),
        "is_active": user.is_active,
    }


# ── User Management ────────────────────────────────────────────────────────

USER_SORTS = ("newest", "oldest", "email", "tokens", "last_seen")


@router.get("/users", response_model=List[AdminUserOut])
def list_users(
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None, description="Matches email, full name, or exact user id"),
    plan: Optional[PlanTier] = Query(default=None),
    role: Optional[UserRole] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status", pattern="^(active|disabled)$"),
    sort: str = Query(default="newest", pattern="^(newest|oldest|email|tokens|last_seen)$"),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List users with pagination, search, filters and sorting.

    The total row count (before pagination) is returned in the ``X-Total-Count``
    response header so the body shape stays a plain list.
    """
    month = _current_month()
    q = db.query(User)
    if search and search.strip():
        term = search.strip()
        like = f"%{term}%"
        conds = [User.email.ilike(like), UserProfile.full_name.ilike(like)]
        if term.lstrip("#").isdigit():
            conds.append(User.id == int(term.lstrip("#")))
        q = q.outerjoin(UserProfile, UserProfile.user_id == User.id).filter(or_(*conds))
    if plan:
        q = q.filter(User.plan == plan)
    if role:
        q = q.filter(User.role == role)
    if status_filter == "active":
        q = q.filter(User.is_active.is_(True))
    elif status_filter == "disabled":
        q = q.filter(User.is_active.is_(False))

    total = q.count()

    if sort == "oldest":
        q = q.order_by(User.created_at.asc(), User.id.asc())
    elif sort == "email":
        q = q.order_by(User.email.asc())
    elif sort == "tokens":
        q = q.outerjoin(
            UsageLedger, and_(UsageLedger.user_id == User.id, UsageLedger.month_year == month)
        ).order_by(func.coalesce(UsageLedger.tokens_used, 0).desc(), User.id.desc())
    elif sort == "last_seen":
        seen = (
            db.query(RefreshToken.user_id.label("uid"), func.max(RefreshToken.created_at).label("seen"))
            .group_by(RefreshToken.user_id)
            .subquery()
        )
        q = q.outerjoin(seen, seen.c.uid == User.id).order_by(
            func.coalesce(seen.c.seen, datetime(1970, 1, 1)).desc(), User.id.desc()
        )
    else:
        q = q.order_by(User.created_at.desc(), User.id.desc())

    users = q.offset((page - 1) * page_size).limit(page_size).all()
    response.headers["X-Total-Count"] = str(total)
    return _build_admin_users(users, db, include_usage=True)


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


@router.get("/users/{user_id}/detail", response_model=AdminUserDetailOut)
def get_user_detail(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Aggregated cross-entity detail for a single user (activity, usage, history)."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    base = _build_admin_user(user, db, include_usage=True)

    profile = db.query(UserProfile).filter_by(user_id=user_id).first()
    onboarding_completed = bool(profile and profile.onboarding_completed)

    applications_count = (
        db.query(func.count(JobApplication.id))
        .filter(JobApplication.user_id == user_id, JobApplication.deleted_at.is_(None))
        .scalar() or 0
    )
    packages_generated_total = (
        db.query(func.coalesce(func.sum(UsageLedger.packages_used), 0))
        .filter_by(user_id=user_id)
        .scalar() or 0
    )
    interview_sessions_count = (
        db.query(func.count(InterviewSession.id))
        .filter(InterviewSession.user_id == user_id, InterviewSession.deleted_at.is_(None))
        .scalar() or 0
    )
    support_tickets_count = (
        db.query(func.count(SupportTicket.id))
        .filter(SupportTicket.user_id == user_id, SupportTicket.deleted_at.is_(None))
        .scalar() or 0
    )

    recent_events = [
        ProductEventOut.model_validate(e)
        for e in db.query(ProductEvent)
        .filter(ProductEvent.user_id == user_id)
        .order_by(ProductEvent.created_at.desc())
        .limit(10)
        .all()
    ]
    for e in recent_events:
        e.user_email = user.email

    recent_audit_logs = [
        AuditLogOut.model_validate(a)
        for a in db.query(AuditLog)
        .filter(
            (AuditLog.actor_user_id == user_id)
            | ((AuditLog.entity_type == "user") & (AuditLog.entity_id == user_id))
        )
        .order_by(AuditLog.created_at.desc())
        .limit(10)
        .all()
    ]
    for a in recent_audit_logs:
        if a.actor_user_id == user_id:
            a.actor_user_email = user.email

    return AdminUserDetailOut(
        **base.model_dump(),
        onboarding_completed=onboarding_completed,
        applications_count=applications_count,
        packages_generated_total=int(packages_generated_total),
        interview_sessions_count=interview_sessions_count,
        support_tickets_count=support_tickets_count,
        recent_events=recent_events,
        recent_audit_logs=recent_audit_logs,
    )


@router.get("/users/{user_id}/insights")
def get_user_insights(
    user_id: int,
    days: int = Query(30, ge=7, le=90),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Job search (roles, sites, companies, funnel), documents, and AI usage/cost for one user."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user_insights(db, user, days=days)


@router.post("/users/{user_id}/usage/reset-daily")
def reset_user_daily_usage(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Start a fresh 24h window: usage before now no longer counts toward the daily limit.
    Monthly usage and the cost history are untouched."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    svc = QuotaService(db)
    before = svc.daily(user)
    reset_at = svc.reset_daily(user)
    log_admin_action(
        db, admin, "admin.user.usage_reset_daily", "user", user.id,
        before={"daily_used": before.used, "daily_limit": before.limit},
        after={"daily_used": 0, "reset_at": reset_at.isoformat()},
        metadata={"email": user.email},
        request=request,
    )
    return svc.status(user)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: int,
    payload: AdminUserUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update a user's role, plan, token budget, subscription status, or active flag."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        if payload.role is not None and payload.role != UserRole.admin:
            raise HTTPException(status_code=400, detail="You can't remove your own admin role")
        if payload.is_active is False:
            raise HTTPException(status_code=400, detail="You can't deactivate your own account")

    before = _user_snapshot(user)
    granted: list[str] = []

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
                granted.append(feature.value)
            elif not existing.enabled:
                existing.enabled = True
                granted.append(feature.value)
    if payload.token_budget_monthly is not None:
        user.token_budget_monthly = payload.token_budget_monthly
    if "token_budget_daily" in payload.model_fields_set:
        user.token_budget_daily = payload.token_budget_daily  # None → plan default
    if payload.subscription_status is not None:
        user.subscription_status = payload.subscription_status
    if payload.is_active is not None:
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)

    b, a = diff(before, _user_snapshot(user))
    if b or a or granted:
        log_admin_action(
            db, admin, "admin.user.update", "user", user.id,
            before=b, after=a,
            metadata={"email": user.email, **({"features_granted": granted} if granted else {})},
            request=request,
        )
    return _build_admin_user(user, db)


@router.post("/users/{user_id}/features", response_model=AdminUserOut)
def toggle_feature(
    user_id: int,
    payload: FeatureToggleRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Enable or disable a specific feature for a user."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing = db.query(UserFeature).filter_by(user_id=user_id, feature=payload.feature).first()
    previous = existing.enabled if existing else None
    if existing:
        existing.enabled = payload.enabled
    else:
        db.add(UserFeature(user_id=user_id, feature=payload.feature, enabled=payload.enabled))
    db.commit()
    if previous != payload.enabled:
        log_admin_action(
            db, admin, "admin.user.feature_toggle", "user", user.id,
            before={"feature": payload.feature.value, "enabled": previous},
            after={"feature": payload.feature.value, "enabled": payload.enabled},
            metadata={"email": user.email},
            request=request,
        )
    return _build_admin_user(user, db)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Permanently delete a user and all associated data."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role.value == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete an admin account")
    snapshot = {"email": user.email, **_user_snapshot(user)}
    db.delete(user)
    db.commit()
    log_admin_action(db, admin, "admin.user.delete", "user", user_id, before=snapshot, request=request)


def _set_active(user_id: int, active: bool, request: Request, admin: User, db: Session) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not active and user.id == admin.id:
        raise HTTPException(status_code=400, detail="You can't deactivate your own account")
    was = user.is_active
    user.is_active = active
    if not active:
        # Deactivated users must stop auto-applying immediately. Turn the
        # setting off (not just paused) so reactivation doesn't silently
        # resume it — the user has to opt back in.
        from app.models.auto_apply import AutoApplySettings

        aas = db.query(AutoApplySettings).filter(AutoApplySettings.user_id == user.id).first()
        if aas is not None:
            aas.is_active = False
            aas.paused_at = datetime.utcnow()
    db.commit()
    if was != active:
        log_admin_action(
            db, admin, "admin.user.activate" if active else "admin.user.deactivate", "user", user.id,
            before={"is_active": was}, after={"is_active": active},
            metadata={"email": user.email}, request=request,
        )
    return {"message": "User activated" if active else "User deactivated"}


@router.post("/users/{user_id}/activate", status_code=status.HTTP_200_OK)
def activate_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _set_active(user_id, True, request, admin, db)


@router.post("/users/{user_id}/deactivate", status_code=status.HTTP_200_OK)
def deactivate_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _set_active(user_id, False, request, admin, db)


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

    m_start, m_end = _month_bounds(month_str)
    new_users_this_month = (
        db.query(func.count(User.id)).filter(User.created_at >= m_start, User.created_at < m_end).scalar() or 0
    )

    plan_count_rows = db.query(User.plan, func.count(User.id)).group_by(User.plan).all()
    plan_counts = {p.value: c for p, c in plan_count_rows}

    total_packages_this_month = (
        db.query(func.coalesce(func.sum(UsageLedger.packages_used), 0))
        .filter_by(month_year=month_str)
        .scalar() or 0
    )

    open_support_tickets = (
        db.query(func.count(SupportTicket.id))
        .filter(SupportTicket.status != "closed", SupportTicket.deleted_at.is_(None))
        .scalar() or 0
    )

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
        .filter(UsageEvent.created_at >= _month_bounds(month_str)[0], UsageEvent.created_at < _month_bounds(month_str)[1])
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
        new_users_this_month=new_users_this_month,
        total_tokens_this_month=total_tokens,
        total_api_calls_this_month=total_calls,
        total_packages_this_month=int(total_packages_this_month),
        open_support_tickets=open_support_tickets,
        plan_counts=plan_counts,
        tokens_by_feature=tokens_by_feature,
        top_users=top_users,
    )


# ── Analytics Overview ──────────────────────────────────────────────────────

@router.get("/analytics/overview", response_model=AnalyticsOverview)
def admin_analytics_overview(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Conversion funnel and aggregate analytics derived from product_events."""
    total_users = db.query(func.count(User.id)).scalar() or 0

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    new_users_this_month = db.query(func.count(User.id)).filter(User.created_at >= month_start).scalar() or 0

    plan_count_rows = db.query(User.plan, func.count(User.id)).group_by(User.plan).all()
    plan_counts = {p.value: c for p, c in plan_count_rows}

    onboarded = (
        db.query(func.count(UserProfile.id))
        .filter(UserProfile.onboarding_completed.is_(True))
        .scalar() or 0
    )
    onboarding_completion_rate = round((onboarded / total_users * 100), 1) if total_users else 0.0

    paid_users = db.query(func.count(User.id)).filter(User.plan != PlanTier.free).scalar() or 0
    free_to_paid_conversion_rate = round((paid_users / total_users * 100), 1) if total_users else 0.0

    funnel_defs = [
        ("Website page views", "page_view"),
        ("Registrations", "registration_completed"),
        ("Onboarding completed", "onboarding_completed"),
        ("First package generated", "package_generated"),
    ]
    funnel: List[FunnelStep] = []
    for label, event_name in funnel_defs:
        if event_name == "page_view":
            count = db.query(func.count(ProductEvent.id)).filter(ProductEvent.event_name == event_name).scalar() or 0
        else:
            count = (
                db.query(func.count(func.distinct(ProductEvent.user_id)))
                .filter(ProductEvent.event_name == event_name, ProductEvent.user_id.isnot(None))
                .scalar() or 0
            )
        funnel.append(FunnelStep(label=label, event_name=event_name, count=count))

    top_event_rows = (
        db.query(ProductEvent.event_name, func.count(ProductEvent.id).label("cnt"))
        .group_by(ProductEvent.event_name)
        .order_by(func.count(ProductEvent.id).desc())
        .limit(10)
        .all()
    )
    top_events = [{"event_name": e, "count": c} for e, c in top_event_rows]

    month_str = _current_month()
    packages_this_month = (
        db.query(func.coalesce(func.sum(UsageLedger.packages_used), 0))
        .filter_by(month_year=month_str)
        .scalar() or 0
    )
    open_tickets = (
        db.query(func.count(SupportTicket.id))
        .filter(SupportTicket.status != "closed", SupportTicket.deleted_at.is_(None))
        .scalar() or 0
    )

    return AnalyticsOverview(
        total_users=total_users,
        new_users_this_month=new_users_this_month,
        plan_counts=plan_counts,
        onboarding_completion_rate=onboarding_completion_rate,
        free_to_paid_conversion_rate=free_to_paid_conversion_rate,
        funnel=funnel,
        top_events=top_events,
        packages_generated_this_month=int(packages_this_month),
        open_support_tickets=open_tickets,
    )


# ── Product Events ───────────────────────────────────────────────────────────

@router.get("/events", response_model=List[ProductEventOut])
def admin_list_events(
    response: Response,
    event_name: Optional[str] = Query(default=None),
    user_id: Optional[int] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    start_date: Optional[datetime] = Query(default=None),
    end_date: Optional[datetime] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Filterable list of product/website analytics events."""
    q = db.query(ProductEvent)
    if event_name:
        q = q.filter(ProductEvent.event_name == event_name)
    if user_id is not None:
        q = q.filter(ProductEvent.user_id == user_id)
    if entity_type:
        q = q.filter(ProductEvent.entity_type == entity_type)
    if start_date:
        q = q.filter(ProductEvent.created_at >= start_date)
    if end_date:
        q = q.filter(ProductEvent.created_at <= end_date)

    response.headers["X-Total-Count"] = str(q.count())
    rows = q.order_by(ProductEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    user_ids = {r.user_id for r in rows if r.user_id is not None}
    emails: dict = {}
    if user_ids:
        for uid, email in db.query(User.id, User.email).filter(User.id.in_(user_ids)).all():
            emails[uid] = email

    results = []
    for r in rows:
        out = ProductEventOut.model_validate(r)
        out.user_email = emails.get(r.user_id) if r.user_id is not None else None
        results.append(out)
    return results


@router.get("/events/facets")
def admin_event_facets(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Distinct event names and entity types, for filter dropdowns."""
    names = [r[0] for r in db.query(ProductEvent.event_name).distinct().order_by(ProductEvent.event_name).all() if r[0]]
    entities = [r[0] for r in db.query(ProductEvent.entity_type).distinct().order_by(ProductEvent.entity_type).all() if r[0]]
    return {"event_names": names, "entity_types": entities}


@router.get("/audit-logs/facets")
def admin_audit_facets(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Distinct actions and entity types, for filter dropdowns."""
    actions = [r[0] for r in db.query(AuditLog.action).distinct().order_by(AuditLog.action).all() if r[0]]
    entities = [r[0] for r in db.query(AuditLog.entity_type).distinct().order_by(AuditLog.entity_type).all() if r[0]]
    return {"actions": actions, "entity_types": entities}


# ── Audit Logs ───────────────────────────────────────────────────────────────

@router.get("/audit-logs", response_model=List[AuditLogOut])
def admin_list_audit_logs(
    response: Response,
    actor_user_id: Optional[int] = Query(default=None),
    action: Optional[str] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    entity_id: Optional[int] = Query(default=None),
    start_date: Optional[datetime] = Query(default=None),
    end_date: Optional[datetime] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Filterable list of append-only audit log entries."""
    q = db.query(AuditLog)
    if actor_user_id is not None:
        q = q.filter(AuditLog.actor_user_id == actor_user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        q = q.filter(AuditLog.entity_id == entity_id)
    if start_date:
        q = q.filter(AuditLog.created_at >= start_date)
    if end_date:
        q = q.filter(AuditLog.created_at <= end_date)

    response.headers["X-Total-Count"] = str(q.count())
    rows = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id is not None}
    emails: dict = {}
    if actor_ids:
        for uid, email in db.query(User.id, User.email).filter(User.id.in_(actor_ids)).all():
            emails[uid] = email

    results = []
    for r in rows:
        out = AuditLogOut.model_validate(r)
        out.actor_user_email = emails.get(r.actor_user_id) if r.actor_user_id is not None else None
        results.append(out)
    return results


# ── Feature Flag Overrides ────────────────────────────────────────────────────

@router.get("/feature-flags", response_model=FeatureFlagsOverview)
def admin_feature_flags(
    feature: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, description="Filter overrides by user email"),
    enabled: Optional[bool] = Query(default=None),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-feature override counts plus a list of individual user overrides."""
    summary_rows = (
        db.query(UserFeature.feature, UserFeature.enabled, func.count(UserFeature.id))
        .group_by(UserFeature.feature, UserFeature.enabled)
        .all()
    )
    counts: dict = {}
    for feat, enabled, cnt in summary_rows:
        key = feat.value
        bucket = counts.setdefault(key, {"enabled": 0, "disabled": 0})
        bucket["enabled" if enabled else "disabled"] += cnt

    by_plan: dict = defaultdict(dict)
    for feat, plan_, cnt in (
        db.query(UserFeature.feature, User.plan, func.count(UserFeature.id))
        .join(User, User.id == UserFeature.user_id)
        .filter(UserFeature.enabled.is_(True))
        .group_by(UserFeature.feature, User.plan)
        .all()
    ):
        by_plan[feat.value][plan_.value] = cnt

    summary = [
        FeatureFlagSummary(
            feature=f.value,
            enabled_count=counts.get(f.value, {}).get("enabled", 0),
            disabled_count=counts.get(f.value, {}).get("disabled", 0),
            enabled_by_plan=by_plan.get(f.value, {}),
        )
        for f in FeatureFlag
    ]
    plan_users = {p.value: c for p, c in db.query(User.plan, func.count(User.id)).group_by(User.plan).all()}

    q = db.query(UserFeature, User.email).join(User, User.id == UserFeature.user_id)
    if feature:
        q = q.filter(UserFeature.feature == feature)
    if search and search.strip():
        q = q.filter(User.email.ilike(f"%{search.strip()}%"))
    if enabled is not None:
        q = q.filter(UserFeature.enabled.is_(enabled))
    overrides_total = q.count()
    rows = q.order_by(UserFeature.id.desc()).limit(200).all()

    overrides = [
        FeatureOverrideOut(id=uf.id, user_id=uf.user_id, user_email=email, feature=uf.feature.value, enabled=uf.enabled)
        for uf, email in rows
    ]

    return FeatureFlagsOverview(summary=summary, overrides=overrides, plan_users=plan_users, overrides_total=overrides_total)


@router.post("/feature-flags/bulk", response_model=FeatureBulkResult)
def admin_feature_flags_bulk(
    payload: FeatureBulkRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Set one feature on/off for every user on a plan (or a list of user ids).

    Use ``dry_run: true`` first to see how many users would change.
    """
    if payload.plan is None and not payload.user_ids:
        raise HTTPException(status_code=400, detail="Choose a plan or provide user_ids")

    uq = db.query(User.id)
    if payload.plan is not None:
        uq = uq.filter(User.plan == payload.plan)
    if payload.user_ids:
        uq = uq.filter(User.id.in_(payload.user_ids))
    user_ids = [uid for (uid,) in uq.all()]

    existing = {
        uf.user_id: uf
        for uf in db.query(UserFeature)
        .filter(UserFeature.feature == payload.feature, UserFeature.user_id.in_(user_ids))
        .all()
    } if user_ids else {}
    to_change = [uid for uid in user_ids if (existing[uid].enabled if uid in existing else None) != payload.enabled]

    if not payload.dry_run and to_change:
        for uid in to_change:
            if uid in existing:
                existing[uid].enabled = payload.enabled
            else:
                db.add(UserFeature(user_id=uid, feature=payload.feature, enabled=payload.enabled))
        db.commit()
        log_admin_action(
            db, admin, "admin.feature.bulk_set", "feature", None,
            after={"feature": payload.feature.value, "enabled": payload.enabled},
            metadata={
                "plan": payload.plan.value if payload.plan else None,
                "user_ids": payload.user_ids[:50] if payload.user_ids else None,
                "matched_users": len(user_ids),
                "changed": len(to_change),
            },
            request=request,
        )

    return FeatureBulkResult(
        feature=payload.feature.value,
        enabled=payload.enabled,
        matched_users=len(user_ids),
        changed=len(to_change),
        dry_run=payload.dry_run,
    )


# ── Crawler Configs ────────────────────────────────────────────────────────────

@router.get("/crawler-configs", response_model=List[CrawlerConfigOut])
def admin_list_crawler_configs(
    enabled_only: bool = Query(default=False),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-user job discovery crawler configurations."""
    q = db.query(CrawlerConfig, User.email).join(User, User.id == CrawlerConfig.user_id)
    if enabled_only:
        q = q.filter(CrawlerConfig.is_enabled.is_(True))
    rows = q.order_by(CrawlerConfig.updated_at.desc()).limit(200).all()

    configs = []
    for cfg, email in rows:
        jobs_count = db.query(func.count(CrawledJob.id)).filter(CrawledJob.user_id == cfg.user_id).scalar() or 0
        out = CrawlerConfigOut.model_validate(cfg)
        out.user_email = email
        out.crawled_jobs_count = jobs_count
        configs.append(out)
    return configs


@router.get("/crawled-jobs/cleanup/preview", response_model=CrawledJobsCleanupPreview)
def admin_crawled_jobs_cleanup_preview(
    older_than_hours: int = Query(default=24, ge=1, le=24 * 90),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """How many crawled / discovered jobs are older than the cutoff, and how
    many of those are protected (applied, saved or dismissed) and will be kept."""
    return CrawledJobsCleanupService(db).preview(older_than_hours)


@router.post("/crawled-jobs/cleanup", response_model=CrawledJobsCleanupResult)
def admin_crawled_jobs_cleanup(
    payload: CrawledJobsCleanupRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Hard-delete crawled / discovered jobs older than ``older_than_hours``.
    Jobs linked to an application, or saved / dismissed by a user, are kept."""
    result = CrawledJobsCleanupService(db).purge(
        older_than_hours=payload.older_than_hours,
        include_discovered=payload.include_discovered,
        include_user_crawled=payload.include_user_crawled,
    )
    AuditService(db).log(
        action="admin.crawled_jobs.cleanup",
        entity_type="crawled_jobs",
        actor_user_id=admin.id,
        actor_role=getattr(admin.role, "value", str(admin.role)),
        after=result,
        metadata=payload.model_dump(),
        request=request,
    )
    return result


# ── Plans Management ───────────────────────────────────────────────────────

@router.get("/plans", response_model=List[PlanOut])
def admin_list_plans(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """List all plans (including archived/inactive) for admin management."""
    return [PlanOut.model_validate(p) for p in PlanService(db).list_all()]


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def admin_create_plan(
    payload: PlanCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = PlanService(db).create(payload, actor_user_id=admin.id, request=request)
    return PlanOut.model_validate(plan)


@router.patch("/plans/{plan_id}", response_model=PlanOut)
def admin_update_plan(
    plan_id: int,
    payload: PlanUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = PlanService(db).update(plan_id, payload, actor_user_id=admin.id, request=request)
    return PlanOut.model_validate(plan)


@router.post("/plans/{plan_id}/archive", response_model=PlanOut)
def admin_archive_plan(
    plan_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = PlanService(db).archive(plan_id, actor_user_id=admin.id, request=request)
    return PlanOut.model_validate(plan)


@router.post("/plans/{plan_id}/restore", response_model=PlanOut)
def admin_restore_plan(
    plan_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = PlanService(db).restore(plan_id, actor_user_id=admin.id, request=request)
    return PlanOut.model_validate(plan)


# ── Help Desk ────────────────────────────────────────────────────────────

def _admin_ticket_out(service: SupportService, ticket_id: int) -> TicketOut:
    ticket = service.get_ticket(ticket_id, admin=True)
    messages = service.get_messages(ticket_id)
    return TicketOut(
        id=ticket.id,
        user_id=ticket.user_id,
        subject=ticket.subject,
        category=ticket.category,
        priority=ticket.priority,
        status=ticket.status,
        related_entity_type=ticket.related_entity_type,
        related_entity_id=ticket.related_entity_id,
        assigned_admin_id=ticket.assigned_admin_id,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        closed_at=ticket.closed_at,
        deleted_at=ticket.deleted_at,
        messages=[TicketMessageOut.model_validate(m) for m in messages],
    )


@router.get("/support/tickets", response_model=List[TicketListItem])
def admin_list_tickets(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    priority: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    assigned_admin_id: Optional[int] = Query(default=None),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all support tickets with optional status/priority/category/assignee filters."""
    service = SupportService(db)
    tickets = service.list_all(
        status_filter=status_filter,
        priority=priority,
        category=category,
        assigned_admin_id=assigned_admin_id,
    )
    counts: dict[int, int] = {}
    if tickets:
        counts = dict(
            db.query(SupportTicketMessage.ticket_id, func.count(SupportTicketMessage.id))
            .filter(SupportTicketMessage.ticket_id.in_([t.id for t in tickets]), SupportTicketMessage.deleted_at.is_(None))
            .group_by(SupportTicketMessage.ticket_id)
            .all()
        )
    return [
        TicketListItem(
            id=t.id,
            user_id=t.user_id,
            subject=t.subject,
            category=t.category,
            priority=t.priority,
            status=t.status,
            assigned_admin_id=t.assigned_admin_id,
            message_count=counts.get(t.id, 0),
            created_at=t.created_at,
            updated_at=t.updated_at,
            deleted_at=t.deleted_at,
        )
        for t in tickets
    ]


@router.get("/support/tickets/{ticket_id}", response_model=TicketOut)
def admin_get_ticket(ticket_id: int, _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _admin_ticket_out(SupportService(db), ticket_id)


@router.post("/support/tickets/{ticket_id}/reply", response_model=TicketOut)
def admin_reply_ticket(
    ticket_id: int,
    payload: TicketMessageCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    service = SupportService(db)
    service.add_message(ticket_id, payload.body, admin=True, sender_user_id=admin.id, request=request)
    return _admin_ticket_out(service, ticket_id)


@router.patch("/support/tickets/{ticket_id}", response_model=TicketOut)
def admin_update_ticket(
    ticket_id: int,
    payload: AdminTicketUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    service = SupportService(db)
    service.update_ticket(
        ticket_id,
        new_status=payload.status,
        priority=payload.priority,
        assigned_admin_id=payload.assigned_admin_id,
        actor_user_id=admin.id,
        request=request,
    )
    return _admin_ticket_out(service, ticket_id)


@router.post("/support/tickets/{ticket_id}/archive", response_model=TicketOut)
def admin_archive_ticket(
    ticket_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    service = SupportService(db)
    service.archive(ticket_id, actor_user_id=admin.id, request=request)
    return _admin_ticket_out(service, ticket_id)


@router.post("/support/tickets/{ticket_id}/restore", response_model=TicketOut)
def admin_restore_ticket(
    ticket_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    service = SupportService(db)
    service.restore_ticket(ticket_id, actor_user_id=admin.id, request=request)
    return _admin_ticket_out(service, ticket_id)
