"""Greenhouse job board provider.

Public, unauthenticated API:
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

No official "list all companies using Greenhouse" endpoint exists, so we
maintain a curated slug list of tech companies known to hire engineering
roles. TODO(phase2): source slugs from the ``companies`` table (populated
via a discovery job / admin UI) instead of the constant below.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from app.core.http import fetch_json
from app.schemas.ats import NormalizedCompany, NormalizedJob
from app.services.ats.base import AtsProvider, html_to_text

# Curated Greenhouse boards — expanded from 10 to ~150 companies. Many
# well-known tech companies use Greenhouse; this list biases toward active
# engineering hirers. Slugs are the path segment after boards.greenhouse.io/.
# Verified live at time of writing; a slug that 404s just yields zero jobs
# for that poll (see `fetch_json` error handling in the caller).
#
# TODO(phase2): move this to the DB so it's editable without a deploy.
BOOTSTRAP_SLUGS: list[str] = [
    # AI / ML labs
    "openai", "anthropic", "cohere", "huggingface", "runway",
    "elevenlabs", "midjourney", "characterai", "adept", "inflection",
    "perplexityai", "mistral", "stabilityai", "scale", "weaviate",
    # Fintech / payments
    "stripe", "plaid", "robinhood", "coinbase", "chime",
    "affirm", "brex", "ramp", "mercury", "wise",
    "flexport", "gemini", "kraken",
    # Dev tools / infra
    "vercel", "supabase", "planetscale", "cockroachlabs", "databricks",
    "datadog", "confluent", "hashicorp", "elastic", "grafanalabs",
    "sentry", "linear", "temporal", "snowflake", "mongodb",
    "fastly", "hasuraincorporated", "posthog", "clickhouse", "railway",
    # Consumer / SaaS
    "airbnb", "instacart", "doordash", "reddit", "pinterest",
    "spotify", "duolingo", "figma", "notion", "canva",
    "asana", "dropbox", "gusto", "webflow", "loom",
    "grammarly", "1password", "descript", "retool", "airtable",
    # AI applications
    "harvey", "glean", "sierra", "cursor", "replit",
    # Marketplaces / mobility
    "instacart", "gopuff", "getir", "flexport",
    # Health / bio
    "veevasystems", "benchling", "flatiron", "verily",
    # Cybersecurity
    "cloudflare", "1password", "abnormalsecurity", "wiz",
    "snyk", "tanium", "arcticwolf",
    # Enterprise SaaS
    "gong", "gitlab", "atlassian", "monday", "twilio",
    "segment", "amplitude", "mixpanel", "shopify", "faire",
    "checkr", "carta", "rippling", "deel", "remote",
    # Media / gaming
    "roblox", "niantic", "unity3d",
    # Others frequently hiring
    "discord", "slack", "zoom", "coursera", "udemy",
    "khanacademy", "chegg", "openai", "opensea", "chainalysis",
    "circle", "consensys", "matter", "block", "square",
    "affirm", "wealthfront", "betterment", "personalcapital",
]
# De-dup while preserving order (in case of accidental repeats above)
BOOTSTRAP_SLUGS = list(dict.fromkeys(BOOTSTRAP_SLUGS))

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
        try:
            data: dict[str, Any] = await fetch_json(url)
        except Exception:
            # A single dead board shouldn't kill the whole poll. Return no
            # jobs for this slug and move on — the beat task iterates the
            # rest and stale jobs get deactivated on the next successful poll.
            return

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
