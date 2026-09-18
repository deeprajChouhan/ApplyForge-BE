"""
Resume-template CRUD + selection endpoints.

All endpoints require auth. Every query is scoped by user_id (system
templates are the only shared rows and are read-only).
"""
from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_feature
from app.db.session import get_db
from app.models.enums import FeatureFlag
from app.models.models import ResumeTemplate, User, UserProfile
from app.schemas.resume_templates import (
    CreateTemplateRequest,
    DuplicateTemplateRequest,
    ResumeTemplateListOut,
    ResumeTemplateOut,
    SetDefaultRequest,
    UpdateTemplateRequest,
)
from app.services.templates import ResumeTemplateService, normalize_config

router = APIRouter(prefix="/resume-templates", tags=["resume-templates"])

_need_resume = Depends(require_feature(FeatureFlag.resume))


def _to_out(tpl: ResumeTemplate, default_id: int | None) -> ResumeTemplateOut:
    try:
        cfg = json.loads(tpl.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        cfg = {}
    return ResumeTemplateOut(
        id=tpl.id,
        user_id=tpl.user_id,
        name=tpl.name,
        base_template=tpl.base_template,
        is_system=tpl.is_system,
        config=normalize_config(cfg),
        is_default=(default_id is not None and default_id == tpl.id),
        created_at=tpl.created_at,
        updated_at=tpl.updated_at,
    )


def _user_default_id(db: Session, user_id: int) -> int | None:
    profile = db.query(UserProfile).filter_by(user_id=user_id).first()
    return profile.default_resume_template_id if profile else None


@router.get("", response_model=ResumeTemplateListOut, dependencies=[_need_resume])
def list_templates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc = ResumeTemplateService(db, user.id)
    templates = svc.list()
    default_id = _user_default_id(db, user.id)
    return ResumeTemplateListOut(
        templates=[_to_out(t, default_id) for t in templates],
        default_template_id=default_id,
    )


@router.get("/{template_id}", response_model=ResumeTemplateOut,
            dependencies=[_need_resume])
def get_template(template_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    svc = ResumeTemplateService(db, user.id)
    tpl = svc.get(template_id)
    return _to_out(tpl, _user_default_id(db, user.id))


@router.post("", response_model=ResumeTemplateOut, status_code=status.HTTP_201_CREATED,
             dependencies=[_need_resume])
def create_template(payload: CreateTemplateRequest,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    svc = ResumeTemplateService(db, user.id)
    tpl = svc.create(payload.name, payload.base_template, payload.config)
    return _to_out(tpl, _user_default_id(db, user.id))


@router.patch("/{template_id}", response_model=ResumeTemplateOut,
              dependencies=[_need_resume])
def update_template(template_id: int, payload: UpdateTemplateRequest,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    svc = ResumeTemplateService(db, user.id)
    tpl = svc.update(template_id, name=payload.name, config=payload.config)
    return _to_out(tpl, _user_default_id(db, user.id))


@router.post("/{template_id}/duplicate", response_model=ResumeTemplateOut,
             status_code=status.HTTP_201_CREATED, dependencies=[_need_resume])
def duplicate_template(template_id: int, payload: DuplicateTemplateRequest,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    svc = ResumeTemplateService(db, user.id)
    tpl = svc.duplicate(template_id, payload.name)
    return _to_out(tpl, _user_default_id(db, user.id))


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[_need_resume])
def delete_template(template_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    ResumeTemplateService(db, user.id).delete(template_id)
    return None


@router.post("/default", response_model=ResumeTemplateOut,
             dependencies=[_need_resume])
def set_default(payload: SetDefaultRequest,
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    svc = ResumeTemplateService(db, user.id)
    tpl = svc.set_user_default(payload.template_id)
    return _to_out(tpl, _user_default_id(db, user.id))
