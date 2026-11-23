from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TailoringOut(BaseModel):
    """Evidence-mapped ATS tailoring for one application (see app/services/resume_tailoring)."""
    application_id: int
    enabled: bool
    source_resume_id: int | None = None
    generated_document_id: int | None = None
    # Overlay: {headline, summary, skills[], experiences[{ref, role, company, description}],
    #           projects[{ref, name, description}]}
    tailored: dict[str, Any]
    # Keyword map: {coverage{before,after}, terms[], gaps[], warnings[], hallucination_flags[], source{...}}
    report: dict[str, Any]
    is_stale: bool = False
    stale_reasons: list[str] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TailoringToggleRequest(BaseModel):
    enabled: bool
