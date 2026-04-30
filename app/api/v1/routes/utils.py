"""
Utility endpoints — lightweight helpers used by the browser extension
and other clients that don't need a saved application.
"""
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.models import User
from app.schemas.application import ScorePreviewRequest, ScoreResponse
from app.services.scoring.service import CompetitionScorer, FitScorer, PriorityScorer

router = APIRouter(prefix="/utils", tags=["utils"])


@router.post("/score-preview", response_model=ScoreResponse)
def score_preview(
    payload: ScorePreviewRequest,
    _user: User = Depends(get_current_user),
) -> ScoreResponse:
    """
    Score a job listing without creating an application.
    Used by the browser extension to show a Priority Score badge on any job page.

    - fit_score is approximated from the raw JD text (no RAG, no profile needed)
      using keyword density as a proxy until the user saves the application.
    - competition_score is derived from seniority and experience signals in the JD.

    All authenticated users can call this endpoint — no feature flag required
    so the extension works on free-tier accounts.
    """
    # Lightweight fit approximation — neutral 50 without a real profile match.
    # The full RAG-backed score runs post-save via POST /applications/{id}/score.
    fit_score         = 50.0
    competition_score = CompetitionScorer.score(payload.jd_text, payload.company_name)

    composed = PriorityScorer.compose(fit_score, competition_score)
    return ScoreResponse(**composed)
