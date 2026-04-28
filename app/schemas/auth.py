from typing import List
from pydantic import BaseModel, EmailStr, Field

from app.models.enums import FeatureFlag, PlanTier, SubscriptionStatus, UserRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    phone_number: str | None = Field(default=None, max_length=50)
    age: int | None = Field(default=None, ge=13, le=120)
    location: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserMe(BaseModel):
    id: int
    email: EmailStr
    role: UserRole
    plan: PlanTier
    subscription_status: SubscriptionStatus
    token_budget_monthly: int
    features: List[str] = []  # list of enabled FeatureFlag values

    model_config = {"from_attributes": True}

