"""
ResumeTemplateResolver
======================
Single point of resolution for "which template should be used for this
export / preview / generation?". Every renderer (preview, PDF, DOCX)
consumes the ResolvedTemplate this returns.

Resolution order (first wins):
  1. Application's own resume_template_id.
     For historical parity, also honours GeneratedDocument.resume_template_id
     of the latest non-deleted resume version when the app itself has none.
  2. UserProfile.default_resume_template_id.
  3. The system Classic ATS template (bootstrapped from SYSTEM_TEMPLATES if
     the DB seed has not run yet — e.g. in a fresh SQLite test DB).

The resolver ALSO enforces user_id scoping — a template that does not
belong to this user (and is not a system template) is treated as if it
did not exist, so users can never load or export with another user's
template even if a stale id is stored on their application.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import (
    JobApplication,
    GeneratedDocument,
    ResumeTemplate,
    UserProfile,
)
from app.models.enums import DocumentType
from .defaults import (
    CLASSIC_KEY,
    SYSTEM_TEMPLATES,
    build_default_config,
    normalize_config,
)


@dataclass(frozen=True)
class ResolvedTemplate:
    """
    The template a renderer will use. `id` may be None only when we fall
    back to an in-memory Classic ATS default (fresh DB, seed hasn't run).
    """
    id: int | None
    name: str
    base_template: str
    config: dict[str, Any]
    is_system: bool
    source: str  # 'application' | 'generated_document' | 'user_default' | 'classic_fallback'


def _accessible(template: ResumeTemplate | None, user_id: int) -> bool:
    if template is None or template.deleted_at is not None:
        return False
    return bool(template.is_system) or template.user_id == user_id


def _to_resolved(t: ResumeTemplate, source: str) -> ResolvedTemplate:
    try:
        raw = json.loads(t.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        raw = {}
    return ResolvedTemplate(
        id=t.id,
        name=t.name,
        base_template=t.base_template,
        config=normalize_config(raw),
        is_system=bool(t.is_system),
        source=source,
    )


class ResumeTemplateResolver:
    """
    Stateless helper — pass `db` and `user_id`. Every method returns a
    ResolvedTemplate whose `config` has been normalized.
    """

    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    # ── public API ─────────────────────────────────────────────────────

    def resolve_for_application(self, app_id: int) -> ResolvedTemplate:
        app = self.db.get(JobApplication, app_id)
        if app is None or app.user_id != self.user_id:
            # Do not leak existence; renderer callers should have verified
            # ownership already. Fall through to user default.
            return self.resolve_user_default()

        if app.resume_template_id:
            tpl = self.db.get(ResumeTemplate, app.resume_template_id)
            if _accessible(tpl, self.user_id):
                return _to_resolved(tpl, "application")

        # Fall back to the latest generated resume's snapshot if any.
        latest_doc = (
            self.db.query(GeneratedDocument)
            .filter(
                GeneratedDocument.application_id == app_id,
                GeneratedDocument.user_id == self.user_id,
                GeneratedDocument.doc_type == DocumentType.resume,
                GeneratedDocument.deleted_at.is_(None),
                GeneratedDocument.resume_template_id.is_not(None),
            )
            .order_by(GeneratedDocument.version.desc())
            .first()
        )
        if latest_doc and latest_doc.resume_template_id:
            tpl = self.db.get(ResumeTemplate, latest_doc.resume_template_id)
            if _accessible(tpl, self.user_id):
                return _to_resolved(tpl, "generated_document")

        return self.resolve_user_default()

    def resolve_user_default(self) -> ResolvedTemplate:
        profile = (
            self.db.query(UserProfile).filter_by(user_id=self.user_id).first()
        )
        if profile and profile.default_resume_template_id:
            tpl = self.db.get(ResumeTemplate, profile.default_resume_template_id)
            if _accessible(tpl, self.user_id):
                return _to_resolved(tpl, "user_default")
        return self.resolve_classic()

    def resolve_classic(self) -> ResolvedTemplate:
        tpl = (
            self.db.query(ResumeTemplate)
            .filter(
                ResumeTemplate.is_system.is_(True),
                ResumeTemplate.base_template == CLASSIC_KEY,
                ResumeTemplate.deleted_at.is_(None),
            )
            .first()
        )
        if tpl is not None:
            return _to_resolved(tpl, "classic_fallback")

        # DB has no seed yet — build an in-memory Classic template so
        # renderers still work on a fresh test SQLite DB.
        seed = next(t for t in SYSTEM_TEMPLATES if t["base_template"] == CLASSIC_KEY)
        return ResolvedTemplate(
            id=None,
            name=seed["name"],
            base_template=seed["base_template"],
            config=normalize_config(seed["config"]),
            is_system=True,
            source="classic_fallback",
        )

    def resolve_by_id(self, template_id: int) -> ResolvedTemplate:
        tpl = self.db.get(ResumeTemplate, template_id)
        if _accessible(tpl, self.user_id):
            return _to_resolved(tpl, "application")
        return self.resolve_user_default()
