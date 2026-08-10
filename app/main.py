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
    # Allow any Chrome/Firefox extension origin (IDs change per install), plus any
    # applyforge.pro subdomain (recruiter, admin, etc.) so new frontends don't need
    # a CORS_ORIGINS change. The JWT still enforces auth on every actual request.
    allow_origin_regex=(
        r"chrome-extension://.*|moz-extension://.*|"
        r"https://([a-z0-9-]+\.)*applyforge\.pro"
    ),
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


@app.on_event("startup")
def backfill_free_features() -> None:
    """
    Idempotent: grant all free-tier features (jd_analyze, applications, resume)
    to any free-plan user who is missing them.
    """
    try:
        from app.db.session import SessionLocal
        from app.models.enums import FeatureFlag, PlanTier
        from app.models.models import User, UserFeature

        FREE_FEATURES = [FeatureFlag.jd_analyze, FeatureFlag.applications, FeatureFlag.resume]

        db = SessionLocal()
        try:
            free_users = db.query(User).filter(User.plan == PlanTier.free).all()
            added = 0
            for user in free_users:
                for feature in FREE_FEATURES:
                    existing = db.query(UserFeature).filter_by(
                        user_id=user.id, feature=feature
                    ).first()
                    if not existing:
                        db.add(UserFeature(user_id=user.id, feature=feature, enabled=True))
                        added += 1
                    elif not existing.enabled:
                        existing.enabled = True
                        added += 1
            if added:
                db.commit()
                logger.info("Backfilled free features for %d user-feature row(s).", added)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Free feature backfill startup task failed (non-fatal): %s", exc)


@app.on_event("startup")
def clear_admin_crawled_jobs() -> None:
    """
    On every deploy: wipe all crawled_jobs rows for the admin account so the
    Discovered Jobs page starts clean after each redeployment.
    Only runs if ADMIN_EMAIL is configured.
    """
    try:
        from app.db.session import SessionLocal
        from app.models.models import CrawledJob, User

        db = SessionLocal()
        try:
            admin = db.query(User).filter(User.email.ilike(settings.admin_email)).first()
            if not admin:
                return
            deleted = db.query(CrawledJob).filter(CrawledJob.user_id == admin.id).delete()
            db.commit()
            if deleted:
                logger.info(
                    "deploy_cleanup: deleted %d crawled_jobs for admin <%s>",
                    deleted, settings.admin_email,
                )
            else:
                logger.info(
                    "deploy_cleanup: no crawled_jobs to clear for admin <%s>",
                    settings.admin_email,
                )
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Admin crawled-jobs cleanup failed (non-fatal): %s", exc)


@app.on_event("startup")
def ensure_recruiter_schema() -> None:
    """
    Create the recruiter module's rec_* tables if missing. Isolated from the
    consumer schema (its own tables, no cross foreign keys) and idempotent —
    safe to run on every boot.
    """
    try:
        from app.recruiter.init_db import ensure_recruiter_tables
        ensure_recruiter_tables()
        logger.info("recruiter schema ready (rec_* tables ensured).")
    except Exception as exc:
        logger.warning("recruiter schema bootstrap failed (non-fatal): %s", exc)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(api_router, prefix="/api/v1")


@app.on_event("startup")
def start_job_crawler_scheduler() -> None:
    """Start the APScheduler background scheduler for automated job crawling."""
    try:
        from app.core.scheduler import start_scheduler
        start_scheduler()
    except Exception as exc:
        logger.warning("Job crawler scheduler failed to start (non-fatal): %s", exc)


@app.on_event("shutdown")
def stop_job_crawler_scheduler() -> None:
    """Gracefully stop the background scheduler on app shutdown."""
    try:
        from app.core.scheduler import stop_scheduler
        stop_scheduler()
    except Exception as exc:
        logger.warning("Job crawler scheduler shutdown error (non-fatal): %s", exc)
