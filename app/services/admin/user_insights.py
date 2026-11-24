"""
Operator view of one consumer user: what they're searching for, where, what
they've generated, and what their AI usage costs.

Everything is filtered by ``user_id`` and read-only. Consumer tables only —
no recruiter (rec_*) data is read here.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.llm_pricing import estimate_cost
from app.models.answer import AnswerLibrary
from app.models.models import (
    ApplicationChat,
    ApplicationChatMessage,
    ApplicationTailoring,
    GeneratedDocument,
    InterviewSession,
    JobApplication,
    ParsedResumeData,
    ProductEvent,
    UploadedFile,
    UsageEvent,
    User,
    UserProfile,
)
from app.models.enums import FileType
from app.services.usage.quota import QuotaService

# Hostname fragment → display label. First match wins.
_SITES: list[tuple[str, str]] = [
    ("linkedin.", "LinkedIn"),
    ("indeed.", "Indeed"),
    ("glassdoor.", "Glassdoor"),
    ("greenhouse.io", "Greenhouse"),
    ("lever.co", "Lever"),
    ("ashbyhq.com", "Ashby"),
    ("myworkdayjobs.com", "Workday"),
    ("workday.com", "Workday"),
    ("smartrecruiters.com", "SmartRecruiters"),
    ("workable.com", "Workable"),
    ("otta.com", "Welcome to the Jungle"),
    ("welcometothejungle.", "Welcome to the Jungle"),
    ("wellfound.com", "Wellfound"),
    ("reed.co.uk", "Reed"),
    ("totaljobs.", "Totaljobs"),
    ("cv-library.", "CV-Library"),
    ("bayt.com", "Bayt"),
    ("naukrigulf.", "NaukriGulf"),
    ("naukri.", "Naukri"),
]

# Role family keywords, checked in order against the lower-cased title.
_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("AI / ML", ("ai ", " ai", "a.i", "machine learning", "ml ", " ml", "llm", "genai", "nlp", "computer vision", "deep learning", "applied scientist")),
    ("Data", ("data ", "analytics", "analyst", "bi ", "business intelligence")),
    ("DevOps / Cloud", ("devops", "sre", "site reliability", "cloud", "platform", "infrastructure")),
    ("Full-stack", ("full stack", "fullstack", "full-stack")),
    ("Frontend", ("frontend", "front end", "front-end", "react", "ui engineer")),
    ("Backend", ("backend", "back end", "back-end", "api ", "python", "java ", "golang")),
    ("Mobile", ("ios", "android", "mobile", "flutter")),
    ("Product", ("product manager", "product owner", "pm ")),
    ("Design", ("designer", "ux", "ui/ux")),
    ("QA / Test", ("qa", "test", "quality")),
    ("Software (general)", ("software", "engineer", "developer", "programmer")),
]

_SENIORITY = [
    ("Intern / Graduate", ("intern", "graduate", "trainee", "apprentice")),
    ("Junior", ("junior", "jr", "entry", "associate")),
    ("Senior", ("senior", "sr", "snr")),
    ("Lead / Staff+", ("lead", "staff", "principal", "head", "director", "architect", "manager")),
]


def site_for_url(url: Optional[str]) -> str:
    if not url:
        return "No link (pasted JD)"
    try:
        host = (urlparse(url if "://" in url else f"https://{url}").hostname or "").lower()
    except ValueError:
        return "Other"
    if not host:
        return "Other"
    for frag, label in _SITES:
        if frag in host:
            return label
    parts = host.removeprefix("www.").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _norm_title(t: str) -> str:
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t or "")
    t = re.split(r"\s[\|\-–—@]\s|,", t)[0]
    return re.sub(r"\s+", " ", t).strip()


def _bucket(title: str, table) -> str:
    padded = f" {title.lower()} "
    for label, keys in table:
        if any(k in padded for k in keys):
            return label
    return "Other" if table is _FAMILIES else "Mid-level"


def _json_list(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return [str(v) for v in val if v] if isinstance(val, list) else [str(val)]
    except (ValueError, TypeError):
        return [s.strip() for s in raw.split(",") if s.strip()]


def _top(counter: Counter, n: int) -> list[dict[str, Any]]:
    return [{"label": k, "count": v} for k, v in counter.most_common(n)]


def user_insights(db: Session, user: User, days: int = 30) -> dict[str, Any]:
    uid = user.id
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    month_start = datetime(now.year, now.month, 1)

    # ── Job search ────────────────────────────────────────────────────────
    apps = (
        db.query(
            JobApplication.role_title, JobApplication.company_name, JobApplication.jd_link,
            JobApplication.status, JobApplication.priority_score, JobApplication.created_at,
        )
        .filter(JobApplication.user_id == uid, JobApplication.deleted_at.is_(None))
        .all()
    )
    titles: Counter = Counter()
    display: dict[str, Counter] = defaultdict(Counter)
    families: Counter = Counter()
    seniority: Counter = Counter()
    companies: Counter = Counter()
    sites: Counter = Counter()
    statuses: Counter = Counter()
    scores: list[float] = []
    weekly: Counter = Counter()
    last_app: Optional[datetime] = None
    for title, company, link, status, score, created in apps:
        t = _norm_title(title)
        if t:
            titles[t.lower()] += 1
            display[t.lower()][t] += 1
            families[_bucket(t, _FAMILIES)] += 1
            seniority[_bucket(t, _SENIORITY)] += 1
        if company:
            companies[company.strip()] += 1
        sites[site_for_url(link)] += 1
        statuses[getattr(status, "value", status)] += 1
        if score is not None:
            scores.append(float(score))
        if created:
            last_app = max(last_app or created, created)
            # ISO week start (Monday) for the last 12 weeks
            if created >= now - timedelta(weeks=12):
                monday = (created - timedelta(days=created.weekday())).date().isoformat()
                weekly[monday] += 1

    top_titles = [
        {"label": display[k].most_common(1)[0][0], "count": v} for k, v in titles.most_common(10)
    ]
    weeks = []
    cur = (now - timedelta(days=now.weekday())).date()
    for i in range(11, -1, -1):
        d = (cur - timedelta(weeks=i)).isoformat()
        weeks.append({"date": d, "value": weekly.get(d, 0)})

    applied_like = {"applied", "follow_up", "interview", "replied", "offer", "rejected"}
    funnel = {
        "saved": len(apps),
        "applied": sum(statuses.get(s, 0) for s in applied_like),
        "interview": statuses.get("interview", 0) + statuses.get("offer", 0),
        "offer": statuses.get("offer", 0),
    }

    profile = db.query(UserProfile).filter_by(user_id=uid).first()

    # Sites browsed with the extension (scored), from product events.
    browsed: Counter = Counter()
    for (props,) in (
        db.query(ProductEvent.properties)
        .filter(ProductEvent.user_id == uid, ProductEvent.event_name.in_(("extension_job_scored", "extension_job_saved")))
        .all()
    ):
        try:
            site = (json.loads(props or "{}") or {}).get("source_site")
        except ValueError:
            site = None
        if site:
            browsed[str(site).capitalize()] += 1

    # ── Documents ─────────────────────────────────────────────────────────
    doc_counts = dict(
        db.query(GeneratedDocument.doc_type, func.count(GeneratedDocument.id))
        .filter(GeneratedDocument.user_id == uid)
        .group_by(GeneratedDocument.doc_type)
        .all()
    )
    docs_by_type = {getattr(k, "value", k): int(v) for k, v in doc_counts.items()}
    apps_with_docs = (
        db.query(func.count(func.distinct(GeneratedDocument.application_id)))
        .filter(GeneratedDocument.user_id == uid)
        .scalar() or 0
    )
    resumes_uploaded = (
        db.query(func.count(UploadedFile.id))
        .filter(UploadedFile.user_id == uid, UploadedFile.file_type == FileType.resume)
        .scalar() or 0
    )
    parsed = (
        db.query(func.count(ParsedResumeData.id), func.avg(ParsedResumeData.confidence_score))
        .filter(ParsedResumeData.user_id == uid, ParsedResumeData.deleted_at.is_(None))
        .one()
    )
    tailored = db.query(func.count(ApplicationTailoring.id)).filter(ApplicationTailoring.user_id == uid).scalar() or 0
    chat_threads = db.query(func.count(ApplicationChat.id)).filter(ApplicationChat.user_id == uid).scalar() or 0
    chat_messages = (
        db.query(func.count(ApplicationChatMessage.id))
        .filter(ApplicationChatMessage.user_id == uid, ApplicationChatMessage.sender_role == "user")
        .scalar() or 0
    )
    answers = db.query(func.count(AnswerLibrary.id)).filter(AnswerLibrary.user_id == uid).scalar() or 0
    interviews = (
        db.query(func.count(InterviewSession.id))
        .filter(InterviewSession.user_id == uid, InterviewSession.deleted_at.is_(None))
        .scalar() or 0
    )

    # ── AI usage & cost ───────────────────────────────────────────────────
    def _grouped(start: Optional[datetime], *dims):
        q = db.query(
            *dims, UsageEvent.model,
            func.coalesce(func.sum(UsageEvent.tokens_in), 0),
            func.coalesce(func.sum(UsageEvent.tokens_out), 0),
            func.count(UsageEvent.id),
        ).filter(UsageEvent.user_id == uid)
        if start is not None:
            q = q.filter(UsageEvent.created_at >= start)
        return q.group_by(*dims, UsageEvent.model).all()

    def _cost_total(rows) -> tuple[float, int, int]:
        cost = tokens = calls = 0
        for *_, model, t_in, t_out, n in rows:
            cost += estimate_cost(model, int(t_in), int(t_out))
            tokens += int(t_in) + int(t_out)
            calls += int(n)
        return round(cost, 6), tokens, calls

    month_cost, month_tokens, month_calls = _cost_total(_grouped(month_start))
    all_cost, all_tokens, all_calls = _cost_total(_grouped(None))

    ops: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost_usd": 0.0})
    for endpoint, kind, model, t_in, t_out, n in _grouped(since, UsageEvent.endpoint, UsageEvent.kind):
        key = re.sub(r" ×\d+$", "", endpoint or "unknown")
        o = ops[key]
        o["kind"] = kind
        o["calls"] += int(n)
        o["tokens"] += int(t_in) + int(t_out)
        o["cost_usd"] = round(o["cost_usd"] + estimate_cost(model, int(t_in), int(t_out)), 6)
    by_operation = sorted(({"operation": k, **v} for k, v in ops.items()), key=lambda r: r["tokens"], reverse=True)

    day_col = func.date(UsageEvent.created_at)
    per_day: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for d, model, t_in, t_out, _n in _grouped(since, day_col):
        k = str(d)[:10]
        per_day[k][0] += int(t_in) + int(t_out)
        per_day[k][1] += estimate_cost(model, int(t_in), int(t_out))
    daily_series = []
    for i in range(days - 1, -1, -1):
        k = (now - timedelta(days=i)).date().isoformat()
        tok, cost = per_day.get(k, [0, 0.0])
        daily_series.append({"date": k, "tokens": int(tok), "cost_usd": round(cost, 6)})

    last_ai = db.query(func.max(UsageEvent.created_at)).filter(UsageEvent.user_id == uid).scalar()

    return {
        "user_id": uid,
        "window_days": days,
        "quota": QuotaService(db).status(user),
        "spend": {
            "month_cost_usd": month_cost, "month_tokens": month_tokens, "month_calls": month_calls,
            "all_time_cost_usd": all_cost, "all_time_tokens": all_tokens, "all_time_calls": all_calls,
            "last_ai_call_at": last_ai,
            # Metering covers every AI call only from migration 0044 onwards.
            "tracked_since_note": "Before migration 0044 only Coach chat was metered; earlier spend is not in these numbers.",
        },
        "by_operation": by_operation,
        "daily_usage": daily_series,
        "search": {
            "declared_target_roles": _json_list(profile.target_roles if profile else None),
            "preferred_locations": _json_list(profile.preferred_locations if profile else None),
            "current_role": profile.current_role if profile else None,
            "headline": profile.headline if profile else None,
            "location": profile.location if profile else None,
            "salary_expectation": profile.salary_expectation if profile else None,
            "top_titles": top_titles,
            "role_families": _top(families, 8),
            "seniority": _top(seniority, 5),
            "top_companies": _top(companies, 10),
            "sites": _top(sites, 10),
            "extension_sites": _top(browsed, 6),
            "status_counts": dict(statuses),
            "funnel": funnel,
            "avg_priority_score": round(sum(scores) / len(scores), 1) if scores else None,
            "last_application_at": last_app,
            "added_last_7d": sum(1 for *_, c in apps if c and c >= now - timedelta(days=7)),
            "added_last_30d": sum(1 for *_, c in apps if c and c >= now - timedelta(days=30)),
            "weekly_added": weeks,
        },
        "documents": {
            "resumes_uploaded": int(resumes_uploaded),
            "resumes_parsed": int(parsed[0] or 0),
            "avg_parse_confidence": round(float(parsed[1]), 2) if parsed[1] is not None else None,
            "tailored_resumes": int(tailored),
            "generated_by_type": docs_by_type,
            "generated_total": sum(docs_by_type.values()),
            "applications_with_documents": int(apps_with_docs),
            "chat_threads": int(chat_threads),
            "chat_messages_sent": int(chat_messages),
            "answer_library_entries": int(answers),
            "interview_sessions": int(interviews),
        },
    }
