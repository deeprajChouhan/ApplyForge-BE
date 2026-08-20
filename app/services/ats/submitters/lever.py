"""Lever public-apply HTTP submitter.

Lever exposes a public form-submit endpoint per posting at
    POST https://api.lever.co/v0/postings/{site}/{posting_id}?key=... (API key)
    POST https://jobs.lever.co/api/postings/{site}/{posting_id}/apply (public form)

The public form endpoint (used by the actual `jobs.lever.co` page) accepts
multipart/form-data with `resume`, `name`, `email`, `phone`, plus per-posting
custom questions. No API key required. This submitter uses that endpoint.

Not every Lever posting accepts external submission — some route to an
external ATS or require additional custom fields we don't collect yet.
If the POST returns a 4xx we mark `NEEDS_MANUAL` and preserve the
apply URL so the user can finish it themselves.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.services.ats.submitters.base import (
    AtsSubmitter,
    SubmitContext,
    SubmitOutcome,
    SubmitResult,
)

logger = logging.getLogger(__name__)

_LEVER_SUBMIT_URL = "https://jobs.lever.co/api/postings/{site}/{posting_id}/apply"
_TIMEOUT_SECONDS = 25.0


class LeverSubmitter:
    name = "lever"
    method = "lever_http"

    def submit(self, ctx: SubmitContext) -> SubmitResult:
        url = _LEVER_SUBMIT_URL.format(
            site=ctx.ats_company_slug,
            posting_id=ctx.ats_external_id,
        )

        # Split "First Last" for Lever's form fields. Lever accepts a single
        # `name` field on most postings; if the posting requires split fields
        # the endpoint returns 4xx and we fall back to NEEDS_MANUAL.
        name_parts = ctx.applicant_name.strip().split(" ", 1)
        first = name_parts[0] if name_parts else ""
        last = name_parts[1] if len(name_parts) > 1 else ""

        data = {
            "name": ctx.applicant_name,
            "email": ctx.applicant_email,
            "phone": ctx.applicant_phone or "",
            "firstName": first,
            "lastName": last,
        }
        if ctx.cover_letter_text:
            # Lever's cover-letter field key varies by posting; sending the
            # common ones and letting the server ignore extras is safe.
            data["coverLetter"] = ctx.cover_letter_text
            data["comments"] = ctx.cover_letter_text

        # Optional extras (LinkedIn, portfolio) — same tolerance rule.
        for k in ("linkedin", "portfolio", "github", "website"):
            if k in ctx.extras:
                data[k] = ctx.extras[k]
                data[f"urls[{k}]"] = ctx.extras[k]

        files = {
            "resume": (ctx.resume_filename, ctx.resume_bytes, ctx.resume_mime),
        }

        try:
            with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
                r = client.post(url, data=data, files=files)
        except httpx.HTTPError as exc:
            logger.warning("lever_submit.http_error", extra={"url": url, "error": str(exc)})
            return SubmitResult(
                outcome=SubmitOutcome.FAILED,
                method=self.method,
                error=f"http_error: {exc}",
            )

        if 200 <= r.status_code < 300:
            # Lever typically returns a JSON payload with `applicationId` on
            # success. Best-effort parse; not fatal if the shape changes.
            external_ref: Optional[str] = None
            try:
                body = r.json()
                external_ref = body.get("applicationId") or body.get("id")
            except ValueError:
                pass
            return SubmitResult(
                outcome=SubmitOutcome.SUBMITTED,
                method=self.method,
                external_reference=external_ref,
                evidence_url=ctx.apply_url,
            )

        # 4xx typically means the posting has custom fields we didn't supply,
        # or is routed to an external ATS. Preserve the URL for the user.
        if 400 <= r.status_code < 500:
            snippet = (r.text or "")[:200]
            logger.info("lever_submit.needs_manual", extra={"status": r.status_code, "body": snippet})
            return SubmitResult(
                outcome=SubmitOutcome.NEEDS_MANUAL,
                method=self.method,
                error=f"http_{r.status_code}: {snippet}",
                evidence_url=ctx.apply_url,
            )

        # 5xx — transient. Dispatcher can retry on the next tick.
        return SubmitResult(
            outcome=SubmitOutcome.FAILED,
            method=self.method,
            error=f"http_{r.status_code}",
        )


submitter: AtsSubmitter = LeverSubmitter()
