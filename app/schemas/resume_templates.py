"""Pydantic schemas for the resume-template API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ConfigDict


class SectionConfig(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    label: str | None = Field(default=None, max_length=80)
    enabled: bool = True
    order: int = 0


class StylesConfig(BaseModel):
    font_family: str | None = Field(default=None, max_length=60)
    body_font_size: float | None = Field(default=None, ge=7, le=14)
    heading_font_size: float | None = Field(default=None, ge=8, le=20)
    line_height: float | None = Field(default=None, ge=1.0, le=2.0)
    section_spacing: float | None = Field(default=None, ge=0, le=40)
    margin_top: float | None = Field(default=None, ge=10, le=120)
    margin_bottom: float | None = Field(default=None, ge=10, le=120)
    margin_left: float | None = Field(default=None, ge=10, le=120)
    margin_right: float | None = Field(default=None, ge=10, le=120)
    accent_color: str | None = Field(default=None, max_length=8)
    header_style: str | None = Field(default=None, max_length=20)


class TemplateConfig(BaseModel):
    version: int = 1
    layout: str = "single_column"
    sections: list[SectionConfig] = Field(default_factory=list)
    styles: StylesConfig = Field(default_factory=StylesConfig)


class ResumeTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    name: str
    base_template: str
    is_system: bool
    config: dict[str, Any]
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class ResumeTemplateListOut(BaseModel):
    templates: list[ResumeTemplateOut]
    default_template_id: int | None


class CreateTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_template: str = Field(min_length=1, max_length=40)
    config: dict[str, Any] | None = None


class UpdateTemplateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict[str, Any] | None = None


class DuplicateTemplateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)


class SetDefaultRequest(BaseModel):
    template_id: int


class SetApplicationTemplateRequest(BaseModel):
    """PATCH body for /applications/{id}/template.

    Send `template_id: null` to detach and revert to the user's default.
    """
    template_id: int | None
