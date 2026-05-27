"""
Job source adapters — fetch raw postings from free public APIs.
Each adapter returns a list of dicts with normalized fields.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "ApplyForge-Crawler/1.0 (job search aggregator; contact: admin@applyforge.io)",
    "Accept": "application/json",
}


def _get(url: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ── RemoteOK ────────────────────────────────────────────────────────────────

def fetch_remoteok(keywords: list[str], work_type: str = "any") -> list[dict]:
    """
    RemoteOK public JSON API — all jobs are remote.
    https://remoteok.com/api
    """
    if work_type == "onsite":
        return []   # RemoteOK is remote-only
    try:
        data = _get("https://remoteok.com/api", timeout=20)
        if not isinstance(data, list):
            return []
        # First item is a legal notice dict — skip it
        jobs = [j for j in data if isinstance(j, dict) and "id" in j]
        results = []
        kw_lower = [k.lower() for k in keywords]
        for job in jobs:
            title = job.get("position", "")
            tags  = job.get("tags", []) or []
            desc  = job.get("description", "") or ""
            text  = f"{title} {' '.join(tags)} {desc}".lower()
            if not any(k in text for k in kw_lower):
                continue
            results.append({
                "source": "remoteok",
                "external_id": str(job.get("id", "")),
                "title": title,
                "company": job.get("company", "Unknown"),
                "location": "Remote",
                "work_type": "remote",
                "salary_range": _remoteok_salary(job),
                "description": _strip_html(desc)[:3000],
                "apply_url": job.get("url") or job.get("apply_url") or "",
                "tags": tags[:15],
            })
        return results
    except Exception as exc:
        logger.warning("remoteok_fetch_error: %s", exc)
        return []


def _remoteok_salary(job: dict) -> str | None:
    lo = job.get("salary_min")
    hi = job.get("salary_max")
    if lo and hi:
        return f"${lo:,} – ${hi:,}"
    if lo:
        return f"${lo:,}+"
    return None


# ── Arbeitnow ───────────────────────────────────────────────────────────────

def fetch_arbeitnow(keywords: list[str], work_type: str = "any", country: str | None = None) -> list[dict]:
    """
    Arbeitnow free job board API — global, mix of remote/onsite/hybrid.
    https://www.arbeitnow.com/api/job-board-api
    """
    try:
        url = "https://www.arbeitnow.com/api/job-board-api"
        data = _get(url, timeout=20)
        jobs = data.get("data", [])
        kw_lower = [k.lower() for k in keywords]

        results = []
        for job in jobs:
            title = job.get("title", "")
            desc  = job.get("description", "") or ""
            tags  = job.get("tags", []) or []
            text  = f"{title} {' '.join(tags)} {desc}".lower()

            if not any(k in text for k in kw_lower):
                continue

            # Work-type filter
            is_remote = job.get("remote", False)
            jtype = "remote" if is_remote else "onsite"
            if work_type == "remote" and not is_remote:
                continue
            if work_type == "onsite" and is_remote:
                continue

            results.append({
                "source": "arbeitnow",
                "external_id": job.get("slug", str(hash(job.get("url", "")))),
                "title": title,
                "company": job.get("company_name", "Unknown"),
                "location": job.get("location", ""),
                "work_type": jtype,
                "salary_range": None,
                "description": _strip_html(desc)[:3000],
                "apply_url": job.get("url", ""),
                "tags": tags[:15],
            })
        return results
    except Exception as exc:
        logger.warning("arbeitnow_fetch_error: %s", exc)
        return []


# ── Adzuna (optional, requires API key) ─────────────────────────────────────

def fetch_adzuna(
    keywords: list[str],
    country: str = "us",
    salary_min: int | None = None,
    work_type: str = "any",
    app_id: str | None = None,
    app_key: str | None = None,
) -> list[dict]:
    """
    Adzuna Jobs API — requires free API credentials.
    https://developer.adzuna.com/
    Set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env to enable.
    """
    if not app_id or not app_key:
        return []
    try:
        q = urllib.parse.quote(" ".join(keywords))
        country_code = (country or "us").lower()[:2]
        base = f"https://api.adzuna.com/v1/api/jobs/{country_code}/search/1"
        params = f"?app_id={app_id}&app_key={app_key}&results_per_page=50&what={q}&content-type=application/json"
        if salary_min:
            params += f"&salary_min={salary_min}"
        if work_type == "remote":
            params += "&title_only=remote"

        data = _get(base + params)
        results = []
        for job in data.get("results", []):
            salary_range = None
            lo = job.get("salary_min")
            hi = job.get("salary_max")
            if lo and hi:
                salary_range = f"{lo:,.0f} – {hi:,.0f}"
            results.append({
                "source": "adzuna",
                "external_id": str(job.get("id", "")),
                "title": job.get("title", ""),
                "company": job.get("company", {}).get("display_name", "Unknown"),
                "location": job.get("location", {}).get("display_name", ""),
                "work_type": work_type if work_type != "any" else None,
                "salary_range": salary_range,
                "description": _strip_html(job.get("description", ""))[:3000],
                "apply_url": job.get("redirect_url", ""),
                "tags": [],
            })
        return results
    except Exception as exc:
        logger.warning("adzuna_fetch_error: %s", exc)
        return []


# ── Utility ──────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    """Very lightweight HTML stripper — no external deps."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
