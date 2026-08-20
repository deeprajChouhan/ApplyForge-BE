"""Lever job board provider.

Public, unauthenticated API:
    https://api.lever.co/v0/postings/{slug}?mode=json

Curated ~100-slug list, expanded from 7 to give the matcher a wide
enough pool to actually find relevant roles. A dead slug just yields
zero jobs (see fetch_json try/except below).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.core.http import fetch_json
from app.schemas.ats import NormalizedCompany, NormalizedJob
from app.services.ats.base import AtsProvider, html_to_text

# TODO(phase2): move to DB so it's editable without a deploy.
BOOTSTRAP_SLUGS: list[str] = [
    # Original seed
    "netflix", "mixpanel", "pinterest", "spotify", "github", "shopify", "medium",
    # AI / ML
    "openai", "cohere", "runway", "elevenlabs", "adept", "perplexity", "inflection",
    "characterai", "harvey", "sierra", "arcinstitute",
    # Fintech / payments
    "stripe", "plaid", "brex", "ramp", "mercury", "wealthfront", "affirm", "chime",
    "gemini", "coinbase", "kraken", "opensea", "circle", "checkout", "sardine",
    # Dev tools / infra
    "vercel", "supabase", "planetscale", "cockroachlabs", "databricks", "hashicorp",
    "sentry", "linear", "temporal", "railway", "grafanalabs", "confluent",
    "clickhouse", "posthog", "chroma", "modal", "prefect", "orb",
    # Consumer / SaaS
    "airbnb", "instacart", "doordash", "reddit", "spotify", "duolingo", "figma",
    "notion", "canva", "asana", "dropbox", "grammarly", "1password", "descript",
    "retool", "airtable", "loom", "attentive",
    # Enterprise
    "gong", "gitlab", "atlassian", "twilio", "segment", "amplitude", "mixpanel",
    "faire", "checkr", "carta", "rippling", "deel", "remote", "airbase",
    "workato", "fivetran", "hex", "airbyte",
    # Security
    "cloudflare", "wiz", "snyk", "1password", "abnormalsecurity", "tanium",
    "arcticwolf", "attackiq",
    # Health / bio
    "veevasystems", "benchling", "flatiron", "verily", "included", "sword",
    # Marketplaces / mobility
    "gopuff", "flexport", "faire",
    # Media / gaming
    "roblox", "unity3d", "niantic",
    # Communication
    "discord", "slack", "zoom",
    # Education
    "coursera", "udemy", "khanacademy",
]
BOOTSTRAP_SLUGS = list(dict.fromkeys(BOOTSTRAP_SLUGS))  # de-dupe, preserve order

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
    can_submit = True  # HTTP submitter exists — see app.services.ats.submitters.lever

    async def list_companies(self) -> AsyncIterator[NormalizedCompany]:
        for slug in BOOTSTRAP_SLUGS:
            yield NormalizedCompany(
                ats_provider=self.name,
                ats_slug=slug,
                name=slug,
                careers_url=f"https://jobs.lever.co/{slug}",
            )

    async def list_jobs(self, company: NormalizedCompany) -> AsyncIterator[NormalizedJob]:
        url = _BOARD_URL.format(slug=company.ats_slug)
        try:
            data: list[dict[str, Any]] = await fetch_json(url)
        except Exception:
            # Dead / 404 slug — skip cleanly so the batch continues.
            return

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
                seniority=None,
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description=description_plain or html_to_text(description_html),
                description_html=description_html,
                apply_url=posting.get("hostedUrl", ""),
                company_ats_slug=company.ats_slug,
                company_name=company.name,
                posted_at=_parse_epoch_ms(posting.get("createdAt")),
            )
