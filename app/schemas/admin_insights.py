"""Response models for the operator console's overview, cost and quality views."""
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── Overview ────────────────────────────────────────────────────────────────

class Comparison(BaseModel):
    current: float
    previous: float


class OverviewKpis(BaseModel):
    users_total: int
    paid_users: int
    signups: Comparison
    active_users: Comparison
    tokens: Comparison
    ai_calls: Comparison
    cost_usd: Comparison
    comparison_note: str


class TrendPoint(BaseModel):
    date: str
    signups: int
    active_users: int
    tokens: int


class TicketItem(BaseModel):
    ticket_id: int
    subject: str
    priority: str
    waiting_since: datetime
    email: Optional[str] = None


class TicketsAttention(BaseModel):
    count: int
    urgent: int
    items: List[TicketItem]


class BudgetItem(BaseModel):
    user_id: int
    email: str
    plan: str
    tokens_used: int
    budget: int
    pct: int


class BudgetAttention(BaseModel):
    count: int
    over_budget: int
    items: List[BudgetItem]


class UserRef(BaseModel):
    user_id: int
    email: str
    plan: Optional[str] = None


class PastDueAttention(BaseModel):
    count: int
    items: List[UserRef]


class ParseItem(BaseModel):
    user_id: int
    email: str
    confidence: float
    parsed_at: datetime


class ParseAttention(BaseModel):
    count: int
    threshold: float
    items: List[ParseItem]


class OverviewAttention(BaseModel):
    tickets_awaiting_reply: TicketsAttention
    users_near_budget: BudgetAttention
    past_due_subscriptions: PastDueAttention
    low_confidence_parses: ParseAttention


class AdminOverviewOut(BaseModel):
    generated_at: datetime
    kpis: OverviewKpis
    trend: List[TrendPoint]
    attention: OverviewAttention


# ── Costs ───────────────────────────────────────────────────────────────────

class CostTotals(BaseModel):
    tokens: int
    tokens_in: int
    tokens_out: int
    calls: int
    cost_usd: float


class CostByModel(CostTotals):
    model: str


class CostByFeature(CostTotals):
    feature: str


class CostByUser(CostTotals):
    user_id: Optional[int] = None  # None = anonymous preview / system job
    email: Optional[str] = None


class CostByOperation(CostTotals):
    operation: str  # "POST /applications/{id}/tailor", "crawler.scheduled", …


class CostByKind(CostTotals):
    kind: str  # "llm" | "embedding"


class CostByDay(CostTotals):
    date: str


class AdminCostsOut(BaseModel):
    month: str
    is_current_month: bool
    total: CostTotals
    previous_month_total_usd: float
    projected_month_end_usd: Optional[float] = Field(
        default=None, description="Linear projection from month-to-date spend (current month only)"
    )
    active_users: int
    cost_per_active_user_usd: float
    unpriced_models: List[str] = Field(
        default_factory=list, description="Models priced at the default rate — add them to app/core/llm_pricing.py"
    )
    by_model: List[CostByModel]
    by_feature: List[CostByFeature]
    by_operation: List[CostByOperation] = Field(default_factory=list)
    by_kind: List[CostByKind] = Field(default_factory=list)
    top_users: List[CostByUser]
    by_day: List[CostByDay]


# ── Quality ─────────────────────────────────────────────────────────────────

class FlagOut(BaseModel):
    claim: Optional[str] = None
    claim_type: Optional[str] = None
    reason: Optional[str] = None
    confidence: Optional[float] = None


class FlaggedDoc(BaseModel):
    application_id: int
    user_id: Optional[int] = None
    email: Optional[str] = None
    doc_type: str
    flag_count: int
    evaluated_at: datetime
    flags: List[FlagOut]


class DocTypeQuality(BaseModel):
    doc_type: str
    evaluated: int
    flagged: int


class ClaimTypeCount(BaseModel):
    claim_type: str
    count: int


class HallucinationStats(BaseModel):
    evaluated_docs: int
    flagged_docs: int
    flag_rate: Optional[float] = None
    avg_flags_per_doc: Optional[float] = None
    min_confidence: float
    by_doc_type: List[DocTypeQuality]
    by_claim_type: List[ClaimTypeCount]
    recent_flagged: List[FlaggedDoc]
    last_checked_at: Optional[datetime] = None


class GenerationStats(BaseModel):
    documents_generated: int
    evaluation_coverage: Optional[float] = None


class ParsingStats(BaseModel):
    parses: int
    avg_confidence: Optional[float] = None
    low_confidence: int
    threshold: float


class AdminQualityOut(BaseModel):
    window_days: int
    hallucination: HallucinationStats
    generation: GenerationStats
    parsing: ParsingStats
    outcomes: Dict[str, int]


class SpotCheckRequest(BaseModel):
    sample_size: int = Field(default=50, ge=1, le=200)
    days: int = Field(default=30, ge=1, le=365)


class SpotCheckResult(BaseModel):
    checked: int
    flagged: int
    skipped_no_grounding: int
    flag_rate: Optional[float] = None
    window_days: int
