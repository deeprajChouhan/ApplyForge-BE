from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, EmailStr

from app.models.enums import FeatureFlag, PlanTier, SubscriptionStatus, UserRole


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
    total_tokens_this_month: int
    total_api_calls_this_month: int
    tokens_by_feature: dict
    top_users: List[dict]
