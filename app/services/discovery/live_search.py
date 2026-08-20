"""Per-user live job discovery.

Runs on a fast beat (every 2 min) and, for each active user, hits the
Greenhouse boards for slugs relevant to that user's target titles. New
jobs are upserted and `tick_user` is triggered immediately so freshly-
discovered matches reach the queue in seconds instead of waiting for
the next scheduled orchestrator tick.

This complements — does NOT replace — the hourly poller in
`app.services.ats.tasks.poll_provider`. The hourly poll is the source
of truth (full sweep, deactivates stale jobs); live_search is a fast,
per-user delta layer that keeps the "always fetching" feel.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

import structlog
from celery import shared_task
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.auto_apply import AutoApplySettings
from app.models.job import Job
from app.services.ats.greenhouse import BOOTSTRAP_SLUGS, GreenhouseProvider
from app.services.ats.tasks import upsert_company, upsert_job
from app.schemas.ats import NormalizedCompany

logger = structlog.get_logger(__name__)

# Cap how many boards we hit per user per tick — keeps external HTTP
# traffic bounded. With ~150 slugs and one user this is 150 requests
# every 2 min; that's ~1.25 rps to Greenhouse, well under any rate limit.
BOARDS_PER_USER_PER_TICK = 200


def _title_tokens(titles: list[str] | None) -> list[str]:
    """Same tokenization as the orchestrator SQL prefilter."""
    if not titles:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for phrase in titles:
        phrase = (phrase or "").strip().lower()
        if not phrase:
            continue
        if phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
        for tok in phrase.split():
            if len(tok) > 2 and tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def _title_matches(title: str, tokens: list[str]) -> bool:
    if not tokens:
        # Without a title preference, match everything so discovery still
        # populates the pool for a user who hasn't set titles yet.
        return True
    t = title.lower()
    return any(tok in t for tok in tokens)


async def _discover_for_user_async(target_tokens: list[str], slugs: list[str]) -> list[dict]:
    """Hit each Greenhouse board and return jobs whose title matches
    the user's target tokens. Returns a list of dicts ready for upsert."""
    provider = GreenhouseProvider()
    results: list[dict] = []

    for slug in slugs[:BOARDS_PER_USER_PER_TICK]:
        company = NormalizedCompany(
            ats_provider=provider.name,
            ats_slug=slug,
            name=slug,
            careers_url=f"https://boards.greenhouse.io/{slug}",
        )
        try:
            async for job in provider.list_jobs(company):
                if _title_matches(job.title, target_tokens):
                    results.append({"company": company, "job": job})
        except Exception as exc:
            logger.warning("live_search.slug_failed", slug=slug, error=str(exc))
            continue

    return results


@shared_task(name="app.services.discovery.live_search.search_for_user")
def search_for_user(user_id: int) -> Dict[str, Any]:
    """Discover title-matching jobs for a single user across configured boards.

    Insert any new ones and immediately trigger the orchestrator so
    matches get queued within seconds.
    """
    try:
        with SessionLocal() as db:
            settings = (
                db.execute(select(AutoApplySettings).where(AutoApplySettings.user_id == user_id))
                .scalars()
                .first()
            )
            if settings is None or not getattr(settings, "is_active", False):
                return {"skipped": True, "reason": "inactive_or_missing", "user_id": user_id}
            if getattr(settings, "paused_at", None) is not None:
                return {"skipped": True, "reason": "paused", "user_id": user_id}

            tokens = _title_tokens(getattr(settings, "target_titles_json", None) or [])
            # Small optimization: if the user has titles, we could later
            # narrow slugs to a per-title relevance list. For now, sweep
            # all configured boards — external HTTP is cheap and cached
            # server-side by CDN.
            matches = asyncio.run(_discover_for_user_async(tokens, BOOTSTRAP_SLUGS))

            if not matches:
                return {"user_id": user_id, "matched": 0, "new": 0}

            now = datetime.now(timezone.utc)
            new_count = 0
            for m in matches:
                company = upsert_company(db, m["company"])
                db.commit()
                _, is_new = upsert_job(db, company, m["job"], now)
                db.commit()
                if is_new:
                    new_count += 1

            # Kick off the orchestrator immediately so new jobs get matched
            # + queued in this same minute, not on the next 5-min tick.
            if new_count > 0:
                _trigger_tick_user(user_id)

            return {"user_id": user_id, "matched": len(matches), "new": new_count}
    except Exception as exc:
        logger.error("live_search.search_for_user_failed", user_id=user_id, error=str(exc))
        return {"error": str(exc), "user_id": user_id}


def _trigger_tick_user(user_id: int) -> None:
    """Indirection to avoid a circular import with the orchestrator module."""
    from app.services.auto_apply.orchestrator import tick_user

    tick_user.delay(user_id)


@shared_task(name="app.services.discovery.live_search.search_for_all_users")
def search_for_all_users() -> Dict[str, Any]:
    """Fan out `search_for_user` for every active, unpaused user."""
    try:
        with SessionLocal() as db:
            user_ids = (
                db.execute(
                    select(AutoApplySettings.user_id).where(
                        AutoApplySettings.is_active == True,  # noqa: E712
                        AutoApplySettings.paused_at.is_(None),
                    )
                )
                .scalars()
                .all()
            )
        for uid in user_ids:
            search_for_user.delay(uid)
        return {"queued": len(user_ids)}
    except Exception as exc:
        logger.error("live_search.search_for_all_users_failed", error=str(exc))
        return {"error": str(exc)}
