"""
app/api/v1/routes/evaluation.py
--------------------------------
Evaluation infrastructure endpoints.

  POST /applications/{id}/outcome     – record interview/rejected/offer/no_response
  GET  /applications/{id}/score       – retrieve evaluation scores for an application
  GET  /metrics/dashboard             – JSON metrics summary
  GET  /metrics                       – HTML metrics dashboard
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_feature
from app.db.session import get_db
from app.models.enums import FeatureFlag
from app.models.evaluation_models import ApplicationEvaluation
from app.models.models import JobApplication, User
from app.schemas.evaluation import (
    ApplicationScoreResponse,
    OutcomeRequest,
    OutcomeResponse,
    ScoreDetail,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evaluation"])

_need_apps = Depends(require_feature(FeatureFlag.applications))


# ── POST /applications/{id}/outcome ──────────────────────────────────────────

@router.post(
    "/applications/{app_id}/outcome",
    response_model=OutcomeResponse,
    dependencies=[_need_apps],
    summary="Record application outcome",
)
def record_outcome(
    app_id: int,
    payload: OutcomeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OutcomeResponse:
    """
    Record the real-world outcome of a job application.

    Creates or updates the ApplicationEvaluation row for this application
    (using the cover_letter doc_type as the canonical row when no specific
    doc_type evaluation exists yet).
    """
    # Verify the application belongs to this user
    app = db.query(JobApplication).filter_by(id=app_id, user_id=user.id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    # Upsert: find an existing eval row or create one
    eval_row = (
        db.query(ApplicationEvaluation)
        .filter_by(application_id=app_id)
        .first()
    )
    if eval_row is None:
        eval_row = ApplicationEvaluation(
            application_id=app_id,
            user_id=user.id,
            doc_type="cover_letter",
            evaluated_at=datetime.utcnow(),
        )
        db.add(eval_row)

    eval_row.outcome      = payload.outcome
    eval_row.evaluated_at = datetime.utcnow()
    db.commit()

    return OutcomeResponse(
        application_id=app_id,
        outcome=payload.outcome,
        message=f"Outcome '{payload.outcome}' recorded for application {app_id}.",
    )


# ── GET /applications/{id}/score ──────────────────────────────────────────────

@router.get(
    "/applications/{app_id}/score",
    response_model=ApplicationScoreResponse,
    dependencies=[_need_apps],
    summary="Get evaluation scores for an application",
)
def get_application_score(
    app_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationScoreResponse:
    """
    Return all evaluation scores stored for a given application.

    If no evaluations exist yet, returns an empty list — run document
    generation first to trigger scoring.
    """
    # Auth check
    app = db.query(JobApplication).filter_by(id=app_id, user_id=user.id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    rows = (
        db.query(ApplicationEvaluation)
        .filter_by(application_id=app_id)
        .order_by(ApplicationEvaluation.evaluated_at.desc())
        .all()
    )

    evaluations: list[ScoreDetail] = []
    for row in rows:
        # Parse stored JSON fields
        reasoning: Optional[dict] = None
        if row.score_reasoning_json:
            try:
                reasoning = json.loads(row.score_reasoning_json)
            except (json.JSONDecodeError, TypeError):
                reasoning = None

        flags: list[dict] = []
        if row.hallucination_flags_json:
            try:
                flags = json.loads(row.hallucination_flags_json)
            except (json.JSONDecodeError, TypeError):
                flags = []

        evaluations.append(
            ScoreDetail(
                doc_type=row.doc_type,
                ats_keyword_match=row.ats_keyword_match,
                tone_score=row.tone_score,
                length_score=row.length_score,
                experience_relevance=row.experience_relevance,
                overall_score=row.overall_score,
                score_method=row.score_method,
                reasoning=reasoning,
                hallucination_count=row.hallucination_count,
                hallucination_flags=flags,
                outcome=row.outcome,
                evaluated_at=row.evaluated_at,
            )
        )

    return ApplicationScoreResponse(application_id=app_id, evaluations=evaluations)


# ── GET /metrics/dashboard ────────────────────────────────────────────────────

@router.get(
    "/metrics/dashboard",
    summary="JSON metrics dashboard",
)
def metrics_dashboard(
    since_days: int = 30,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Return a JSON summary of evaluation metrics for the authenticated user.

    Query param `since_days` controls the look-back window (default: 30).
    """
    from evaluation.metrics import MetricsAggregator
    agg = MetricsAggregator(db=db)
    return agg.dashboard(since_days=since_days, user_id=user.id)


# ── GET /metrics — HTML dashboard ─────────────────────────────────────────────

@router.get(
    "/metrics",
    response_class=HTMLResponse,
    summary="HTML metrics dashboard",
    include_in_schema=False,  # Don't show in OpenAPI docs — it's a UI page
)
def metrics_html(
    since_days: int = 30,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """
    Render a minimal HTML page showing evaluation metrics in a clean table.
    No frontend framework — plain HTML + CSS only.
    """
    from evaluation.metrics import MetricsAggregator
    agg  = MetricsAggregator(db=db)
    data = agg.dashboard(since_days=since_days, user_id=user.id)

    # ── Build HTML rows ───────────────────────────────────────────────────────
    def _row(label: str, value: str, highlight: bool = False) -> str:
        cls = ' class="highlight"' if highlight else ""
        return f"<tr{cls}><td>{label}</td><td>{value}</td></tr>"

    # Scores by job type
    scores_rows = ""
    for entry in data.get("avg_scores_by_job_type", []):
        scores_rows += (
            f"<tr>"
            f"<td>{entry['role_title']}</td>"
            f"<td>{entry['avg_overall']}</td>"
            f"<td>{entry['avg_ats']}</td>"
            f"<td>{entry['avg_exp_rel']}</td>"
            f"<td>{entry['count']}</td>"
            f"</tr>"
        )
    if not scores_rows:
        scores_rows = '<tr><td colspan="5" class="empty">No data yet</td></tr>'

    # Outcome breakdown
    outcome_data = data.get("outcome_conversion_rate", {})
    outcome_rows = ""
    for outcome, cnt in outcome_data.get("by_outcome", {}).items():
        outcome_rows += f"<tr><td>{outcome}</td><td>{cnt}</td></tr>"
    if not outcome_rows:
        outcome_rows = '<tr><td colspan="2" class="empty">No outcomes recorded</td></tr>'

    hall = data.get("hallucination_rate", {})
    cost = data.get("cost_per_application", {})

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ApplyForge — Evaluation Metrics</title>
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d2e; --accent: #6c63ff;
    --text: #e2e8f0; --muted: #718096; --border: #2d3748;
    --green: #48bb78; --amber: #ed8936; --red: #f56565;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: system-ui, sans-serif;
         padding: 2rem; line-height: 1.6; }}
  h1 {{ color: var(--accent); font-size: 1.8rem; margin-bottom: 0.25rem; }}
  .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 2rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
           gap: 1rem; margin-bottom: 2rem; }}
  .card {{ background: var(--surface); border: 1px solid var(--border);
           border-radius: 8px; padding: 1.25rem; }}
  .card-label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase;
                 letter-spacing: 0.05em; margin-bottom: 0.5rem; }}
  .card-value {{ font-size: 2rem; font-weight: 700; color: var(--accent); }}
  .card-sub {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.25rem; }}
  section {{ margin-bottom: 2rem; }}
  h2 {{ font-size: 1.1rem; color: var(--text); margin-bottom: 0.75rem;
        border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--surface);
           border-radius: 8px; overflow: hidden; }}
  th {{ background: var(--border); padding: 0.65rem 1rem; text-align: left;
        font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }}
  td {{ padding: 0.6rem 1rem; border-bottom: 1px solid var(--border); font-size: 0.9rem; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: rgba(108, 99, 255, 0.05); }}
  .empty {{ color: var(--muted); font-style: italic; text-align: center; padding: 1.5rem; }}
  .footer {{ color: var(--muted); font-size: 0.75rem; margin-top: 3rem; }}
</style>
</head>
<body>
<h1>📊 Evaluation Metrics</h1>
<p class="subtitle">
  Last {since_days} days &nbsp;·&nbsp;
  Generated {data['generated_at'][:19].replace('T', ' ')} UTC &nbsp;·&nbsp;
  {data['total_evaluations']} evaluations &nbsp;·&nbsp; {data['total_llm_calls']} LLM calls
</p>

<div class="grid">
  <div class="card">
    <div class="card-label">Hallucination Rate</div>
    <div class="card-value">{hall.get('rate_pct', 0)}%</div>
    <div class="card-sub">{hall.get('flagged', 0)} flagged / {hall.get('total_evals', 0)} evals</div>
  </div>
  <div class="card">
    <div class="card-label">Avg Cost / Application</div>
    <div class="card-value">${cost.get('avg_cost_usd', 0):.4f}</div>
    <div class="card-sub">Total: ${cost.get('total_cost_usd', 0):.4f}</div>
  </div>
  <div class="card">
    <div class="card-label">Latency P50</div>
    <div class="card-value">{data.get('latency_p50_ms', 0):.0f}ms</div>
    <div class="card-sub">P95: {data.get('latency_p95_ms', 0):.0f}ms</div>
  </div>
  <div class="card">
    <div class="card-label">Positive Outcome Rate</div>
    <div class="card-value">{outcome_data.get('positive_rate_pct', 0)}%</div>
    <div class="card-sub">interview + offer</div>
  </div>
</div>

<section>
  <h2>Average Scores by Job Type</h2>
  <table>
    <thead>
      <tr>
        <th>Role Title</th><th>Overall</th><th>ATS Match</th><th>Exp. Relevance</th><th>Count</th>
      </tr>
    </thead>
    <tbody>{scores_rows}</tbody>
  </table>
</section>

<section>
  <h2>Outcome Breakdown</h2>
  <table>
    <thead><tr><th>Outcome</th><th>Count</th></tr></thead>
    <tbody>{outcome_rows}</tbody>
  </table>
</section>

<p class="footer">
  ApplyForge Evaluation Infrastructure &nbsp;·&nbsp;
  Data window: since {data['since'][:10]}
</p>
</body>
</html>"""

    return HTMLResponse(content=html)
