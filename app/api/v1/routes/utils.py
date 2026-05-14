"""
Utility endpoints — lightweight helpers used by the browser extension
and other clients that don't need a saved application.
"""
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.models import User
from app.schemas.application import ScorePreviewRequest, ScoreResponse
from app.services.scoring.service import (
    CompetitionScorer,
    FitScorer,
    JDInsightExtractor,
    PriorityScorer,
    ReplyPredictor,
    explain_preview_score,
)

router = APIRouter(prefix="/utils", tags=["utils"])


@router.post("/score-preview", response_model=ScoreResponse)
def score_preview(
    payload: ScorePreviewRequest,
    _user: User = Depends(get_current_user),
) -> ScoreResponse:
    """
    Score a job listing without creating an application.
    Used by the browser extension to show a Priority Score badge on any job page.

    Returns a rich ScoreResponse that includes:
    - priority_score / fit_score / competition_score (as before)
    - job_summary     — what the role is actually about
    - key_requirements — top skills/signals detected in the JD
    - why_score       — plain-English reason for the score
    - reply_probability / reply_label / reply_reasoning — custom reply predictor
    - required_yoe / detected_seniority / work_type / contract_type

    fit_score stays at 50 (neutral) in preview because no profile is loaded;
    the full RAG-backed score runs post-save via POST /applications/{id}/score.
    """
    jd   = payload.jd_text
    co   = payload.company_name
    role = payload.role_title

    # ── Sub-scores ─────────────────────────────────────────────────────────────
    fit_score         = 50.0  # neutral until full profile match post-save
    competition_score = CompetitionScorer.score(jd, co)

    composed = PriorityScorer.compose(fit_score, competition_score)

    # ── JD insights ────────────────────────────────────────────────────────────
    insights = JDInsightExtractor.extract(jd, co, role)

    # ── Reply prediction ───────────────────────────────────────────────────────
    reply_prob, reply_label, reply_reasoning = ReplyPredictor.predict(
        competition_score=competition_score,
        jd_text=jd,
        company_name=co,
        work_type=insights["work_type"],
        contract_type=insights["contract_type"],
    )

    # ── Human-readable score explanation ──────────────────────────────────────
    why_score = explain_preview_score(fit_score, competition_score, insights)

    return ScoreResponse(
        **composed,
        job_summary        = insights["job_summary"],
        key_requirements   = insights["key_skills"],
        why_score          = why_score,
        reply_probability  = reply_prob,
        reply_label        = reply_label,
        reply_reasoning    = reply_reasoning,
        required_yoe       = insights["required_yoe"],
        detected_seniority = insights["detected_seniority"],
        work_type          = insights["work_type"],
        contract_type      = insights["contract_type"],
    )
