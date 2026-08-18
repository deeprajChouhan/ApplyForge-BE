"""Abstract base class for ATS (Applicant Tracking System) job-board providers.

Each concrete provider (Greenhouse, Lever, Ashby, Workable, ...) implements
``list_companies`` and ``list_jobs`` as async generators that yield normalized
schema objects. No network I/O happens at import time -- providers only touch
the network when their async methods are awaited/iterated.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import AsyncIterator

from app.schemas.ats import NormalizedCompany, NormalizedJob

# Optional fast HTML->text conversion. Falls back to a regex-based strip if
# selectolax isn't installed in the environment.
try:  # pragma: no cover - import guard
    from selectolax.parser import HTMLParser  # type: ignore

    _HAS_SELECTOLAX = True
except ImportError:  # pragma: no cover - import guard
    _HAS_SELECTOLAX = False

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def html_to_text(html: str | None) -> str:
    """Convert an HTML fragment to plain text.

    Uses selectolax when available (fast, handles entities correctly) and
    falls back to a naive regex tag-strip otherwise. Never raises on
    malformed input -- worst case it returns a lightly-mangled string.
    """
    if not html:
        return ""

    if _HAS_SELECTOLAX:
        try:
            tree = HTMLParser(html)
            text = tree.body.text(separator="\n") if tree.body else tree.text(separator="\n")
        except Exception:
            text = _TAG_RE.sub(" ", html)
    else:
        text = _TAG_RE.sub(" ", html)

    text = _WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


class AtsProvider(ABC):
    """Contract every ATS integration must satisfy."""

    #: Short machine name, e.g. "greenhouse", "lever", "ashby", "workable".
    name: str

    #: Suggested minimum seconds between polls of this provider (per company).
    base_poll_interval_seconds: int = 3600

    #: Whether this provider supports submitting applications via a
    #: first-party API (tier-1 auto-apply) as opposed to requiring browser
    #: automation against the hosted apply page.
    can_submit: bool = False

    @abstractmethod
    async def list_companies(self) -> AsyncIterator[NormalizedCompany]:
        """Yield companies known to this provider.

        Phase 1 implementations may yield from a small hardcoded bootstrap
        list; later phases should read candidate slugs from the
        ``companies`` table / a discovery pipeline instead.
        """
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for type checkers

    @abstractmethod
    async def list_jobs(self, company: NormalizedCompany) -> AsyncIterator[NormalizedJob]:
        """Yield normalized job postings currently live for ``company``."""
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for type checkers
