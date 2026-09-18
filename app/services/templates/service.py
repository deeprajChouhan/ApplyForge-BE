"""
ResumeTemplateService — CRUD scoped by user_id.

- Users can only read their own templates + system templates.
- Users can only write their own templates. Attempting to mutate a system
  template returns 403.
- Duplicating a system template creates a user-owned copy.
- Setting a per-application template updates JobApplication.resume_template_id.
- Setting the account default updates UserProfile.default_resume_template_id.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import (
    JobApplication,
    ResumeTemplate,
    UserProfile,
)
from .defaults import (
    ALLOWED_BASE_TEMPLATES,
    CLASSIC_KEY,
    SYSTEM_TEMPLATES,
    build_default_config,
    normalize_config,
)


class ResumeTemplateService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    # ── read ──────────────────────────────────────────────────────────

    def list(self) -> list[ResumeTemplate]:
        rows = (
            self.db.query(ResumeTemplate)
            .filter(ResumeTemplate.deleted_at.is_(None))
            .filter(
                (ResumeTemplate.is_system.is_(True))
                | (ResumeTemplate.user_id == self.user_id)
            )
            .order_by(ResumeTemplate.is_system.desc(), ResumeTemplate.id.asc())
            .all()
        )
        # If DB seed is missing (fresh test DB), lazily seed system templates.
        if not any(r.is_system for r in rows):
            self._seed_system_templates()
            rows = (
                self.db.query(ResumeTemplate)
                .filter(ResumeTemplate.deleted_at.is_(None))
                .filter(
                    (ResumeTemplate.is_system.is_(True))
                    | (ResumeTemplate.user_id == self.user_id)
                )
                .order_by(ResumeTemplate.is_system.desc(), ResumeTemplate.id.asc())
                .all()
            )
        return rows

    def get(self, template_id: int) -> ResumeTemplate:
        tpl = self.db.get(ResumeTemplate, template_id)
        if tpl is None or tpl.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Template not found")
        if not tpl.is_system and tpl.user_id != self.user_id:
            # Do not disclose existence of another user's template.
            raise HTTPException(status_code=404, detail="Template not found")
        return tpl

    # ── write ─────────────────────────────────────────────────────────

    def create(self, name: str, base_template: str,
               config: dict[str, Any] | None = None) -> ResumeTemplate:
        base = self._validate_base(base_template)
        cfg = normalize_config(config or build_default_config())
        tpl = ResumeTemplate(
            user_id=self.user_id,
            name=(name or "Untitled Template").strip()[:120],
            base_template=base,
            is_system=False,
            config_json=json.dumps(cfg),
        )
        self.db.add(tpl)
        self.db.commit()
        self.db.refresh(tpl)
        return tpl

    def update(self, template_id: int, *,
               name: str | None = None,
               config: dict[str, Any] | None = None) -> ResumeTemplate:
        tpl = self.get(template_id)
        if tpl.is_system:
            raise HTTPException(
                status_code=403,
                detail="System templates are read-only. Duplicate first, then edit.",
            )
        if name is not None:
            trimmed = name.strip()
            if trimmed:
                tpl.name = trimmed[:120]
        if config is not None:
            tpl.config_json = json.dumps(normalize_config(config))
        self.db.commit()
        self.db.refresh(tpl)
        return tpl

    def duplicate(self, template_id: int, new_name: str | None = None) -> ResumeTemplate:
        src = self.get(template_id)
        try:
            cfg = normalize_config(json.loads(src.config_json or "{}"))
        except (json.JSONDecodeError, TypeError):
            cfg = build_default_config()
        name = (new_name or f"{src.name} (Copy)").strip()[:120]
        dup = ResumeTemplate(
            user_id=self.user_id,
            name=name,
            base_template=src.base_template,
            is_system=False,
            config_json=json.dumps(cfg),
        )
        self.db.add(dup)
        self.db.commit()
        self.db.refresh(dup)
        return dup

    def delete(self, template_id: int) -> None:
        tpl = self.get(template_id)
        if tpl.is_system:
            raise HTTPException(
                status_code=403,
                detail="System templates cannot be deleted.",
            )
        # If any application/user default still points at this template,
        # clear the FK first (ON DELETE SET NULL handles it in prod, but we
        # also fall back to Classic here so subsequent reads have a sane
        # default without extra network calls).
        (self.db.query(UserProfile)
            .filter(UserProfile.user_id == self.user_id,
                    UserProfile.default_resume_template_id == template_id)
            .update({UserProfile.default_resume_template_id: None}))
        (self.db.query(JobApplication)
            .filter(JobApplication.user_id == self.user_id,
                    JobApplication.resume_template_id == template_id)
            .update({JobApplication.resume_template_id: None}))
        # Soft-delete so historical GeneratedDocument snapshots remain
        # readable (their FK is nullable and will read as detached).
        from datetime import datetime
        tpl.deleted_at = datetime.utcnow()
        self.db.commit()

    # ── selection ─────────────────────────────────────────────────────

    def set_user_default(self, template_id: int) -> ResumeTemplate:
        tpl = self.get(template_id)  # 404 or 403-hidden if not accessible
        profile = (
            self.db.query(UserProfile).filter_by(user_id=self.user_id).first()
        )
        if profile is None:
            profile = UserProfile(user_id=self.user_id)
            self.db.add(profile)
            self.db.flush()
        profile.default_resume_template_id = tpl.id
        self.db.commit()
        return tpl

    def set_application_template(self, app_id: int,
                                 template_id: int | None) -> JobApplication:
        app = (
            self.db.query(JobApplication)
            .filter(JobApplication.id == app_id,
                    JobApplication.user_id == self.user_id)
            .first()
        )
        if app is None:
            raise HTTPException(status_code=404, detail="Application not found")
        if template_id is None:
            app.resume_template_id = None
        else:
            tpl = self.get(template_id)  # ownership-checked
            app.resume_template_id = tpl.id
        self.db.commit()
        self.db.refresh(app)
        return app

    # ── helpers ───────────────────────────────────────────────────────

    def _validate_base(self, base_template: str) -> str:
        if base_template not in ALLOWED_BASE_TEMPLATES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown base_template '{base_template}'.",
            )
        return base_template

    def _seed_system_templates(self) -> None:
        """Lazily insert the four system templates on a fresh DB."""
        for seed in SYSTEM_TEMPLATES:
            existing = (
                self.db.query(ResumeTemplate)
                .filter(ResumeTemplate.is_system.is_(True),
                        ResumeTemplate.base_template == seed["base_template"],
                        ResumeTemplate.deleted_at.is_(None))
                .first()
            )
            if existing is None:
                self.db.add(ResumeTemplate(
                    user_id=None,
                    name=seed["name"],
                    base_template=seed["base_template"],
                    is_system=True,
                    config_json=json.dumps(normalize_config(seed["config"])),
                ))
        self.db.commit()
