"""
Stripe billing for recruiter agencies (Phase 5.4).

Per-agency billing model: an agency is billed either flat (one fixed price,
Stripe quantity 1) or per-seat (price × recruiter seats, quantity = seat count).
The operator chooses the model per agency.

Everything degrades safely when Stripe isn't configured — `is_enabled()` returns
False and the callers surface a clear "billing not enabled" error, so the app and
tests run fine without Stripe. `stripe` is imported lazily so it isn't a hard
dependency of the process.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.recruiter.enums import AgencyPlan, BillingModel
from app.recruiter.models import Agency, Recruiter


class BillingError(Exception):
    """Raised for billing problems the caller should surface to the user."""


def is_enabled() -> bool:
    return bool(settings.stripe_secret_key_value)


def _stripe():
    if not is_enabled():
        raise BillingError("Billing is not enabled on this deployment")
    import stripe  # lazy — not a hard dependency

    stripe.api_key = settings.stripe_secret_key_value
    return stripe


def price_for(plan: AgencyPlan, model: BillingModel) -> str | None:
    """Resolve the Stripe price id for a plan + billing model (None for free)."""
    table = {
        (AgencyPlan.pro, BillingModel.flat): settings.stripe_price_pro_flat,
        (AgencyPlan.pro, BillingModel.per_seat): settings.stripe_price_pro_seat,
        (AgencyPlan.enterprise, BillingModel.flat): settings.stripe_price_enterprise_flat,
        (AgencyPlan.enterprise, BillingModel.per_seat): settings.stripe_price_enterprise_seat,
    }
    return table.get((plan, model))


def seat_count(db: Session, agency_id: int) -> int:
    return int(db.query(func.count(Recruiter.id)).filter(Recruiter.agency_id == agency_id).scalar() or 0)


def _ensure_customer(db: Session, agency: Agency) -> str:
    if agency.stripe_customer_id:
        return agency.stripe_customer_id
    stripe = _stripe()
    customer = stripe.Customer.create(name=agency.name, metadata={"agency_id": str(agency.id)})
    agency.stripe_customer_id = customer["id"]
    db.commit()
    return customer["id"]


def create_checkout_session(db: Session, agency: Agency, plan: AgencyPlan) -> str:
    """Start a subscription checkout for `plan` using the agency's billing model."""
    if plan == AgencyPlan.free:
        raise BillingError("The free plan does not require checkout")
    price = price_for(plan, agency.billing_model)
    if not price:
        raise BillingError(f"No Stripe price configured for {plan.value} ({agency.billing_model.value})")

    stripe = _stripe()
    customer_id = _ensure_customer(db, agency)
    quantity = seat_count(db, agency.id) if agency.billing_model == BillingModel.per_seat else 1

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price, "quantity": max(1, quantity)}],
        success_url=settings.billing_success_url,
        cancel_url=settings.billing_cancel_url,
        metadata={"agency_id": str(agency.id), "plan": plan.value},
        subscription_data={"metadata": {"agency_id": str(agency.id), "plan": plan.value}},
    )
    return session["url"]


def create_portal_session(db: Session, agency: Agency) -> str:
    if not agency.stripe_customer_id:
        raise BillingError("This agency has no billing account yet")
    stripe = _stripe()
    session = stripe.billing_portal.Session.create(
        customer=agency.stripe_customer_id, return_url=settings.billing_portal_return_url
    )
    return session["url"]


def sync_seat_quantity(db: Session, agency: Agency) -> None:
    """
    Keep the per-seat subscription quantity in step with the seat count. No-op
    for flat billing, when billing is disabled, or when there's no subscription.
    Best-effort — never breaks the underlying seat change.
    """
    if not is_enabled() or agency.billing_model != BillingModel.per_seat or not agency.stripe_subscription_id:
        return
    try:
        stripe = _stripe()
        sub = stripe.Subscription.retrieve(agency.stripe_subscription_id)
        item_id = sub["items"]["data"][0]["id"]
        stripe.SubscriptionItem.modify(
            item_id, quantity=max(1, seat_count(db, agency.id)), proration_behavior="create_prorations"
        )
    except Exception:
        pass


# ── Webhook handling ──────────────────────────────────────────────────────
_STATUS_ACTIVE = {"active", "trialing"}


def construct_event(payload: bytes, signature: str):
    stripe = _stripe()
    secret = settings.stripe_webhook_secret_value
    if not secret:
        raise BillingError("Webhook secret not configured")
    return stripe.Webhook.construct_event(payload, signature, secret)


def handle_event(db: Session, event: dict) -> None:
    """Update the agency's subscription state from a Stripe event."""
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    agency = _agency_for_event(db, obj)
    if agency is None:
        return

    if etype == "checkout.session.completed":
        agency.stripe_subscription_id = obj.get("subscription") or agency.stripe_subscription_id
        agency.subscription_status = "active"
        plan = (obj.get("metadata") or {}).get("plan")
        if plan in (p.value for p in AgencyPlan):
            agency.plan = AgencyPlan(plan)
    elif etype in ("customer.subscription.updated", "customer.subscription.created"):
        agency.stripe_subscription_id = obj.get("id") or agency.stripe_subscription_id
        agency.subscription_status = obj.get("status", agency.subscription_status)
        plan = (obj.get("metadata") or {}).get("plan")
        if plan in (p.value for p in AgencyPlan):
            agency.plan = AgencyPlan(plan)
    elif etype == "customer.subscription.deleted":
        agency.subscription_status = "canceled"
        agency.plan = AgencyPlan.free
        agency.stripe_subscription_id = None

    db.commit()


def _agency_for_event(db: Session, obj: dict) -> Agency | None:
    aid = (obj.get("metadata") or {}).get("agency_id")
    if aid:
        try:
            return db.get(Agency, int(aid))
        except (TypeError, ValueError):
            pass
    customer = obj.get("customer")
    if customer:
        return db.query(Agency).filter(Agency.stripe_customer_id == customer).first()
    return None
