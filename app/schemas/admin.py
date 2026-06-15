import json
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, EmailStr, field_validator

from app.models.enums import FeatureFlag, PlanTier, SubscriptionStatus, UserRole


def _parse_json_dict(v: Any) -> dict:
    if v is None:
        return {}
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return {}
    return v


def _parse_json_dict_optional(v: Any) -> Optional[dict]:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None
    return v


def _parse_json_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return []
    return v


class UserFeatureOut(BaseModel):
    feature: str
    enabled: bool

    model_config = {"from_attributes": True}


class UserUsageSummary(BaseModel):
    month_year: str
    tokens_used: int
    api_calls: int


class AdminUserOut(BaseModel):
    id: int
    email: EmailStr
    role: UserRole
    plan: PlanTier
    subscription_status: SubscriptionStatus
    token_budget_monthly: int
    is_active: bool
    created_at: datetime
    features: List[str] = []
    usage_current_month: Optional[UserUsageSummary] = None

    model_config = {"from_attributes": True}


class AdminUserUpdate(BaseModel):
    role: Optional[UserRole] = None
    plan: Optional[PlanTier] = None
    subscription_status: Optional[SubscriptionStatus] = None
    token_budget_monthly: Optional[int] = None
    is_active: Optional[bool] = None


class FeatureToggleRequest(BaseModel):
    feature: FeatureFlag
    enabled: bool


class PlatformUsageStats(BaseModel):
    total_users: int
    active_users_this_month: int
    new_users_this_month: int
    total_tokens_this_month: int
    total_api_calls_this_month: int
    total_packages_this_month: int
    open_support_tickets: int
    plan_counts: dict
    tokens_by_feature: dict
    top_users: List[dict]


# ── Product Events ──────────────────────────────────────────────────────────

class ProductEventOut(BaseModel):
    id: int
    user_id: Optional[int] = None
    user_email: Optional[str] = None
    event_name: str
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    properties: dict = {}
    referrer: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("properties", mode="before")
    @classmethod
    def _validate_properties(cls, v: Any) -> dict:
        return _parse_json_dict(v)


# ── Audit Logs ──────────────────────────────────────────────────────────────

class AuditLogOut(BaseModel):
    id: int
    actor_user_id: Optional[int] = None
    actor_user_email: Optional[str] = None
    actor_role: Optional[str] = None
    action: str
    entity_type: str
    entity_id: Optional[int] = None
    before: Optional[dict] = None
    after: Optional[dict] = None
    extra_metadata: Optional[dict] = None
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("before", "after", "extra_metadata", mode="before")
    @classmethod
    def _validate_json_fields(cls, v: Any) -> Optional[dict]:
        return _parse_json_dict_optional(v)


# ── Analytics Overview ──────────────────────────────────────────────────────

class FunnelStep(BaseModel):
    label: str
    event_name: str
    count: int


class AnalyticsOverview(BaseModel):
    total_users: int
    new_users_this_month: int
    plan_counts: dict
    onboarding_completion_rate: float
    free_to_paid_conversion_rate: float
    funnel: List[FunnelStep]
    top_events: List[dict]
    packages_generated_this_month: int
    open_support_tickets: int


# ── Aggregated User Detail ──────────────────────────────────────────────────

class AdminUserDetailOut(AdminUserOut):
    onboarding_completed: bool = False
    applications_count: int = 0
    packages_generated_total: int = 0
    interview_sessions_count: int = 0
    support_tickets_count: int = 0
    recent_events: List[ProductEventOut] = []
    recent_audit_logs: List[AuditLogOut] = []


# ── Feature Flag Overrides ──────────────────────────────────────────────────

class FeatureFlagSummary(BaseModel):
    feature: str
    enabled_count: int
    disabled_count: int


class FeatureOverrideOut(BaseModel):
    id: int
    user_id: int
    user_email: str
    feature: str
    enabled: bool

    model_config = {"from_attributes": True}


class FeatureFlagsOverview(BaseModel):
    summary: List[FeatureFlagSummary]
    overrides: List[FeatureOverrideOut]


# ── Crawler Configs ──────────────────────────────────────────────────────────

class CrawlerConfigOut(BaseModel):
    id: int
    user_id: int
    user_email: str
    is_enabled: bool
    job_roles: List[str] = []
    country: Optional[str] = None
    work_type: str
    daily_goal: int
    last_run_at: Optional[datetime] = None
    crawled_jobs_count: int = 0

    model_config = {"from_attributes": True}

    @field_validator("job_roles", mode="before")
    @classmethod
    def _validate_job_roles(cls, v: Any) -> list:
        return _parse_json_list(v)
