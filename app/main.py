import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging

logger = logging.getLogger(__name__)
setup_logging()
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def seed_and_promote_admin() -> None:
    """
    Idempotent startup task:
    1. If ADMIN_PASSWORD is set and the admin account does not exist → create it.
    2. Ensure the admin account has role=admin, plan=pro, and all feature flags enabled.
    Runs every boot — completely safe to re-run.
    """
    try:
        from app.core.security import hash_password
        from app.db.session import SessionLocal
        from app.models.enums import FeatureFlag, PlanTier, SubscriptionStatus, UserRole
        from app.models.models import (
            PLAN_DEFAULT_FEATURES, PLAN_TOKEN_BUDGETS,
            User, UserFeature, UserProfile,
        )

        db = SessionLocal()
        try:
            admin_user = db.query(User).filter(User.email.ilike(settings.admin_email)).first()

            # ── Step 1: Create admin if missing and password is provided ──
            if not admin_user:
                if not settings.admin_password:
                    logger.info(
                        "Admin <%s> not found and ADMIN_PASSWORD not set — skipping creation.",
                        settings.admin_email,
                    )
                    return
                admin_user = User(
                    email=settings.admin_email,
                    password_hash=hash_password(settings.admin_password),
                    role=UserRole.admin,
                    plan=PlanTier.pro,
                    subscription_status=SubscriptionStatus.active,
                    token_budget_monthly=PLAN_TOKEN_BUDGETS[PlanTier.pro],
                )
                db.add(admin_user)
                db.flush()
                db.add(UserProfile(user_id=admin_user.id, full_name="Admin"))
                logger.info("Admin user <%s> created on first boot.", settings.admin_email)

            # ── Step 2: Promote / synchronize role, plan, features ─────────
            changed = False
            if admin_user.role != UserRole.admin:
                admin_user.role = UserRole.admin
                changed = True
            if admin_user.plan != PlanTier.pro:
                admin_user.plan = PlanTier.pro
                admin_user.token_budget_monthly = PLAN_TOKEN_BUDGETS[PlanTier.pro]
                changed = True

            for feature in list(FeatureFlag):
                existing = db.query(UserFeature).filter_by(
                    user_id=admin_user.id, feature=feature
                ).first()
                if not existing:
                    db.add(UserFeature(user_id=admin_user.id, feature=feature, enabled=True))
                    changed = True
                elif not existing.enabled:
                    existing.enabled = True
                    changed = True

            db.commit()
            if changed:
                logger.info("Admin <%s> promoted / synchronized on startup.", settings.admin_email)
            else:
                logger.info("Admin <%s> already configured correctly.", settings.admin_email)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Admin startup task failed (non-fatal): %s", exc)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(api_router, prefix="/api/v1")

