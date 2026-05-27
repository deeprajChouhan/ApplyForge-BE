"""
Job Crawler API routes — Pro feature.

Endpoints:
  GET  /crawler/config           → get user's crawler config
  PUT  /crawler/config           → upsert crawler config
  GET  /crawler/jobs             → list discovered jobs
  POST /crawler/jobs/{id}/action → dismiss / save a job
  POST /crawler/run              → manually trigger a crawl (admin / debug)
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_feature
from app.db.session import get_db
from app.models.enums import FeatureFlag
from app.models.models import User
from app.schemas.crawler import (
    CrawledJobAction,
    CrawledJobOut,
    CrawlerConfigOut,
    CrawlerConfigUpdate,
    CrawlTriggerResponse,
)
from app.services.crawler.service import CrawlerService

router = APIRouter(prefix="/crawler", tags=["crawler"])

_need_crawler = Depends(require_feature(FeatureFlag.job_crawler))


@router.get("/config", response_model=CrawlerConfigOut, dependencies=[_need_crawler])
def get_config(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the user's crawler configuration (creates default if none exists)."""
    return CrawlerService(db, user.id).get_or_create_config()


@router.put("/config", response_model=CrawlerConfigOut, dependencies=[_need_crawler])
def upsert_config(
    payload: CrawlerConfigUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create or update the crawler configuration for the current user."""
    return CrawlerService(db, user.id).update_config(
        payload.model_dump(exclude_none=True)
    )


@router.get("/jobs", response_model=list[CrawledJobOut], dependencies=[_need_crawler])
def list_jobs(
    date: Optional[str] = Query(default=None, description="Filter by crawl date (YYYY-MM-DD)"),
    show_dismissed: bool = Query(default=False),
    min_score: Optional[float] = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List discovered jobs for the current user, sorted by match score descending."""
    return CrawlerService(db, user.id).list_jobs(
        date_str=date,
        show_dismissed=show_dismissed,
        min_score=min_score,
        limit=limit,
        offset=offset,
    )


@router.post("/jobs/{job_id}/action", response_model=CrawledJobOut, dependencies=[_need_crawler])
def action_job(
    job_id: int,
    payload: CrawledJobAction,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Perform an action on a discovered job:
    - is_dismissed=true → hide the job
    - is_saved=true → add to applications as a draft
    """
    return CrawlerService(db, user.id).action_job(
        job_id=job_id,
        is_dismissed=payload.is_dismissed,
        is_saved=payload.is_saved,
    )


@router.post("/run", response_model=CrawlTriggerResponse, dependencies=[_need_crawler])
def manual_run(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Manually trigger the crawler for the current user (runs synchronously)."""
    result = CrawlerService(db, user.id).run_crawl()
    return CrawlTriggerResponse(
        message="Crawl complete",
        jobs_found=result["jobs_found"],
        jobs_added=result["jobs_added"],
    )
