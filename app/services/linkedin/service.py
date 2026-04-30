"""
LinkedIn Connections Service -- Phase 2 of Priority Score
=========================================================

Responsibilities
----------------
1. parse_linkedin_csv()        -- Parse LinkedIn's Connections CSV export into dicts.
2. CompanyMatcher              -- Fuzzy token-set (Jaccard) company name matcher.
3. LinkedInService
     .upsert_connections()     -- Bulk-upsert parsed rows into linkedin_connections.
     .get_connections_for_company()  -- Return matched connections for a company name.
     .refresh_all_priority_scores() -- Recompose priority_score for every application
                                       the user owns where JD analysis already exists.

LinkedIn CSV format (standard export)
--------------------------------------
LinkedIn prepends a few notice/comment lines before the actual CSV data.
The real header row starts with "First Name".  Columns we consume:

    First Name | Last Name | URL | Email Address | Company | Position | Connected On

Connected On date formats observed in the wild:
    "01 Jan 2024"   (LinkedIn's default)
    "2024-01-01"    (some regional exports)
    "January 1, 2024"
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import JobApplication, LinkedInConnection, Skill
from app.services.scoring.service import PriorityScorer


# ---------------------------------------------------------------------------
# Company name fuzzy matcher
# ---------------------------------------------------------------------------

# Common legal suffixes and generic words to ignore when comparing names
_STRIP_RE = re.compile(
    r"\b("
    r"inc\.?|llc\.?|ltd\.?|corp\.?|co\.?|company|limited|group|"
    r"holdings?|international|worldwide|technologies|technology|tech|"
    r"solutions?|services?|consulting|consultancy|partners?|associates?|"
    r"global|enterprises?|ventures?|studios?|labs?|ai|software|systems?"
    r")\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]")


def _tokenise(name: str) -> frozenset[str]:
    """
    Lowercase, strip punctuation and generic business suffixes, then return
    the remaining non-trivial tokens as a frozenset.
    """
    s = name.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _STRIP_RE.sub(" ", s)
    return frozenset(t for t in s.split() if len(t) > 1)


def fuzzy_company_match(name_a: str, name_b: str, threshold: float = 0.5) -> bool:
    """
    Jaccard similarity on token sets.  Returns True when the overlap is at
    least `threshold` of the union.

    Examples
    --------
    >>> fuzzy_company_match("Google LLC", "Google")          # True  (1/1 = 1.0)
    >>> fuzzy_company_match("Meta Platforms Inc", "Meta")    # True  (1/2 = 0.5)
    >>> fuzzy_company_match("Amazon", "Apple")               # False (0/2 = 0.0)
    >>> fuzzy_company_match("OpenAI", "Open Source Org")     # False (1/3 = 0.33)
    """
    tokens_a = _tokenise(name_a)
    tokens_b = _tokenise(name_b)
    if not tokens_a or not tokens_b:
        return False
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) >= threshold


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%d %b %Y",      # 01 Jan 2024  (LinkedIn default)
    "%Y-%m-%d",      # 2024-01-01
    "%m/%d/%Y",      # 01/15/2024
    "%B %d, %Y",     # January 15, 2024
    "%d/%m/%Y",      # 15/01/2024
    "%b %d, %Y",     # Jan 15, 2024
)


def _parse_date(value: str) -> date | None:
    """Try several date formats; return None if none match."""
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_linkedin_csv(raw_bytes: bytes) -> list[dict[str, Any]]:
    """
    Parse a raw LinkedIn Connections CSV export.

    LinkedIn prepends disclaimer/notice lines before the actual header row.
    We skip forward until we find the line containing "First Name", then
    hand the remainder to csv.DictReader.

    Returns a list of dicts with keys:
        full_name, company, position, connected_on (date | None)

    Raises
    ------
    ValueError
        If no header row is found (not a LinkedIn connections export).
    """
    # Decode -- LinkedIn exports are UTF-8; strip BOM if present
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()

    # Locate the real header row
    header_idx = next(
        (i for i, line in enumerate(lines) if "First Name" in line),
        None,
    )
    if header_idx is None:
        raise ValueError(
            "Could not find the CSV header row.  "
            "Please upload a LinkedIn 'Connections' export (the file should "
            "contain a 'First Name' column)."
        )

    csv_text = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_text))

    rows: list[dict[str, Any]] = []
    for row in reader:
        first = (row.get("First Name") or "").strip()
        last  = (row.get("Last Name")  or "").strip()
        full_name = f"{first} {last}".strip()
        if not full_name:
            continue  # skip blank rows

        rows.append({
            "full_name":    full_name,
            "company":      (row.get("Company")      or "").strip() or None,
            "position":     (row.get("Position")     or "").strip() or None,
            "connected_on": _parse_date(row.get("Connected On") or ""),
        })

    return rows


# ---------------------------------------------------------------------------
# LinkedIn service
# ---------------------------------------------------------------------------

class LinkedInService:
    """
    Service layer for LinkedIn connection ingestion and reachability scoring.

    All DB operations are scoped to the authenticated user via ``user_id``.
    """

    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def upsert_connections(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        """
        Bulk-upsert parsed connection rows for this user.

        Strategy: count existing rows before and after to separate
        genuine inserts from updates without relying on dialect-specific
        rowcount semantics.

        Returns
        -------
        dict with keys: imported (new rows), updated (overwritten rows), total.
        """
        if not rows:
            return {"imported": 0, "updated": 0, "total": 0}

        total = len(rows)
        before = (
            self.db.query(LinkedInConnection)
            .filter_by(user_id=self.user_id)
            .count()
        )

        # Upsert one-by-one using merge-style logic so we stay dialect-agnostic
        # (works with MySQL, SQLite for tests, etc.).
        # For large imports this is fine -- LinkedIn caps exports at ~30 000 rows
        # and the round-trip cost is negligible compared to HTTP overhead.
        for r in rows:
            existing = (
                self.db.query(LinkedInConnection)
                .filter_by(user_id=self.user_id, full_name=r["full_name"])
                .first()
            )
            if existing:
                existing.company      = r["company"]
                existing.position     = r["position"]
                existing.connected_on = r["connected_on"]
            else:
                self.db.add(
                    LinkedInConnection(
                        user_id=self.user_id,
                        full_name=r["full_name"],
                        company=r["company"],
                        position=r["position"],
                        connected_on=r["connected_on"],
                    )
                )

        self.db.commit()

        after = (
            self.db.query(LinkedInConnection)
            .filter_by(user_id=self.user_id)
            .count()
        )
        new_rows     = after - before
        updated_rows = total - new_rows

        return {
            "imported": new_rows,
            "updated":  max(updated_rows, 0),
            "total":    total,
        }

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_connections_for_company(self, company_name: str) -> list[LinkedInConnection]:
        """
        Return all of this user's connections whose stored company fuzzy-matches
        ``company_name``.  Connections without a company are excluded.
        """
        all_conns = (
            self.db.query(LinkedInConnection)
            .filter(
                LinkedInConnection.user_id == self.user_id,
                LinkedInConnection.company.isnot(None),
            )
            .all()
        )
        return [c for c in all_conns if fuzzy_company_match(c.company, company_name)]

    # ------------------------------------------------------------------
    # Priority score refresh
    # ------------------------------------------------------------------

    def refresh_all_priority_scores(self) -> int:
        """
        After a CSV import, walk every JobApplication owned by this user and
        recompose priority_score wherever fit_score and competition_score are
        already stored (i.e. the user has previously run JD analysis).

        Returns the count of applications that were touched.
        """
        apps = (
            self.db.query(JobApplication)
            .filter_by(user_id=self.user_id)
            .all()
        )

        updated = 0
        for app in apps:
            if app.fit_score is not None and app.competition_score is not None:
                composed = PriorityScorer.compose(
                    app.fit_score,
                    app.competition_score,
                )
                app.priority_score = composed["priority_score"]
                updated += 1

        if updated:
            self.db.commit()

        return updated
