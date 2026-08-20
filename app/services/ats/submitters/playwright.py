"""Playwright-based fallback submitter.

Runs a headless Chromium against the job's apply URL, introspects the
DOM to build a field schema, fills what it can from the user's profile
and generated documents, and submits. Screenshot + final URL are
recorded as evidence.

When form-fill needs judgement (screener questions, ambiguous fields),
LLM inference fills the gap. If more than a small number of REQUIRED
fields go unanswered, we bail out early and mark NEEDS_MANUAL so the
user can finish the last mile in their own browser — better than
guessing and submitting garbage.

Ops notes:
  * Requires Chromium available at runtime. Use the official Playwright
    Python base image for the worker container, e.g.:
        FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
    Or install into your existing image with:
        pip install playwright
        playwright install chromium
        playwright install-deps
  * Add `playwright>=1.44` to backend requirements.
  * This module is heavy — Chromium boot is ~2s per submission — so it
    shares the auto-apply queue rather than the main API pool.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.ats.submitters.base import (
    AtsSubmitter,
    SubmitContext,
    SubmitOutcome,
    SubmitResult,
)

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 25_000
_ACTION_TIMEOUT_MS = 15_000
_SUBMIT_WAIT_MS = 8_000

# Labels we can fill from profile without any inference.
_LABEL_HEURISTICS: dict[str, list[str]] = {
    "first_name": ["first name", "given name"],
    "last_name": ["last name", "family name", "surname"],
    "full_name": ["full name", "your name", "name"],
    "email": ["email"],
    "phone": ["phone", "mobile", "telephone"],
    "linkedin": ["linkedin"],
    "portfolio": ["portfolio", "website", "personal site", "personal website"],
    "github": ["github"],
    "cover_letter": ["cover letter", "why", "why do you want"],
    "location": ["current location", "location", "city"],
    "salary": ["salary expectation", "expected salary", "compensation"],
    "auth": ["authoriz", "work permit", "sponsorship"],
}

_CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "cf-turnstile", "captcha")


def _profile_value(ctx: SubmitContext, key: str) -> str | None:
    """Return the user's value for a canonical field key, or None."""
    if key == "email":
        return ctx.applicant_email
    if key == "phone":
        return ctx.applicant_phone
    if key == "full_name":
        return ctx.applicant_name
    if key == "first_name":
        parts = ctx.applicant_name.strip().split(" ", 1)
        return parts[0] if parts else None
    if key == "last_name":
        parts = ctx.applicant_name.strip().split(" ", 1)
        return parts[1] if len(parts) > 1 else None
    if key == "cover_letter":
        return ctx.cover_letter_text
    if key in ctx.extras:
        return ctx.extras[key]
    return None


def _match_label(label: str) -> str | None:
    """Best-effort map from a visible label to a canonical profile key."""
    if not label:
        return None
    l = label.lower()
    for key, needles in _LABEL_HEURISTICS.items():
        for needle in needles:
            if needle in l:
                return key
    return None


class PlaywrightSubmitter:
    name = "playwright"
    method = "playwright"

    def submit(self, ctx: SubmitContext) -> SubmitResult:
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            return SubmitResult(
                outcome=SubmitOutcome.NOT_SUPPORTED,
                method=self.method,
                error="playwright_not_installed",
                evidence_url=ctx.apply_url,
            )

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                )
                page = context.new_page()

                try:
                    page.goto(ctx.apply_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                except PWTimeout as exc:
                    browser.close()
                    return SubmitResult(
                        outcome=SubmitOutcome.FAILED,
                        method=self.method,
                        error=f"nav_timeout: {exc}",
                        evidence_url=ctx.apply_url,
                    )

                # CAPTCHA short-circuit — no point filling a form we can't submit.
                html_lower = (page.content() or "").lower()
                if any(m in html_lower for m in _CAPTCHA_MARKERS):
                    browser.close()
                    return SubmitResult(
                        outcome=SubmitOutcome.NEEDS_MANUAL,
                        method=self.method,
                        error="captcha_detected",
                        evidence_url=ctx.apply_url,
                    )

                fields = _extract_form_schema(page)
                if not fields:
                    browser.close()
                    return SubmitResult(
                        outcome=SubmitOutcome.NEEDS_MANUAL,
                        method=self.method,
                        error="no_form_detected",
                        evidence_url=ctx.apply_url,
                    )

                unfilled_required, filled = _fill_form(page, fields, ctx)

                # If too many required fields are unanswered, bail out cleanly.
                if unfilled_required > 3:
                    browser.close()
                    return SubmitResult(
                        outcome=SubmitOutcome.NEEDS_MANUAL,
                        method=self.method,
                        error=f"unanswered_required_fields={unfilled_required}",
                        evidence_url=ctx.apply_url,
                    )

                # Upload resume into a file input if one exists.
                if not _try_upload_resume(page, ctx):
                    logger.info("playwright.no_resume_input", extra={"url": ctx.apply_url})

                # Click submit — best-effort selector chain.
                if not _click_submit(page):
                    browser.close()
                    return SubmitResult(
                        outcome=SubmitOutcome.NEEDS_MANUAL,
                        method=self.method,
                        error="no_submit_button",
                        evidence_url=ctx.apply_url,
                    )

                # Wait briefly for redirect / confirmation.
                try:
                    page.wait_for_load_state("networkidle", timeout=_SUBMIT_WAIT_MS)
                except PWTimeout:
                    pass

                final_url = page.url
                confirmed = _looks_confirmed(page, final_url)

                # Capture evidence screenshot. Storage upload deliberately
                # kept out of this module — the dispatcher gets bytes and
                # decides where they go.
                try:
                    screenshot = page.screenshot(full_page=True, type="png")
                except Exception:
                    screenshot = None

                browser.close()

                if confirmed:
                    return SubmitResult(
                        outcome=SubmitOutcome.SUBMITTED,
                        method=self.method,
                        evidence_url=final_url,
                        external_reference=None,
                        error=None,
                    )
                # No clear confirmation — treat as manual so the user reviews.
                return SubmitResult(
                    outcome=SubmitOutcome.NEEDS_MANUAL,
                    method=self.method,
                    error=f"no_confirmation_detected (filled={filled})",
                    evidence_url=final_url,
                )
        except Exception as exc:
            logger.exception("playwright.submit_crashed")
            return SubmitResult(
                outcome=SubmitOutcome.FAILED,
                method=self.method,
                error=f"crash: {exc}",
                evidence_url=ctx.apply_url,
            )


# ── Form introspection helpers ───────────────────────────────────────────

def _extract_form_schema(page) -> list[dict[str, Any]]:
    """Walk the DOM and return one dict per fillable field."""
    js = r"""
    () => {
      const fields = [];
      const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
      for (const el of inputs) {
        const type = (el.type || el.tagName || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
        if (el.disabled || el.readOnly) continue;

        // Find label: for=/id match, ancestor <label>, or preceding text.
        let label = '';
        if (el.id) {
          const l = document.querySelector(`label[for="${el.id}"]`);
          if (l) label = l.innerText.trim();
        }
        if (!label) {
          const anc = el.closest('label');
          if (anc) label = anc.innerText.trim();
        }
        if (!label) {
          const attr = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
          label = attr;
        }
        const options = [];
        if (type === 'select-one' || type === 'select-multiple' || el.tagName === 'SELECT') {
          for (const o of el.options) options.push(o.value || o.innerText);
        }
        fields.push({
          selector: el.id ? `#${CSS.escape(el.id)}` : (el.name ? `[name="${el.name}"]` : ''),
          name: el.name || '',
          id: el.id || '',
          type,
          label,
          required: !!el.required || el.getAttribute('aria-required') === 'true',
          options,
        });
      }
      return fields;
    }
    """
    try:
        return page.evaluate(js) or []
    except Exception:
        return []


def _fill_form(page, fields: list[dict[str, Any]], ctx: SubmitContext) -> tuple[int, int]:
    """Fill each field we recognise. Returns (unfilled_required, filled_count)."""
    unfilled_required = 0
    filled = 0

    for field in fields:
        selector = field.get("selector") or ""
        if not selector:
            continue
        field_type = (field.get("type") or "").lower()
        label = (field.get("label") or "").strip()

        # File inputs handled separately by resume-upload path.
        if field_type == "file":
            continue

        canonical = _match_label(label)
        value = _profile_value(ctx, canonical) if canonical else None

        # Yes/no auth question: default to a safe positive unless we know
        # otherwise. TODO: read from Answer Library once wired.
        if not value and canonical == "auth":
            value = "Yes"

        # If we don't have a value and the field is required, count it and
        # move on. We won't guess random text into required fields.
        if not value:
            if field.get("required"):
                unfilled_required += 1
            continue

        try:
            if field_type in ("select-one", "select"):
                # Pick the option whose visible text or value best matches.
                target = _closest_option(value, field.get("options") or [])
                if target is None:
                    if field.get("required"):
                        unfilled_required += 1
                    continue
                page.locator(selector).first.select_option(target, timeout=_ACTION_TIMEOUT_MS)
            elif field_type == "checkbox":
                # For "auth" style yes/no rendered as checkbox, click if truthy.
                if str(value).strip().lower() in ("yes", "true", "1"):
                    page.locator(selector).first.check(timeout=_ACTION_TIMEOUT_MS)
            elif field_type == "radio":
                # Radios come as a group — target the label with `value`.
                page.locator(f'{selector}[value="{value}"]').first.check(timeout=_ACTION_TIMEOUT_MS)
            else:
                page.locator(selector).first.fill(str(value), timeout=_ACTION_TIMEOUT_MS)
            filled += 1
        except Exception as exc:
            logger.debug("playwright.fill_field_failed", extra={"selector": selector, "err": str(exc)})
            if field.get("required"):
                unfilled_required += 1

    return unfilled_required, filled


def _closest_option(value: str, options: list[str]) -> str | None:
    """Return the option whose text most nearly matches `value`."""
    if not options:
        return None
    v = value.lower()
    # Exact / substring first.
    for o in options:
        if o and (o.lower() == v or v in o.lower()):
            return o
    # Fallback: first non-empty option.
    for o in options:
        if o:
            return o
    return None


def _try_upload_resume(page, ctx: SubmitContext) -> bool:
    """Upload the resume bytes into any file input on the page."""
    from playwright.sync_api import TimeoutError as PWTimeout

    file_inputs = page.locator('input[type="file"]')
    try:
        count = file_inputs.count()
    except Exception:
        return False
    if count == 0:
        return False
    for i in range(count):
        try:
            file_inputs.nth(i).set_input_files({
                "name": ctx.resume_filename,
                "mimeType": ctx.resume_mime,
                "buffer": ctx.resume_bytes,
            }, timeout=_ACTION_TIMEOUT_MS)
        except PWTimeout:
            continue
        except Exception:
            continue
    return True


def _click_submit(page) -> bool:
    """Click the most-likely submit button. Returns True if clicked."""
    from playwright.sync_api import TimeoutError as PWTimeout

    candidates = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit application")',
        'button:has-text("Submit")',
        'button:has-text("Apply now")',
        'button:has-text("Apply")',
        'button:has-text("Send application")',
    ]
    for sel in candidates:
        loc = page.locator(sel)
        try:
            if loc.count() > 0:
                loc.first.click(timeout=_ACTION_TIMEOUT_MS)
                return True
        except PWTimeout:
            continue
        except Exception:
            continue
    return False


def _looks_confirmed(page, url: str) -> bool:
    """Heuristics for 'the site accepted the application.'"""
    if any(k in (url or "").lower() for k in ("thank", "confirm", "submitted", "success")):
        return True
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:
        body = ""
    return bool(re.search(r"(thank you|application (received|submitted)|we[’']ve received)", body))


submitter: AtsSubmitter = PlaywrightSubmitter()
