"""Provider → submitter dispatch.

Two tiers:
  * `_HTTP_SUBMITTERS` — provider-native API submitters. Fast (~1s),
    cheap, high-fidelity. Prefer these when available.
  * `PLAYWRIGHT_FALLBACK` — browser automation that works on almost any
    form. Used when the provider has no HTTP submitter (Ashby, Workday,
    company career pages), or when the HTTP submitter returns
    NOT_SUPPORTED for that specific posting.

Add a new HTTP submitter by importing it below and registering it in
`_HTTP_SUBMITTERS`. `get_submitter` returns the HTTP one when it exists,
`get_fallback_submitter` always returns the Playwright submitter — the
dispatcher chains them.
"""
from __future__ import annotations

from typing import Optional

from app.services.ats.submitters.base import AtsSubmitter
from app.services.ats.submitters.lever import submitter as lever_submitter
from app.services.ats.submitters.greenhouse import submitter as greenhouse_submitter
from app.services.ats.submitters.playwright import submitter as playwright_submitter

_HTTP_SUBMITTERS: dict[str, AtsSubmitter] = {
    lever_submitter.name: lever_submitter,
    greenhouse_submitter.name: greenhouse_submitter,
}


def get_submitter(ats_provider: str) -> Optional[AtsSubmitter]:
    """Return the HTTP submitter for a given provider, or None."""
    return _HTTP_SUBMITTERS.get((ats_provider or "").lower())


def get_fallback_submitter() -> AtsSubmitter:
    """Return the browser-automation submitter that works on any form."""
    return playwright_submitter
