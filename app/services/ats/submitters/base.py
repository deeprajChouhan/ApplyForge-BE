"""Base types + Protocol for ATS submitters.

Every submitter accepts a fully-prepared `SubmitContext` (the JD, the
user's contact info, resume bytes + filename, generated cover letter
text) and returns a `SubmitResult` describing what happened.

Submitters do NOT touch the DB. The dispatcher wraps the call, then
updates `JobApplication.auto_apply_stage`, `submit_method`, etc. based
on the result. That keeps submitters testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol


class SubmitOutcome(str, Enum):
    SUBMITTED = "submitted"                # applied successfully
    NEEDS_MANUAL = "needs_manual"          # can't submit programmatically (captcha, extra fields)
    NOT_SUPPORTED = "not_supported"        # this provider has no HTTP submitter yet
    FAILED = "failed"                      # transient / unexpected error


@dataclass
class SubmitContext:
    apply_url: str
    ats_provider: str
    ats_external_id: str         # posting id on the provider (e.g. Lever posting UUID)
    ats_company_slug: str        # board slug (e.g. "lyft" on jobs.lever.co/lyft)
    applicant_name: str
    applicant_email: str
    applicant_phone: Optional[str]
    resume_bytes: bytes
    resume_filename: str
    resume_mime: str             # e.g. "application/pdf"
    cover_letter_text: Optional[str]
    # Free-form extra fields the submitter might use (LinkedIn URL, portfolio, etc.).
    extras: dict[str, str]


@dataclass
class SubmitResult:
    outcome: SubmitOutcome
    method: str                          # e.g. "lever_http", "greenhouse_http"
    evidence_url: Optional[str] = None   # URL to confirmation page / stored screenshot
    external_reference: Optional[str] = None  # ATS-side application id, if returned
    error: Optional[str] = None          # populated when outcome != SUBMITTED
    unfilled_questions: Optional[list[str]] = None  # list of question labels that were left unanswered


class AtsSubmitter(Protocol):
    """Marker Protocol every submitter implements."""

    name: str            # short id, e.g. "lever", "greenhouse"
    method: str          # goes into JobApplication.submit_method

    def submit(self, ctx: SubmitContext) -> SubmitResult: ...
