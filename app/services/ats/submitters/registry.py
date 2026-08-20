"""Provider → submitter dispatch.

Add a new submitter by importing it below and registering it in
`_SUBMITTERS`. Anything not in the map returns NOT_SUPPORTED, which
the dispatcher treats as "leave in awaiting_review for the user."
"""
from __future__ import annotations

from typing import Optional

from app.services.ats.submitters.base import AtsSubmitter
from app.services.ats.submitters.lever import submitter as lever_submitter
from app.services.ats.submitters.greenhouse import submitter as greenhouse_submitter

_SUBMITTERS: dict[str, AtsSubmitter] = {
    lever_submitter.name: lever_submitter,
    greenhouse_submitter.name: greenhouse_submitter,
}


def get_submitter(ats_provider: str) -> Optional[AtsSubmitter]:
    """Return the submitter for a given provider, or None if unsupported."""
    return _SUBMITTERS.get((ats_provider or "").lower())
