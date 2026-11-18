"""Template resolution: admin override (ops_email_templates) → code default (catalog)."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.email import OpsEmailTemplate
from app.services.email.catalog import SYSTEM_TEMPLATES, TemplateDef


@dataclass
class TemplateSpec:
    key: str
    product: str
    category: str
    name: str
    subject: str
    body: str
    heading: str | None = None
    preheader: str | None = None
    cta_label: str | None = None
    cta_url: str | None = None
    blocks: list[str] = field(default_factory=list)
    is_system: bool = True
    is_active: bool = True
    overridden: bool = False


def _from_def(d: TemplateDef) -> TemplateSpec:
    return TemplateSpec(
        key=d.key, product=d.product, category=d.category, name=d.name, subject=d.subject, body=d.body,
        heading=d.heading, preheader=d.preheader, cta_label=d.cta_label, cta_url=d.cta_url,
        blocks=list(d.blocks), is_system=True,
    )


def resolve(db: Session, key: str) -> TemplateSpec | None:
    row = db.query(OpsEmailTemplate).filter(OpsEmailTemplate.key == key).first()
    base = SYSTEM_TEMPLATES.get(key)
    if row is None:
        return _from_def(base) if base else None
    return TemplateSpec(
        key=row.key,
        product=base.product if base else row.product,
        category=base.category if base else row.category,  # system categories are fixed
        name=row.name,
        subject=row.subject,
        body=row.body,
        heading=row.heading,
        preheader=row.preheader,
        cta_label=row.cta_label,
        cta_url=row.cta_url,
        blocks=list(row.blocks if row.blocks is not None else (base.blocks if base else [])),
        is_system=base is not None,
        is_active=row.is_active,
        overridden=base is not None,
    )


def list_all(db: Session) -> list[TemplateSpec]:
    rows = {r.key: r for r in db.query(OpsEmailTemplate).all()}
    out = [resolve(db, k) for k in SYSTEM_TEMPLATES]
    out += [resolve(db, k) for k in rows if k not in SYSTEM_TEMPLATES]
    return [s for s in out if s]
