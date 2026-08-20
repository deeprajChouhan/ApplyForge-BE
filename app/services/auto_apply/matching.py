"""Backwards-compatible shim.

The auto-apply orchestrator imports `score_job_for_user` from this
module. As of the RCMS rollout the real implementation lives in
`app.services.matching`; this file just re-exports it so the
orchestrator and any older callers keep working unchanged.
"""
from app.services.matching.matching_new import score_job_for_user  # noqa: F401

__all__ = ["score_job_for_user"]
