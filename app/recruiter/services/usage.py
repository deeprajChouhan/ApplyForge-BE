"""
Usage metering (Phase 5.2).

record() appends one usage event per billable action; summary() rolls events up
by kind for a given month. Recording is best-effort — a metering failure must
never break the underlying recruiter action — so callers wrap it defensively and
it commits on its own small unit of work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.recruiter.enums import UsageKind
from app.recruiter.models import UsageEvent


def record(db: Session, agency_id: int, kind: UsageKind, quantity: int = 1) -> None:
    """Append a usage event. Never raises — metering is non-critical."""
    if quantity <= 0:
        return
    try:
        db.add(UsageEvent(agency_id=agency_id, kind=kind.value, quantity=quantity))
        db.commit()
    except Exception:
        db.rollback()


def _month_bounds(month: str | None) -> tuple[datetime, datetime]:
    """Return [start, end) datetimes for a YYYY-MM month (default: current)."""
    today = date.today()
    if month:
        y, m = (int(p) for p in month.split("-", 1))
    else:
        y, m = today.year, today.month
    start = datetime(y, m, 1)
    end = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
    return start, end


@dataclass
class UsageSummary:
    agency_id: int
    month: str
    by_kind: dict[str, int] = field(default_factory=dict)
    total: int = 0


def summary(db: Session, agency_id: int, month: str | None = None) -> UsageSummary:
    start, end = _month_bounds(month)
    rows = (
        db.query(UsageEvent.kind, func.coalesce(func.sum(UsageEvent.quantity), 0))
        .filter(
            UsageEvent.agency_id == agency_id,
            UsageEvent.created_at >= start,
            UsageEvent.created_at < end,
        )
        .group_by(UsageEvent.kind)
        .all()
    )
    counts = {k.value: 0 for k in UsageKind}
    for kind, qty in rows:
        counts[kind] = int(qty)
    return UsageSummary(
        agency_id=agency_id,
        month=f"{start.year:04d}-{start.month:02d}",
        by_kind=counts,
        total=sum(counts.values()),
    )
