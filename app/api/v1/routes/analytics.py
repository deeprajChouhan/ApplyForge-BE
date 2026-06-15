"""
Website & product analytics ingestion.

  POST /analytics/events -> record a product/website event (anon or authenticated)
"""
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_optional
from app.db.session import get_db
from app.models.models import User
from app.schemas.analytics import AnalyticsEventIn
from app.services.analytics.service import ProductEventService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
def record_event(
    payload: AnalyticsEventIn,
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    ProductEventService(db).track(
        payload.event_name,
        user=user,
        properties=payload.properties,
        request=request,
        referrer=payload.referrer,
    )
