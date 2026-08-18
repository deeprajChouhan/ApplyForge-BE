"""Greenhouse job board provider.

Public, unauthenticated API:
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

No official "list all companies using Greenhouse" endpoint exists, so Phase 1
bootstraps from a small hardcoded slug list. TODO(phase2): source slugs from
the ``companies`` table (populated via a discovery job / admin UI) instead of
the constant below.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.core.http import fetch_json
from app.schemas.ats import NormalizedCompany, NormalizedJob
from app.services.ats.base import AtsProvider, html_to_text

# TODO(phase2): replace with DB-backed slug source.
BOOTSTRAP_SLUGS: list[str] = [
    "stripe",
    "airbnb",
    "notion",
    "figma",
    "openai",
    "anthropic",
    "robinhood",
    "coinbase",
    "plaid",
    "datadog",
]

_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Greenhouse timestamps look like "2024-05-01T12:34:56-04:00".
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class GreenhouseProvider(AtsProvider):
    name = "greenhouse"
    base_poll_interval_seconds = 3600
    can_submit = False  # TODO(phase2+): Greenhouse Job Board API supports POST /candidates for some boards.

    async def list_companies(self) -> AsyncIterator[NormalizedCompany]:
        for slug in BOOTSTRAP_SLUGS:
            yield NormalizedCompany(
                ats_provider=self.name,
                ats_slug=slug,
                name=slug,  # TODO(phase2): fetch/display real company name once known (board JSON doesn't include it).
                careers_url=f"https://boards.greenhouse.io/{slug}",
            )

    async def list_jobs(self, company: NormalizedCompany) -> AsyncIterator[NormalizedJob]:
        url = _BOARD_URL.format(slug=company.ats_slug)
        data: dict[str, Any] = await fetch_json(url)

        for job in data.get("jobs", []):
            content_html: str | None = job.get("content")
            location: dict[str, Any] | None = job.get("location") or {}

            yield NormalizedJob(
                ats_provider=self.name,
                external_id=str(job["id"]),
                title=job.get("title", ""),
                location=location.get("name"),
                remote_mode="unknown",  # TODO(phase2): infer from location/title text.
                employment_type=None,  # Not provided by Greenhouse board API.
                seniority=None,  # Not provided by Greenhouse board API.
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description=html_to_text(content_html),
                description_html=content_html,
                apply_url=job.get("absolute_url", ""),
                company_ats_slug=company.ats_slug,
                company_name=company.name,
                posted_at=_parse_datetime(job.get("updated_at")),
            )
