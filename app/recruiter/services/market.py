"""
Market & potential analysis (Phase 3, Section 3.4) — self-contained edition.

Computes a recruiter-facing market picture entirely from the agency's OWN data:
demand (what open roles ask for) versus supply (what the candidate pool offers),
skill shortages, salary bands, the pipeline funnel, and time-to-fill. No
dependency on the consumer crawler; when external market data is wired in later
it can enrich these same aggregates.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.recruiter.enums import ApplicationStage, RoleStatus
from app.recruiter.models import Application, CandidateProfile, Role


@dataclass
class SkillDemandSupply:
    skill: str
    demand: int  # number of roles asking for it (required or preferred)
    supply: int  # number of pool candidates who have it
    shortage: bool


@dataclass
class SalarySummary:
    count: int = 0
    avg_min: int | None = None
    avg_max: int | None = None
    overall_min: int | None = None
    overall_max: int | None = None


@dataclass
class StageCount:
    stage: str
    count: int


@dataclass
class MarketOverview:
    roles_total: int = 0
    roles_open: int = 0
    candidates_total: int = 0
    placements: int = 0
    time_to_fill_days: float | None = None
    skills: list[SkillDemandSupply] = field(default_factory=list)
    shortages: list[SkillDemandSupply] = field(default_factory=list)
    salary: SalarySummary = field(default_factory=SalarySummary)
    pipeline_funnel: list[StageCount] = field(default_factory=list)


def _avg(nums: list[int]) -> int | None:
    return round(sum(nums) / len(nums)) if nums else None


def compute_market(db: Session, agency_id: int, top: int = 12) -> MarketOverview:
    roles: list[Role] = db.query(Role).filter(Role.agency_id == agency_id).all()
    candidates: list[CandidateProfile] = (
        db.query(CandidateProfile).filter(CandidateProfile.agency_id == agency_id).all()
    )
    apps: list[Application] = (
        db.query(Application).filter(Application.agency_id == agency_id).all()
    )

    # ── Demand: roles wanting each skill ──
    demand: dict[str, int] = {}
    for role in roles:
        wanted = set((role.required_skills or []) + (role.preferred_skills or []))
        for s in wanted:
            demand[s] = demand.get(s, 0) + 1

    # ── Supply: candidates who have each skill ──
    supply: dict[str, int] = {}
    for cand in candidates:
        for cs in {s.name for s in cand.skills}:
            supply[cs] = supply.get(cs, 0) + 1

    all_skills = set(demand) | set(supply)
    rows = [
        SkillDemandSupply(
            skill=s,
            demand=demand.get(s, 0),
            supply=supply.get(s, 0),
            shortage=demand.get(s, 0) > 0 and supply.get(s, 0) < demand.get(s, 0),
        )
        for s in all_skills
    ]
    # Most relevant first: by demand, then by shortage severity.
    rows.sort(key=lambda r: (r.demand, r.demand - r.supply), reverse=True)
    skills = rows[:top]
    shortages = sorted(
        [r for r in rows if r.shortage], key=lambda r: (r.demand - r.supply), reverse=True
    )[:top]

    # ── Salary bands over roles that specify pay ──
    mins = [r.salary_min for r in roles if r.salary_min]
    maxs = [r.salary_max for r in roles if r.salary_max]
    salary = SalarySummary(
        count=sum(1 for r in roles if r.salary_min or r.salary_max),
        avg_min=_avg(mins),
        avg_max=_avg(maxs),
        overall_min=min(mins) if mins else None,
        overall_max=max(maxs) if maxs else None,
    )

    # ── Pipeline funnel + placements + time-to-fill ──
    stage_counts: dict[str, int] = {stage.value: 0 for stage in ApplicationStage}
    fill_days: list[float] = []
    for app in apps:
        stage_counts[app.stage.value] = stage_counts.get(app.stage.value, 0) + 1
        if app.stage == ApplicationStage.placed and app.created_at and app.last_activity_at:
            delta = (app.last_activity_at - app.created_at).total_seconds() / 86_400
            if delta >= 0:
                fill_days.append(delta)

    funnel = [StageCount(stage=s.value, count=stage_counts.get(s.value, 0)) for s in ApplicationStage]
    placements = stage_counts.get(ApplicationStage.placed.value, 0)
    ttf = round(sum(fill_days) / len(fill_days), 1) if fill_days else None

    return MarketOverview(
        roles_total=len(roles),
        roles_open=sum(1 for r in roles if r.status == RoleStatus.open),
        candidates_total=len(candidates),
        placements=placements,
        time_to_fill_days=ttf,
        skills=skills,
        shortages=shortages,
        salary=salary,
        pipeline_funnel=funnel,
    )
