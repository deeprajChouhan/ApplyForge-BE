"""Greenhouse Boards HTTP submitter.

Greenhouse's public Job Board API supports application submission when
the board is configured for external submission:
    POST https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}
Requires HTTP Basic auth with the board's per-company API key. Many
Greenhouse boards do NOT expose this key publicly — for those we return
NEEDS_MANUAL so the user can finish via the apply URL.

To enable submission for a specific company, add its Greenhouse Boards
API key to `GREENHOUSE_BOARD_KEYS[<slug>]` (env-driven map). We ship
with an empty dict so nothing surprises users on day one.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import httpx

from app.services.ats.submitters.base import (
    AtsSubmitter,
    SubmitContext,
    SubmitOutcome,
    SubmitResult,
)

logger = logging.getLogger(__name__)

_SUBMIT_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
_TIMEOUT_SECONDS = 25.0


def _load_board_keys() -> dict[str, str]:
    """Read per-board API keys from env: GH_BOARD_KEY_<SLUG>=<key>.

    Example: `GH_BOARD_KEY_STRIPE=abc...` enables submission for the
    `stripe` Greenhouse board. Case-insensitive slug lookup on read.
    """
    keys: dict[str, str] = {}
    for k, v in os.environ.items():
        if k.startswith("GH_BOARD_KEY_") and v:
            keys[k[len("GH_BOARD_KEY_"):].lower()] = v
    return keys


class GreenhouseSubmitter:
    name = "greenhouse"
    method = "greenhouse_http"

    def submit(self, ctx: SubmitContext) -> SubmitResult:
        board_keys = _load_board_keys()
        api_key = board_keys.get(ctx.ats_company_slug.lower())
        if not api_key:
            # No API key configured for this board — signal NOT_SUPPORTED
            # so the dispatcher falls through to the Playwright submitter,
            # which can drive the public Greenhouse form directly.
            return SubmitResult(
                outcome=SubmitOutcome.NOT_SUPPORTED,
                method=self.method,
                error="no_board_api_key_configured",
                evidence_url=ctx.apply_url,
            )

        url = _SUBMIT_URL.format(slug=ctx.ats_company_slug, job_id=ctx.ats_external_id)

        # Split "First Last" — Greenhouse requires both.
        name_parts = ctx.applicant_name.strip().split(" ", 1)
        first = name_parts[0] if name_parts else ""
        last = name_parts[1] if len(name_parts) > 1 else "-"

        # Greenhouse expects multipart with `first_name`, `last_name`, `email`
        # (all required) + optional `phone`, `resume_content` (base64) or
        # `resume` (file), and any custom question keys returned by GET on
        # the same URL. We send the base minimum + resume file + cover letter.
        data = {
            "first_name": first,
            "last_name": last,
            "email": ctx.applicant_email,
        }
        if ctx.applicant_phone:
            data["phone"] = ctx.applicant_phone
        if ctx.cover_letter_text:
            data["cover_letter_text"] = ctx.cover_letter_text

        files = {
            "resume": (ctx.resume_filename, ctx.resume_bytes, ctx.resume_mime),
        }

        # HTTP Basic auth: the board key is the username, password is blank.
        auth_token = base64.b64encode(f"{api_key}:".encode()).decode()
        headers = {"Authorization": f"Basic {auth_token}"}

        try:
            with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
                r = client.post(url, data=data, files=files, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("greenhouse_submit.http_error", extra={"url": url, "error": str(exc)})
            return SubmitResult(
                outcome=SubmitOutcome.FAILED,
                method=self.method,
                error=f"http_error: {exc}",
            )

        if 200 <= r.status_code < 300:
            external_ref: Optional[str] = None
            try:
                body = r.json()
                external_ref = str(body.get("application_id") or body.get("id") or "") or None
            except ValueError:
                pass
            return SubmitResult(
                outcome=SubmitOutcome.SUBMITTED,
                method=self.method,
                external_reference=external_ref,
                evidence_url=ctx.apply_url,
            )

        if r.status_code == 401:
            return SubmitResult(
                outcome=SubmitOutcome.NEEDS_MANUAL,
                method=self.method,
                error="board_api_key_rejected",
                evidence_url=ctx.apply_url,
            )
        if 400 <= r.status_code < 500:
            snippet = (r.text or "")[:200]
            return SubmitResult(
                outcome=SubmitOutcome.NEEDS_MANUAL,
                method=self.method,
                error=f"http_{r.status_code}: {snippet}",
                evidence_url=ctx.apply_url,
            )

        return SubmitResult(
            outcome=SubmitOutcome.FAILED,
            method=self.method,
            error=f"http_{r.status_code}",
        )


submitter: AtsSubmitter = GreenhouseSubmitter()
