"""Lever job board provider.

Public, unauthenticated API:
    https://api.lever.co/v0/postings/{slug}?mode=json
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.core.http import fetch_json
from app.schemas.ats import NormalizedCompany, NormalizedJob
from app.services.ats.base import AtsProvider, html_to_text

# TODO(phase2): replace with DB-backed slug source.
BOOTSTRAP_SLUGS: list[str] = [
    "netflix",
    "mixpanel",
    "pinterest",
    "spotify",
    "github",
    "shopify",
    "medium",
]

_BOARD_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


def _parse_epoch_ms(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _infer_remote_mode(location: str | None) -> str:
    if location and "remote" in location.lower():
        return "remote"
    return "unknown"


class LeverProvider(AtsProvider):
    name = "lever"
    base_poll_interval_seconds = 3600
    can_submit = False  # TODO(phase2+): Lever "Postings API" supports opportunity creation on some plans.

    async def list_companies(self) -> AsyncIterator[NormalizedCompany]:
        for slug in BOOTSTRAP_SLUGS:
            yield NormalizedCompany(
                ats_provider=self.name,
                ats_slug=slug,
                name=slug,  # TODO(phase2): Lever postings API doesn't return a display company name.
                careers_url=f"https://jobs.lever.co/{slug}",
            )

    async def list_jobs(self, company: NormalizedCompany) -> AsyncIterator[NormalizedJob]:
        url = _BOARD_URL.format(slug=company.ats_slug)
        data: list[dict[str, Any]] = await fetch_json(url)

        for posting in data:
            categories: dict[str, Any] = posting.get("categories") or {}
            location: str | None = categories.get("location")
            description_html: str | None = posting.get("description")
            description_plain: str | None = posting.get("descriptionPlain")

            yield NormalizedJob(
                ats_provider=self.name,
                external_id=str(posting["id"]),
                title=posting.get("text", ""),
                location=location,
                remote_mode=_infer_remote_mode(location),
                employment_type=categories.get("commitment"),
                seniority=None,  # Not directly provided; categories.team is org unit, not seniority.
                salary_min=None,  # TODO(phase2): Lever exposes salary via `salaryRange` on some boards.
                salary_max=None,
                salary_currency=None,
                description=description_plain or html_to_text(description_html),
                description_html=description_html,
                apply_url=posting.get("hostedUrl", ""),
                company_ats_slug=company.ats_slug,
                company_name=company.name,
                posted_at=_parse_epoch_ms(posting.get("createdAt")),
            )
