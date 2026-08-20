"""Phase 4: real ATS submitters.

Each submitter takes a JobApplication + the user's parsed resume +
generated cover letter, and attempts to submit to the ATS's public
endpoint. See `base.py` for the Protocol and `registry.py` for the
provider→submitter dispatch used by dispatcher.submit_application.
"""
from app.services.ats.submitters.base import SubmitOutcome, SubmitResult  # noqa: F401
from app.services.ats.submitters.registry import get_submitter  # noqa: F401
