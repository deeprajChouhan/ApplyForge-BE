"""Ashby job board provider.

Public, unauthenticated API:
    https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.core.http import fetch_json
from app.schemas.ats import NormalizedCompany, NormalizedJob
from app.services.ats.base import AtsProvider, html_to_text

# TODO(phase2): replace with DB-backed slug source.
BOOTSTRAP_SLUGS: list[str] = [
    "ashby",
    "linear",
    "posthog",
    "deel",
    "vercel",
    "railway",
]

_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

_WORKPLACE_TYPE_MAP = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_remote_mode(workplace_type: str | None) -> str:
    if not workplace_type:
        return "unknown"
    return _WORKPLACE_TYPE_MAP.get(workplace_type.strip().lower(), "unknown")


def _extract_compensation(job: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    """Best-effort extraction of salary range from Ashby's compensation block.

    Ashby's compensation payload shape varies by customer configuration, so
    this defensively walks `compensation.summaryComponents` /
    `compensationTier[0].summaryComponents` looking for the first component
    with min/max values. TODO(phase2): handle multiple tiers / currencies
    and equity-only postings more precisely.
    """
    compensation = job.get("compensation") or {}
    tiers = compensation.get("compensationTiers") or job.get("compensationTier") or []

    components: list[dict[str, Any]] = []
    if isinstance(tiers, list) and tiers:
        first_tier = tiers[0] or {}
        components = first_tier.get("summaryComponents") or []
    elif compensation.get("summaryComponents"):
        components = compensation["summaryComponents"]

    for component in components:
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        if min_value is not None or max_value is not None:
            return (
                int(min_value) if min_value is not None else None,
                int(max_value) if max_value is not None else None,
                currency,
            )

    return None, None, None


class AshbyProvider(AtsProvider):
    name = "ashby"
    base_poll_interval_seconds = 3600
    can_submit = False  # TODO(phase2+): Ashby has an application-submission API for partners; needs per-org auth.

    async def list_companies(self) -> AsyncIterator[NormalizedCompany]:
        for slug in BOOTSTRAP_SLUGS:
            yield NormalizedCompany(
                ats_provider=self.name,
                ats_slug=slug,
                name=slug,  # Overwritten below when we know it from the board payload (list_jobs), if needed.
                careers_url=f"https://jobs.ashbyhq.com/{slug}",
            )

    async def list_jobs(self, company: NormalizedCompany) -> AsyncIterator[NormalizedJob]:
        url = _BOARD_URL.format(slug=company.ats_slug)
        data: dict[str, Any] = await fetch_json(url)

        for job in data.get("jobs", []):
            description_html: str | None = job.get("descriptionHtml")
            description_plain: str | None = job.get("descriptionPlain")
            salary_min, salary_max, salary_currency = _extract_compensation(job)

            yield NormalizedJob(
                ats_provider=self.name,
                external_id=str(job["id"]),
                title=job.get("title", ""),
                location=job.get("locationName"),
                remote_mode=_normalize_remote_mode(job.get("workplaceType")),
                employment_type=job.get("employmentType"),
                seniority=None,  # Not provided directly by the job board API.
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency=salary_currency,
                description=description_plain or html_to_text(description_html),
                description_html=description_html,
                apply_url=job.get("jobUrl", ""),
                company_ats_slug=company.ats_slug,
                company_name=company.name,
                posted_at=_parse_datetime(job.get("publishedAt")),
            )
