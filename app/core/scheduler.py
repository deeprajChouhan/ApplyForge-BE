"""
Scheduled Job Crawler
=====================
Runs the job crawler for all Pro users who have it enabled,
at three times each day (UTC):

  06:00  — Morning run   (users wake up to fresh listings)
  15:00  — Afternoon run (midday refresh)
  19:00  — Evening run   (end-of-day top picks)

Uses APScheduler with a background thread pool so it doesn't
block FastAPI's async event loop.
"""
from __future__ import annotations

from app.models.enums import FeatureFlag
from app.services.usage.metering import usage_scope
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_all_crawlers() -> None:
    """
    Called by APScheduler at each scheduled time.
    Iterates over all users with crawler enabled and runs their crawl.
    """
    try:
        from app.db.session import SessionLocal
        from app.models.models import CrawlerConfig
        from app.services.crawler.service import CrawlerService

        db = SessionLocal()
        try:
            enabled_configs = (
                db.query(CrawlerConfig)
                .filter(CrawlerConfig.is_enabled == True)
                .all()
            )
            logger.info("scheduler_crawl_start active_users=%d", len(enabled_configs))

            for cfg in enabled_configs:
                try:
                    svc = CrawlerService(db, cfg.user_id)
                    # Attribute (and quota-check) the crawler's AI calls to this user.
                    with usage_scope(cfg.user_id, "crawler.scheduled", FeatureFlag.job_crawler):
                        result = svc.run_crawl()
                    logger.info(
                        "scheduler_crawl_user user_id=%d found=%d added=%d",
                        cfg.user_id, result["jobs_found"], result["jobs_added"],
                    )
                except Exception as user_exc:
                    logger.warning(
                        "scheduler_crawl_user_error user_id=%d error=%s",
                        cfg.user_id, user_exc,
                    )
                    db.rollback()

        finally:
            db.close()

    except Exception as exc:
        logger.error("scheduler_crawl_fatal error=%s", exc)


def start_scheduler() -> None:
    """Start the APScheduler background scheduler. Call once at app startup."""
    global _scheduler

    if _scheduler and _scheduler.running:
        logger.warning("scheduler_already_running — skipping second start")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")

    # 06:00 UTC — morning run
    _scheduler.add_job(
        _run_all_crawlers,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="crawler_morning",
        name="Job Crawler — Morning (06:00 UTC)",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # 15:00 UTC — afternoon run
    _scheduler.add_job(
        _run_all_crawlers,
        trigger=CronTrigger(hour=15, minute=0, timezone="UTC"),
        id="crawler_afternoon",
        name="Job Crawler — Afternoon (15:00 UTC)",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # 19:00 UTC — evening run
    _scheduler.add_job(
        _run_all_crawlers,
        trigger=CronTrigger(hour=19, minute=0, timezone="UTC"),
        id="crawler_evening",
        name="Job Crawler — Evening (19:00 UTC)",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Recruiter Partner API (inline backend only — Celery beat covers these
    # when PARTNER_JOBS_BACKEND=celery): webhook retries + log retention.
    try:
        from app.core.config import settings as _settings
        from apscheduler.triggers.interval import IntervalTrigger

        if (_settings.partner_jobs_backend or "inline").lower() != "celery":
            from app.recruiter.partner.jobs import deliver_webhooks_now, purge_old_logs

            _scheduler.add_job(
                deliver_webhooks_now,
                trigger=IntervalTrigger(seconds=60),
                id="partner_webhook_retries",
                name="Partner API — webhook retries (every 60s)",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            _scheduler.add_job(
                purge_old_logs,
                trigger=CronTrigger(hour=3, minute=17, timezone="UTC"),
                id="partner_log_retention",
                name="Partner API — log retention (03:17 UTC)",
                replace_existing=True,
            )
    except Exception as exc:  # never block crawler scheduling
        logger.warning("partner_scheduler_jobs_failed error=%s", exc)

    # Email jobs (when not on Celery): sweep due/delayed sends, automations, digests.
    try:
        from app.core.config import settings as _s
        from apscheduler.triggers.interval import IntervalTrigger as _Interval

        if (_s.email_jobs_backend or "inline").lower() != "celery":
            from app.services.email import jobs as _email_jobs

            _scheduler.add_job(_email_jobs.sweep, trigger=_Interval(seconds=60), id="email_sweep",
                               name="Email — sweep due sends (every 60s)", replace_existing=True,
                               max_instances=1, coalesce=True)
            _scheduler.add_job(_email_jobs.daily_rules, trigger=CronTrigger(hour=9, minute=0, timezone="Europe/London"),
                               id="email_daily_rules", name="Email — daily automations (09:00 UK)", replace_existing=True)
            _scheduler.add_job(_email_jobs.daily_briefs, trigger=CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone="Europe/London"),
                               id="email_daily_briefs", name="Email — recruiter daily brief (07:30 UK)", replace_existing=True)
            _scheduler.add_job(_email_jobs.weekly_digests, trigger=CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="Europe/London"),
                               id="email_weekly_digests", name="Email — candidate weekly digest (Mon 08:00 UK)", replace_existing=True)
            _scheduler.add_job(_email_jobs.purge_webhook_events, trigger=CronTrigger(hour=3, minute=40, timezone="UTC"),
                               id="email_webhook_purge", name="Email — purge webhook ids (03:40 UTC)", replace_existing=True)
    except Exception as exc:  # never block crawler scheduling
        logger.warning("email_scheduler_jobs_failed error=%s", exc)

    _scheduler.start()
    logger.info(
        "scheduler_started jobs=%s",
        [j.name for j in _scheduler.get_jobs()],
    )


def stop_scheduler() -> None:
    """Gracefully stop the scheduler. Call at app shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
