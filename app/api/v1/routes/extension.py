"""
ApplyForge Job Clipper — browser extension API.

  GET  /extension/status              -> connection + promo state
  POST /extension/jobs/score-preview  -> Priority Score without saving
  POST /extension/jobs/save           -> save a scored job into the pipeline
  POST /extension/events              -> record an extension product event
  POST /extension/promo/dismiss       -> dismiss the in-app extension promo

All endpoints require an authenticated ApplyForge user. Anonymous scoring or
saving is not permitted.
"""
import os

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import User
from app.schemas.extension import (
    ExtensionEventIn,
    ExtensionScorePreviewOut,
    ExtensionStatusOut,
    SaveJobIn,
    SaveJobOut,
    ScorePreviewIn,
    ScorePreviewMeta,
)
from app.services.analytics.service import ProductEventService
from app.services.applications.service import ApplicationService
from app.services.extension.service import (
    ExtensionService,
    frontend_base_url,
    score_confidence,
)
from app.services.scoring.service import (
    CompetitionScorer,
    JDInsightExtractor,
    PriorityScorer,
    ReplyPredictor,
    explain_preview_score,
)

router = APIRouter(prefix="/extension", tags=["extension"])

# Allow-list of extension funnel events we accept from the client.
_ALLOWED_EVENTS = {
    "extension_promo_viewed",
    "extension_promo_dismissed",
    "extension_install_clicked",
    "extension_connected",
    "extension_job_scored",
    "extension_job_saved",
    "extension_auth_synced",
    "extension_auth_failed",
}


def _chrome_store_url() -> str | None:
    return os.environ.get("CHROME_EXTENSION_URL") or None


@router.get("/status", response_model=ExtensionStatusOut)
def extension_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    state = ExtensionService(db, user.id).get_state()
    return ExtensionStatusOut(
        connected=bool(state and state.connected_at),
        connected_at=state.connected_at if state else None,
        last_seen_at=state.last_seen_at if state else None,
        promo_dismissed_at=state.promo_dismissed_at if state else None,
        chrome_store_url=_chrome_store_url(),
    )


@router.post("/jobs/score-preview", response_model=ExtensionScorePreviewOut)
def score_preview(
    payload: ScorePreviewIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    jd = payload.description or f"{payload.title} at {payload.company}"
    co = payload.company
    role = payload.title

    fit_score = 50.0  # neutral until full profile match runs post-save
    competition = CompetitionScorer.score(jd, co)
    composed = PriorityScorer.compose(fit_score, competition)
    insights = JDInsightExtractor.extract(jd, co, role)

    reply_prob, reply_label, reply_reasoning = ReplyPredictor.predict(
        competition_score=competition,
        jd_text=jd,
        company_name=co,
        work_type=insights["work_type"],
        contract_type=insights["contract_type"],
    )
    why = explain_preview_score(fit_score, competition, insights)
    confidence = score_confidence(payload.description, payload.source_site)
    opportunity = round(100 - competition, 2)

    # Mark the extension as active/connected on real usage.
    ExtensionService(db, user.id).mark_seen(connect=True)
    ProductEventService(db).track(
        "extension_job_scored",
        user_id=user.id,
        properties={"source_site": payload.source_site, "confidence": confidence},
        request=request,
    )

    return ExtensionScorePreviewOut(
        priority_score=composed["priority_score"],
        label=composed["label"],
        fit_score=composed["fit_score"],
        opportunity_score=opportunity,
        competition_score=composed["competition_score"],
        score_confidence=confidence,
        recommendation=composed["recommendation"],
        summary=insights["job_summary"],
        job_summary=insights["job_summary"],
        why_score=why,
        key_requirements=insights["key_skills"],
        reply_likelihood=reply_prob,
        reply_probability=reply_prob,
        reply_label=reply_label,
        reply_reasoning=reply_reasoning,
        required_yoe=insights["required_yoe"],
        detected_seniority=insights["detected_seniority"],
        work_type=insights["work_type"],
        contract_type=insights["contract_type"],
        meta=ScorePreviewMeta(
            seniority=insights["detected_seniority"],
            work_type=insights["work_type"],
            contract_type=insights["contract_type"],
        ),
    )


@router.post("/jobs/save", response_model=SaveJobOut)
def save_job(
    payload: SaveJobIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = ApplicationService(db, user.id).create(
        {
            "company_name": payload.company or "Unknown Company",
            "role_title": payload.title or "Unknown Role",
            "job_description": payload.description or f"{payload.title} at {payload.company}",
            "jd_link": payload.source_url or None,
        }
    )

    ExtensionService(db, user.id).mark_seen(connect=True)
    ProductEventService(db).track(
        "extension_job_saved",
        user_id=user.id,
        entity_type="job_application",
        entity_id=app.id,
        properties={
            "source_site": payload.source_site,
            "score_confidence": payload.score_confidence,
        },
        request=request,
    )

    open_url = f"{frontend_base_url()}/applications/{app.id}"
    return SaveJobOut(success=True, application_id=str(app.id), open_url=open_url)


@router.post("/events")
def track_event(
    payload: ExtensionEventIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = payload.event if payload.event in _ALLOWED_EVENTS else "extension_event_other"
    props = dict(payload.properties or {})
    if event == "extension_event_other":
        props["original_event"] = payload.event

    ExtensionService(db, user.id).mark_seen(connect=(event == "extension_connected"))
    ProductEventService(db).track(
        event, user_id=user.id, properties=props, request=request
    )
    return {"ok": True}


@router.post("/promo/dismiss")
def dismiss_promo(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    state = ExtensionService(db, user.id).dismiss_promo()
    ProductEventService(db).track(
        "extension_promo_dismissed", user_id=user.id, request=request
    )
    return {"ok": True, "promo_dismissed_at": state.promo_dismissed_at}
