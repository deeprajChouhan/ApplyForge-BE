from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.enums import ApplicationStatus
from app.models.models import User
from app.schemas.application import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationUpdate,
    GenerateRequest,
    JDAnalyzeRequest,
    StatusChangeRequest,
)
from app.services.applications.service import ApplicationService

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("", response_model=ApplicationOut)
def create(payload: ApplicationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).create(payload.model_dump())


@router.get("", response_model=list[ApplicationOut])
def list_apps(status: ApplicationStatus | None = Query(default=None), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).list(status)


@router.get("/kanban")
def kanban(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = ApplicationService(db, user.id)
    result = {}
    for s in ApplicationStatus:
        result[s.value] = [ApplicationOut.model_validate(a).model_dump() for a in service.list(s)]
    return result


@router.get("/{app_id}", response_model=ApplicationOut)
def get_app(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).get(app_id)


@router.patch("/{app_id}", response_model=ApplicationOut)
def update(app_id: int, payload: ApplicationUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).update(app_id, payload.model_dump())


@router.post("/{app_id}/status", response_model=ApplicationOut)
def change_status(app_id: int, payload: StatusChangeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).change_status(app_id, payload.status, payload.note)


@router.post("/{app_id}/analyze")
def analyze_jd(app_id: int, payload: JDAnalyzeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).analyze_jd(app_id, payload.job_description)


@router.post("/{app_id}/generate")
def generate(app_id: int, payload: GenerateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).generate_docs(app_id, payload.doc_types)
