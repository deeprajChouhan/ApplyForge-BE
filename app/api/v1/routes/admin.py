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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.enums import FeatureFlag, PlanTier
from app.models.models import (
    PLAN_DEFAULT_FEATURES, PLAN_TOKEN_BUDGETS,
    AuditLog, CrawledJob, CrawlerConfig, InterviewSession, JobApplication, ProductEvent,
    SupportTicket, UsageEvent, UsageLedger, User, UserFeature, UserProfile,
)
from app.schemas.admin import (
    AdminUserDetailOut,
    AdminUserOut,
    AdminUserUpdate,
    AnalyticsOverview,
    AuditLogOut,
    CrawlerConfigOut,
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
from app.services.plans.service import PlanService
from app.services.support.service import SupportService

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

    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    new_users_this_month = db.query(func.count(User.id)).filter(User.created_at >= month_start).scalar() or 0

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


# ── Audit Logs ───────────────────────────────────────────────────────────────

@router.get("/audit-logs", response_model=List[AuditLogOut])
def admin_list_audit_logs(
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

    summary = [
        FeatureFlagSummary(
            feature=f.value,
            enabled_count=counts.get(f.value, {}).get("enabled", 0),
            disabled_count=counts.get(f.value, {}).get("disabled", 0),
        )
        for f in FeatureFlag
    ]

    q = db.query(UserFeature, User.email).join(User, User.id == UserFeature.user_id)
    if feature:
        q = q.filter(UserFeature.feature == feature)
    rows = q.order_by(UserFeature.id.desc()).limit(200).all()

    overrides = [
        FeatureOverrideOut(id=uf.id, user_id=uf.user_id, user_email=email, feature=uf.feature.value, enabled=uf.enabled)
        for uf, email in rows
    ]

    return FeatureFlagsOverview(summary=summary, overrides=overrides)


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
    return [
        TicketListItem(
            id=t.id,
            user_id=t.user_id,
            subject=t.subject,
            category=t.category,
            priority=t.priority,
            status=t.status,
            assigned_admin_id=t.assigned_admin_id,
            message_count=len(service.get_messages(t.id)),
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
