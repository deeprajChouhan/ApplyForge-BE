"""
Phase 5 helpers: offer negotiation history, placement fee calc, AI offer talking points.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.recruiter.models import Offer, OfferNegotiation, Placement
from app.recruiter.services import ai_support


def compute_placement_fee(final_compensation: dict | None, fee_percent: float | None) -> int | None:
    """
    Naive fee calc = final_compensation.amount × fee_percent / 100. Returns
    the numeric amount in the same currency as the compensation. Rounds to
    the nearest whole unit.
    """
    if not final_compensation or fee_percent is None:
        return None
    amount = final_compensation.get("amount")
    if amount is None:
        return None
    period = (final_compensation.get("period") or "YEAR").upper()
    # Annualise if given monthly/day/hour (naive: month × 12; day × 240; hour × 1800)
    factor = {"YEAR": 1, "MONTH": 12, "WEEK": 48, "DAY": 240, "HOUR": 1800}.get(period, 1)
    annual = amount * factor
    return int(round(annual * (fee_percent / 100.0)))


def guarantee_end_date(start_date: date | None, guarantee_weeks: int | None) -> date | None:
    if not start_date or not guarantee_weeks:
        return None
    return start_date + timedelta(weeks=int(guarantee_weeks))


_TALKING_SYSTEM = (
    "You are a senior recruiter drafting talking points for an offer "
    "negotiation. Ground every point in the supplied data — candidate "
    "expectations, client range, negotiation history, candidate motivations. "
    "NEVER autonomously suggest what to offer, only summarize context and "
    "propose talking points a recruiter can bring to the call. Return "
    "STRICT JSON: {\"talking_points\": [string, ...], \"risks\": [string, ...]}"
)


def offer_talking_points(
    offer: Offer,
    negotiations: list[OfferNegotiation],
    candidate_expected: dict | None,
    candidate_motivation: str | None,
) -> dict[str, Any]:
    if not ai_support.llm_enabled():
        return {"talking_points": [], "risks": [], "used_llm": False,
                "generated_at": datetime.now(timezone.utc).isoformat()}
    ctx = [
        f"Current offer: {offer.base_compensation}",
        f"Bonus: {offer.bonus}; equity: {offer.equity}; allowances: {offer.allowances}",
        f"Candidate expected: {candidate_expected}",
        f"Candidate motivation: {candidate_motivation}",
        "Negotiation history:",
    ]
    for n in negotiations[-6:]:
        ctx.append(f"- {n.round_label} from {n.from_party}: {n.compensation} — {n.comment or ''}")
    data = ai_support.generate_json(_TALKING_SYSTEM, "\n".join(ctx))
    if not isinstance(data, dict):
        return {"talking_points": [], "risks": [], "used_llm": False,
                "generated_at": datetime.now(timezone.utc).isoformat()}
    return {
        "talking_points": [str(x) for x in (data.get("talking_points") or [])][:6],
        "risks": [str(x) for x in (data.get("risks") or [])][:5],
        "used_llm": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


DEFAULT_CHECKIN_OFFSETS = (7, 30, 60, 90)


def default_checkin_schedule(start_date: date | None) -> list[dict[str, Any]]:
    out = []
    for off in DEFAULT_CHECKIN_OFFSETS:
        due = start_date + timedelta(days=off) if start_date else None
        out.append({"day_offset": off, "due_date": due.isoformat() if due else None})
    return out
