from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import User
from app.schemas.kit import KitIn, KitOut, KitUpdate
from app.services.kit.service import KitService

router = APIRouter(prefix="/kits", tags=["kits"])


@router.get("/", response_model=list[KitOut])
def list_kits(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KitOut]:
    service = KitService(db, current_user.id)
    return service.list()


@router.post("/", response_model=KitOut, status_code=status.HTTP_201_CREATED)
def create_kit(
    payload: KitIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KitOut:
    service = KitService(db, current_user.id)
    try:
        return service.create(payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/default", response_model=KitOut)
def get_default_kit(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KitOut:
    service = KitService(db, current_user.id)
    kit = service.get_default()
    if kit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No default kit found")
    return kit


@router.get("/{kit_id}", response_model=KitOut)
def get_kit(
    kit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KitOut:
    service = KitService(db, current_user.id)
    kit = service.get(kit_id)
    if kit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kit not found")
    return kit


@router.put("/{kit_id}", response_model=KitOut)
def update_kit(
    kit_id: int,
    payload: KitUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KitOut:
    service = KitService(db, current_user.id)
    try:
        kit = service.update(kit_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if kit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kit not found")
    return kit


@router.delete("/{kit_id}")
def delete_kit(
    kit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, bool]:
    service = KitService(db, current_user.id)
    deleted = service.delete(kit_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kit not found")
    return {"ok": True}


@router.post("/{kit_id}/set-default", response_model=KitOut)
def set_default_kit(
    kit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KitOut:
    service = KitService(db, current_user.id)
    kit = service.update(kit_id, {"is_default": True})
    if kit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kit not found")
    return kit
