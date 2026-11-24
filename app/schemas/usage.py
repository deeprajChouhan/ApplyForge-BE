from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AllowanceOut(BaseModel):
    used: int
    limit: int
    remaining: int
    pct: float
    exhausted: bool
    resets_at: Optional[datetime] = None


class DailyAllowanceOut(AllowanceOut):
    window_hours: int = 24
    custom_limit: bool = False
    reset_at: Optional[datetime] = None  # last admin reset


class UsageStatusOut(BaseModel):
    plan: str
    exempt: bool
    blocked: Optional[str] = None  # "daily" | "monthly" | None
    daily: DailyAllowanceOut
    monthly: AllowanceOut
    packages_used_this_month: int = 0
    monthly_package_limit: int = 0  # -1 = unlimited
