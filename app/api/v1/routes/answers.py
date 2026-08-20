from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import User
from app.schemas.answer import (
    AnswerIn,
    AnswerLookupIn,
    AnswerLookupOut,
    AnswerOut,
    AnswerSeedResult,
    AnswerUpdate,
)
from app.services.answer_library.service import AnswerLibraryService

router = APIRouter(prefix="/answers", tags=["answers"])


@router.get("", response_model=list[AnswerOut])
@router.get("/", response_model=list[AnswerOut])
def list_answers(
    field_type: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    limit: int = Query(default=200, le=500, ge=1),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AnswerOut]:
    service = AnswerLibraryService(db, current_user.id)
    return service.list(field_type=field_type, tag=tag, limit=limit, offset=offset)  # type: ignore[return-value]


@router.post("", response_model=AnswerOut)
@router.post("/", response_model=AnswerOut)
def create_or_update_answer(
    payload: AnswerIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnswerOut:
    service = AnswerLibraryService(db, current_user.id)
    return service.upsert(  # type: ignore[return-value]
        question_text=payload.question_text,
        answer_text=payload.answer_text,
        field_type=payload.field_type,
        tags=getattr(payload, "tags", None),
        confidence=getattr(payload, "confidence", None),
        source=getattr(payload, "source", None) or "user",
    )


@router.get("/{answer_id}", response_model=AnswerOut)
def get_answer(
    answer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnswerOut:
    service = AnswerLibraryService(db, current_user.id)
    record = service.get(answer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Answer not found")
    return record  # type: ignore[return-value]


@router.put("/{answer_id}", response_model=AnswerOut)
def update_answer(
    answer_id: int,
    payload: AnswerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnswerOut:
    service = AnswerLibraryService(db, current_user.id)
    patch = payload.model_dump(exclude_unset=True)
    record = service.update(answer_id, patch)
    if record is None:
        raise HTTPException(status_code=404, detail="Answer not found")
    return record  # type: ignore[return-value]


@router.delete("/{answer_id}")
def delete_answer(
    answer_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, bool]:
    service = AnswerLibraryService(db, current_user.id)
    ok = service.delete(answer_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Answer not found")
    return {"ok": True}


@router.post("/lookup", response_model=AnswerLookupOut)
def lookup_answer(
    payload: AnswerLookupIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnswerLookupOut:
    service = AnswerLibraryService(db, current_user.id)
    return service.lookup(
        question_text=payload.question_text,
        field_type=getattr(payload, "field_type", None),
    )


@router.post("/seed", response_model=AnswerSeedResult)
def seed_answers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnswerSeedResult:
    service = AnswerLibraryService(db, current_user.id)
    return service.seed_common_questions()
