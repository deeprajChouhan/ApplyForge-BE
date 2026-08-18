"""Workable job board provider.

Workable's public job board data is less consistently documented than
Greenhouse/Lever/Ashby and the "clean" JSON API
(``https://apply.workable.com/api/v3/accounts/{slug}/jobs``) requires a POST
request with a JSON body (``{"query": "", "location": [], "department": []}``).

The shared ``fetch_json(url) -> dict`` helper only supports GET requests, so
Phase 1 uses the GET-able widget endpoint instead:

    https://{slug}.workable.com/spi/v3/jobs

TODO(phase2): if/when `app.core.http.fetch_json` grows POST support (method,
json body kwargs), switch to the `apply.workable.com/api/v3/accounts/{slug}/jobs`
endpoint, which returns richer + more reliably-shaped data and supports
pagination via `nextPage`.

TODO(phase2): the `/spi/v3/jobs` list response does not reliably include full
job descriptions for every account; a per-job detail fetch
(`https://{slug}.workable.com/spi/v3/jobs/{shortcode}`) may be required to
populate `description`/`description_html` completely. This is stubbed out
below (`_maybe_fetch_full_description`) but not called by default to avoid
N+1 requests until we've confirmed it's needed and rate-limit-safe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.core.http import fetch_json
from app.schemas.ats import NormalizedCompany, NormalizedJob
from app.services.ats.base import AtsProvider, html_to_text

# TODO(phase2): replace with DB-backed slug source.
BOOTSTRAP_SLUGS: list[str] = [
    "workable",
    "deliveroo",
]

_SPI_JOBS_URL = "https://{slug}.workable.com/spi/v3/jobs"
_SPI_JOB_DETAIL_URL = "https://{slug}.workable.com/spi/v3/jobs/{shortcode}"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _infer_remote_mode(job: dict[str, Any]) -> str:
    # TODO(phase2): confirm exact field name/shape; observed variants include
    # a top-level `telecommuting` bool and/or `location.telecommuting`.
    location = job.get("location") or {}
    if job.get("telecommuting") is True or location.get("telecommuting") is True:
        return "remote"
    return "unknown"


def _format_location(job: dict[str, Any]) -> str | None:
    location = job.get("location") or {}
    parts = [
        location.get("city"),
        location.get("region"),
        location.get("country"),
    ]
    formatted = ", ".join(p for p in parts if p)
    return formatted or None


async def _maybe_fetch_full_description(slug: str, shortcode: str | None) -> str | None:
    """Optional per-job detail fetch for a fuller description. Not called by
    default -- see module TODO. Left in place for a follow-up phase to wire
    up once we've validated rate limits."""
    if not shortcode:
        return None
    url = _SPI_JOB_DETAIL_URL.format(slug=slug, shortcode=shortcode)
    try:
        detail: dict[str, Any] = await fetch_json(url)
    except Exception:
        return None
    return detail.get("description")


class WorkableProvider(AtsProvider):
    name = "workable"
    base_poll_interval_seconds = 3600
    can_submit = False  # TODO(phase2+): Workable has an Application API for partners; needs per-org OAuth.

    async def list_companies(self) -> AsyncIterator[NormalizedCompany]:
        for slug in BOOTSTRAP_SLUGS:
            yield NormalizedCompany(
                ats_provider=self.name,
                ats_slug=slug,
                name=slug,  # TODO(phase2): the board response's top-level `name` field can override this.
                careers_url=f"https://apply.workable.com/{slug}/",
            )

    async def list_jobs(self, company: NormalizedCompany) -> AsyncIterator[NormalizedJob]:
        url = _SPI_JOBS_URL.format(slug=company.ats_slug)
        data: dict[str, Any] = await fetch_json(url)

        # TODO(phase2): confirm the exact key -- observed as "jobs" on the
        # widget endpoint; guard against a bare list response too.
        jobs: list[dict[str, Any]] = data.get("jobs", data) if isinstance(data, dict) else data

        for job in jobs:
            description_html: str | None = job.get("description")
            shortcode = job.get("shortcode") or job.get("code")

            yield NormalizedJob(
                ats_provider=self.name,
                external_id=str(shortcode or job.get("id") or job.get("title")),
                title=job.get("title", ""),
                location=_format_location(job),
                remote_mode=_infer_remote_mode(job),
                employment_type=job.get("employment_type"),
                seniority=None,  # Not provided by this endpoint.
                salary_min=None,  # TODO(phase2): Workable salary data (if published) lives in `salary_range`; unverified shape.
                salary_max=None,
                salary_currency=None,
                description=html_to_text(description_html) if description_html else "",
                description_html=description_html,
                apply_url=job.get("url") or job.get("shortlink", ""),
                company_ats_slug=company.ats_slug,
                company_name=data.get("name", company.name) if isinstance(data, dict) else company.name,
                posted_at=_parse_datetime(job.get("published_on") or job.get("created_at")),
            )
