"""
Job source adapters — fetch raw postings from free public APIs.

Sources:
  - RemoteOK   : free, remote-only, no key
  - Arbeitnow  : free, global, server-side keyword+location search, paginated
  - Jobicy     : free, remote jobs, tag-based
  - LinkedIn   : guest (unauthenticated) endpoint — best-effort, no key
  - Adzuna     : optional — requires free API key, aggregates Indeed + more

Country filtering is applied client-side for all sources using location text
matching so jobs from the wrong country are never surfaced.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ApplyForge-Crawler/1.0; +https://applyforge.pro)",
    "Accept": "application/json, text/html, */*",
}


def _get(url: str, timeout: int = 20, accept: str = "application/json") -> Any:
    headers = {**_HEADERS, "Accept": accept}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get_html(url: str, timeout: int = 20) -> str:
    headers = {**_HEADERS, "Accept": "text/html,application/xhtml+xml"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


# ── Country helpers ──────────────────────────────────────────────────────────

# Map of 2-letter country code → location keywords to match against job.location
_COUNTRY_KEYWORDS: dict[str, list[str]] = {
    "gb": ["united kingdom", "uk", "england", "scotland", "wales",
           "london", "manchester", "birmingham", "leeds", "glasgow",
           "edinburgh", "bristol", "sheffield", "liverpool", "newcastle",
           "cambridge", "oxford", "reading", "brighton", "coventry"],
    "us": ["united states", "usa", "new york", "san francisco",
           "los angeles", "chicago", "seattle", "boston", "austin",
           "denver", "nyc", "bay area", "washington dc"],
    "de": ["germany", "deutschland", "berlin", "munich", "münchen",
           "frankfurt", "hamburg", "cologne", "köln", "düsseldorf",
           "stuttgart", "dresden", "leipzig"],
    "fr": ["france", "paris", "lyon", "marseille", "toulouse", "nice", "bordeaux"],
    "ca": ["canada", "toronto", "vancouver", "montreal", "ottawa", "calgary"],
    "au": ["australia", "sydney", "melbourne", "brisbane", "perth", "adelaide"],
    "nl": ["netherlands", "amsterdam", "rotterdam", "the hague", "utrecht"],
    "sg": ["singapore"],
    "in": ["india", "bangalore", "bengaluru", "mumbai", "delhi",
           "hyderabad", "pune", "chennai", "kolkata"],
    "ie": ["ireland", "dublin", "cork"],
    "es": ["spain", "madrid", "barcelona", "valencia", "seville"],
    "it": ["italy", "milan", "milano", "rome", "roma", "turin"],
    "se": ["sweden", "stockholm", "gothenburg", "malmö"],
    "no": ["norway", "oslo", "bergen"],
    "dk": ["denmark", "copenhagen", "københavn"],
    "ch": ["switzerland", "zurich", "zürich", "geneva", "bern"],
    "pl": ["poland", "warsaw", "kraków", "wrocław"],
    "pt": ["portugal", "lisbon", "lisboa", "porto"],
    "ae": ["united arab emirates", "uae", "dubai", "abu dhabi"],
    "nz": ["new zealand", "auckland", "wellington"],
}

_COUNTRY_ALIASES: dict[str, str] = {
    "united kingdom": "gb", "uk": "gb", "great britain": "gb",
    "britain": "gb", "england": "gb",
    "united states": "us", "usa": "us", "america": "us",
    "germany": "de", "deutschland": "de",
    "france": "fr", "canada": "ca", "australia": "au",
    "netherlands": "nl", "holland": "nl", "singapore": "sg",
    "india": "in", "ireland": "ie", "spain": "es", "italy": "it",
    "sweden": "se", "norway": "no", "denmark": "dk",
    "switzerland": "ch", "poland": "pl", "portugal": "pt",
    "uae": "ae", "new zealand": "nz",
}

# For Arbeitnow location param: code → human-readable location string
_COUNTRY_LOCATION_NAME: dict[str, str] = {
    "gb": "United Kingdom", "us": "United States", "de": "Germany",
    "fr": "France", "ca": "Canada", "au": "Australia", "nl": "Netherlands",
    "sg": "Singapore", "in": "India", "ie": "Ireland", "es": "Spain",
    "it": "Italy", "se": "Sweden", "no": "Norway", "dk": "Denmark",
    "ch": "Switzerland", "pl": "Poland", "pt": "Portugal",
    "ae": "United Arab Emirates", "nz": "New Zealand",
}


def _normalize_country(country: str | None) -> str | None:
    """Normalize any country string → 2-letter code, or None."""
    if not country:
        return None
    c = country.lower().strip()
    if len(c) == 2 and c in _COUNTRY_KEYWORDS:
        return c
    return _COUNTRY_ALIASES.get(c)


def _location_ok(location: str | None, country_code: str | None) -> bool:
    """
    True if this job's location is compatible with the user's country filter.
    Remote / empty / 'worldwide' always pass (remote jobs apply everywhere).
    """
    if not country_code:
        return True
    if not location:
        return True
    loc_lower = location.lower().strip()
    if loc_lower in ("", "remote", "worldwide", "anywhere", "global"):
        return True
    # If "remote" appears anywhere in the location string, allow it
    if "remote" in loc_lower:
        return True
    keywords = _COUNTRY_KEYWORDS.get(country_code, [])
    return any(kw in loc_lower for kw in keywords)


# ── Keyword helpers ──────────────────────────────────────────────────────────

def _expand_keywords(keywords: list[str]) -> list[str]:
    """
    Expand multi-word keywords into individual words for loose matching.
    ["AI Engineer"] → ["ai engineer", "ai", "engineer"]
    """
    stopwords = {"and", "or", "the", "a", "an", "of", "for", "in", "at", "to", "with"}
    expanded: list[str] = []
    for kw in keywords:
        kw_lower = kw.lower().strip()
        if kw_lower and kw_lower not in expanded:
            expanded.append(kw_lower)
        for word in kw_lower.split():
            if word not in stopwords and len(word) > 2 and word not in expanded:
                expanded.append(word)
    return expanded


def _text_matches(text: str, expanded_kws: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in expanded_kws)


def _within_days(date_str: str | None, days: int = 3) -> bool:
    """Return True if date_str is within the last N days (or unknown → True)."""
    if not date_str:
        return True
    try:
        # Handle ISO format: "2024-05-01T12:00:00Z" or "2024-05-01"
        ds = date_str.rstrip("Z").split("+")[0]
        dt = datetime.fromisoformat(ds)
        now = datetime.now()
        return (now - dt).days <= days
    except Exception:
        return True  # If we can't parse, don't filter it out


def _unix_within_days(ts: int | float | None, days: int = 3) -> bool:
    """Return True if Unix timestamp is within the last N days."""
    if not ts:
        return True
    try:
        dt = datetime.fromtimestamp(float(ts))
        return (datetime.now() - dt).days <= days
    except Exception:
        return True


# ── RemoteOK ────────────────────────────────────────────────────────────────

def fetch_remoteok(keywords: list[str], work_type: str = "any",
                   country: str | None = None) -> list[dict]:
    """RemoteOK — remote-only, no key needed. https://remoteok.com/api"""
    if work_type == "onsite":
        return []

    expanded = _expand_keywords(keywords)
    country_code = _normalize_country(country)

    try:
        data = _get("https://remoteok.com/api", timeout=20)
        if not isinstance(data, list):
            return []
        jobs = [j for j in data if isinstance(j, dict) and "id" in j]
        results = []
        for job in jobs:
            # Only jobs posted in the last 7 days
            if not _unix_within_days(job.get("epoch"), days=7):
                continue
            title = job.get("position", "")
            tags  = job.get("tags", []) or []
            desc  = job.get("description", "") or ""
            text  = f"{title} {' '.join(tags)} {desc}"
            if not _text_matches(text, expanded):
                continue
            # RemoteOK jobs are remote — they pass country check unless user
            # specifically wants onsite only (already skipped above)
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
        logger.info("remoteok_fetch total=%d matched=%d", len(jobs), len(results))
        return results
    except Exception as exc:
        logger.warning("remoteok_fetch_error: %s", exc)
        return []


def _remoteok_salary(job: dict) -> str | None:
    lo, hi = job.get("salary_min"), job.get("salary_max")
    if lo and hi:
        return f"${lo:,} – ${hi:,}"
    if lo:
        return f"${lo:,}+"
    return None


# ── Arbeitnow ───────────────────────────────────────────────────────────────

def fetch_arbeitnow(keywords: list[str], work_type: str = "any",
                    country: str | None = None) -> list[dict]:
    """
    Arbeitnow — global job board, server-side keyword + location search.
    https://www.arbeitnow.com/api/job-board-api
    """
    expanded = _expand_keywords(keywords)
    country_code = _normalize_country(country)
    primary_q = urllib.parse.quote(keywords[0]) if keywords else ""

    # Map country code to Arbeitnow location string
    location_param = ""
    if country_code and country_code in _COUNTRY_LOCATION_NAME:
        location_param = "&location=" + urllib.parse.quote(_COUNTRY_LOCATION_NAME[country_code])

    results: list[dict] = []
    seen_slugs: set[str] = set()

    for page in range(1, 4):
        try:
            url = f"https://www.arbeitnow.com/api/job-board-api?page={page}"
            if primary_q:
                url += f"&q={primary_q}"
            url += location_param
            data = _get(url, timeout=20)
            jobs = data.get("data", [])
            if not jobs:
                break

            for job in jobs:
                slug = job.get("slug", str(hash(job.get("url", ""))))
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                title    = job.get("title", "")
                desc     = job.get("description", "") or ""
                tags     = job.get("tags", []) or []
                location = job.get("location", "")
                text     = f"{title} {' '.join(tags)} {desc}"

                # Only jobs posted in the last 7 days
                if not _within_days(job.get("published_at") or job.get("created_at"), days=7):
                    continue
                if not _text_matches(text, expanded):
                    continue
                if not _location_ok(location, country_code):
                    continue

                is_remote = job.get("remote", False)
                jtype = "remote" if is_remote else "onsite"
                if work_type == "remote" and not is_remote:
                    continue
                if work_type == "onsite" and is_remote:
                    continue

                results.append({
                    "source": "arbeitnow",
                    "external_id": slug,
                    "title": title,
                    "company": job.get("company_name", "Unknown"),
                    "location": location,
                    "work_type": jtype,
                    "salary_range": None,
                    "description": _strip_html(desc)[:3000],
                    "apply_url": job.get("url", ""),
                    "tags": tags[:15],
                })

        except Exception as exc:
            logger.warning("arbeitnow_fetch_error page=%d: %s", page, exc)
            break

    logger.info("arbeitnow_fetch matched=%d country=%s", len(results), country_code)
    return results


# ── Jobicy ──────────────────────────────────────────────────────────────────

def fetch_jobicy(keywords: list[str], work_type: str = "any",
                 country: str | None = None, count: int = 50) -> list[dict]:
    """Jobicy — free remote jobs API. https://jobicy.com/api/v2/remote-jobs"""
    if work_type == "onsite":
        return []

    results: list[dict] = []
    seen_ids: set[str] = set()

    for kw in keywords[:3]:
        try:
            tag = urllib.parse.quote(kw.lower())
            url = f"https://jobicy.com/api/v2/remote-jobs?count={count}&tag={tag}"
            data = _get(url, timeout=20)
            for job in data.get("jobs", []):
                jid = str(job.get("id", ""))
                if not jid or jid in seen_ids:
                    continue
                seen_ids.add(jid)
                apply_url = job.get("url", "")
                if not apply_url:
                    continue
                # Only jobs posted in the last 7 days
                if not _within_days(job.get("pubDate"), days=7):
                    continue
                results.append({
                    "source": "jobicy",
                    "external_id": jid,
                    "title": job.get("jobTitle", ""),
                    "company": job.get("companyName", "Unknown"),
                    "location": "Remote",
                    "work_type": "remote",
                    "salary_range": _jobicy_salary(job),
                    "description": _strip_html(job.get("jobDescription", ""))[:3000],
                    "apply_url": apply_url,
                    "tags": [str(job["jobIndustry"])][:15] if job.get("jobIndustry") and isinstance(job["jobIndustry"], str) else [],
                })
        except Exception as exc:
            logger.warning("jobicy_fetch_error kw=%s: %s", kw, exc)

    logger.info("jobicy_fetch matched=%d", len(results))
    return results


def _jobicy_salary(job: dict) -> str | None:
    lo = job.get("annualSalaryMin")
    hi = job.get("annualSalaryMax")
    currency = job.get("salaryCurrency", "USD")
    if lo and hi:
        return f"{currency} {int(lo):,} – {int(hi):,}"
    if lo:
        return f"{currency} {int(lo):,}+"
    return None


# ── LinkedIn (guest / unauthenticated) ───────────────────────────────────────

def _fetch_linkedin_description(job_id: str) -> str | None:
    """
    Fetch the full job description for a single LinkedIn posting.
    Uses the same guest API — no auth required.
    """
    try:
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
        html = _get_html(url, timeout=12)
        # Primary: show-more-less-html__markup div contains the full JD
        m = re.search(
            r'class="show-more-less-html__markup[^"]*">([\s\S]*?)</div>',
            html,
        )
        if m:
            return _strip_html(m.group(1))[:3000]
        # Fallback: description meta tag
        m = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html)
        if m:
            return m.group(1)[:3000]
    except Exception as exc:
        logger.debug("linkedin_desc_failed job_id=%s: %s", job_id, exc)
    return None


def fetch_linkedin(keywords: list[str], work_type: str = "any",
                   country: str | None = None) -> list[dict]:
    """
    LinkedIn guest jobs API — unauthenticated endpoint used by LinkedIn's own
    public job search page.  No API key required but subject to rate limiting.
    Returns HTML which we parse with regex (no bs4 dependency).
    Fetches full job descriptions for each result (up to 12 jobs, polite delay).
    """
    country_code = _normalize_country(country)
    location_str = _COUNTRY_LOCATION_NAME.get(country_code or "", "") if country_code else ""

    # LinkedIn f_WT filter: 2=remote, 1=onsite, 3=hybrid
    wt_map = {"remote": "2", "onsite": "1", "hybrid": "3"}
    wt_param = f"&f_WT={wt_map[work_type]}" if work_type in wt_map else ""

    results: list[dict] = []
    seen_ids: set[str] = set()

    for kw in keywords[:2]:  # limit keywords to avoid rate-limiting
        try:
            q = urllib.parse.quote(kw)
            loc = urllib.parse.quote(location_str) if location_str else ""
            url = (
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                f"?keywords={q}&location={loc}&start=0&count=25{wt_param}"
                "&f_TPR=r604800"  # posted within last 7 days
            )
            html = _get_html(url, timeout=25)

            # Parse job IDs from data-entity-urn attributes
            ids = re.findall(r'data-entity-urn="urn:li:jobPosting:(\d+)"', html)
            titles = re.findall(
                r'class="[^"]*base-search-card__title[^"]*"[^>]*>\s*([^<]+)\s*<', html)
            companies = re.findall(
                r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>\s*<[^>]+>\s*([^<]+)\s*<', html)
            locations = re.findall(
                r'class="[^"]*job-search-card__location[^"]*"[^>]*>\s*([^<]+)\s*<', html)

            for i, jid in enumerate(ids):
                if jid in seen_ids:
                    continue
                seen_ids.add(jid)

                title    = titles[i].strip()    if i < len(titles)    else "Unknown"
                company  = companies[i].strip() if i < len(companies) else "Unknown"
                location = locations[i].strip() if i < len(locations) else location_str

                if not _location_ok(location, country_code):
                    continue

                apply_url = f"https://www.linkedin.com/jobs/view/{jid}/"
                results.append({
                    "source": "linkedin",
                    "external_id": jid,
                    "title": title,
                    "company": company,
                    "location": location,
                    "work_type": work_type if work_type != "any" else None,
                    "salary_range": None,
                    "description": None,  # fetched below
                    "apply_url": apply_url,
                    "tags": [],
                })

        except Exception as exc:
            logger.warning("linkedin_fetch_error kw=%s: %s", kw, exc)

    # Fetch full descriptions (cap at 12 to stay under rate limits)
    fetched = 0
    for result in results:
        if fetched >= 12:
            break
        desc = _fetch_linkedin_description(result["external_id"])
        if desc:
            result["description"] = desc
        fetched += 1
        time.sleep(0.4)  # polite delay between requests

    logger.info("linkedin_fetch matched=%d descriptions_fetched=%d", len(results), fetched)
    return results


# ── Adzuna (optional, requires API key — aggregates Indeed + 10s of boards) ──

def fetch_adzuna(
    keywords: list[str],
    country: str = "us",
    salary_min: int | None = None,
    work_type: str = "any",
    app_id: str | None = None,
    app_key: str | None = None,
) -> list[dict]:
    """
    Adzuna Jobs API — aggregates Indeed, LinkedIn, and 100s of job boards.
    Free API key at https://developer.adzuna.com/
    Set ADZUNA_APP_ID and ADZUNA_APP_KEY in backend/.env to enable.
    """
    if not app_id or not app_key:
        return []

    country_code = _normalize_country(country) or "us"
    try:
        q = urllib.parse.quote(" ".join(keywords))
        base = f"https://api.adzuna.com/v1/api/jobs/{country_code}/search/1"
        params = (
            f"?app_id={app_id}&app_key={app_key}"
            f"&results_per_page=50&what={q}&content-type=application/json"
        )
        if salary_min:
            params += f"&salary_min={salary_min}"
        if work_type == "remote":
            params += "&title_only=remote"

        data = _get(base + params)
        results = []
        for job in data.get("results", []):
            lo, hi = job.get("salary_min"), job.get("salary_max")
            salary_range = f"{lo:,.0f} – {hi:,.0f}" if lo and hi else None
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
        logger.info("adzuna_fetch matched=%d", len(results))
        return results
    except Exception as exc:
        logger.warning("adzuna_fetch_error: %s", exc)
        return []


# ── Utility ──────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
