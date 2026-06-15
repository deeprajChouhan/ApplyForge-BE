"""
Job Discovery API routes.

  GET /jobs/feed → on-demand personalized job feed (live postings, with a
                   demo-data fallback when no live postings match).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_feature
from app.db.session import get_db
from app.models.enums import FeatureFlag
from app.models.models import User
from app.schemas.jobs import JobFeedResponse
from app.services.analytics.service import ProductEventService
from app.services.jobs.service import JobFeedService

router = APIRouter(prefix="/jobs", tags=["jobs"])

_need_job_discovery = Depends(require_feature(FeatureFlag.job_discovery))


@router.get("/feed", response_model=JobFeedResponse, dependencies=[_need_job_discovery])
def get_feed(
    keywords: Optional[str] = Query(default=None, description="Comma-separated role keywords"),
    work_type: str = Query(default="any", pattern="^(remote|hybrid|onsite|any)$"),
    country: Optional[str] = Query(default=None, max_length=100),
    min_score: Optional[float] = Query(default=None, ge=0, le=100),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a personalized feed of job openings, with a demo-data fallback."""
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else None
    result = JobFeedService(db, user.id).get_feed(
        keywords=kw_list,
        work_type=work_type,
        country=country,
        min_score=min_score,
        limit=limit,
    )
    ProductEventService(db).track(
        "job_feed_viewed",
        user=user,
        properties={"is_fallback": result.is_fallback, "count": len(result.items)},
    )
    return result
