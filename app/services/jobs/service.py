"""
JobFeedService — on-demand "Job Discovery" feed.

Unlike the background `CrawlerService` (Pro job crawler, persisted
`CrawledJob` rows, LLM re-scoring), this feed fetches live postings
synchronously from the free keyless sources and scores them with a fast,
local heuristic only — no LLM calls, so a feed request stays snappy.

If no live postings are found (no sources matched, network issues, etc.),
a small set of realistic demo postings is returned instead, each flagged
with `is_fallback=True` so the frontend can show a "Demo data" badge.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import CrawlerConfig, ParsedResumeData, UserProfile
from app.schemas.jobs import JobFeedItem, JobFeedResponse
from app.services.crawler.sources import fetch_arbeitnow, fetch_jobicy, fetch_remoteok

logger = logging.getLogger(__name__)

_DEFAULT_ROLES = ["Software Engineer"]

_STOP_WORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "her",
    "was", "one", "our", "out", "day", "get", "has", "him", "his", "how",
    "its", "new", "now", "old", "see", "two", "who", "did", "let", "put",
    "say", "she", "too", "use", "will", "with", "this", "that", "have",
    "from", "they", "been", "were", "your", "what", "when", "which", "more",
    "also", "into", "than", "then", "some", "would", "about", "their",
    "there", "these", "other", "after", "work", "team", "role", "join",
    "look", "help", "make", "time", "need", "must", "well", "strong",
    "good", "great", "able", "within", "across", "including", "such",
    "skills", "experience", "years", "working", "looking", "based",
    "position", "company", "provide", "ability", "ensure", "manage",
    "develop", "support", "using", "learn", "build", "lead", "drive",
}


def _extract_words(text: str) -> set[str]:
    return {
        w for w in re.findall(r"\b[a-z]{4,}\b", text.lower())
        if w not in _STOP_WORDS
    }


class JobFeedService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def get_feed(
        self,
        keywords: Optional[list[str]] = None,
        work_type: str = "any",
        country: Optional[str] = None,
        min_score: Optional[float] = None,
        limit: int = 20,
    ) -> JobFeedResponse:
        roles = [k for k in (keywords or []) if k.strip()] or self._target_roles()

        raw_jobs: list[dict] = []
        for fetch in (fetch_remoteok, fetch_arbeitnow, fetch_jobicy):
            try:
                raw_jobs.extend(fetch(roles, work_type, country))
            except Exception as exc:
                logger.warning("job_feed_source_error fetch=%s: %s", fetch.__name__, exc)

        resume_text = self._resume_text()

        items: list[JobFeedItem] = []
        for raw in raw_jobs:
            score = self._score_job(raw, roles, resume_text)
            if min_score is not None and score < min_score:
                continue
            items.append(JobFeedItem(
                id=f"{raw['source']}:{raw['external_id']}",
                company=raw.get("company") or "Unknown",
                role=raw.get("title") or "",
                location=raw.get("location"),
                country=country,
                work_type=raw.get("work_type"),
                source=raw["source"],
                source_url=raw.get("apply_url") or "",
                description=raw.get("description"),
                match_score=score,
                is_fallback=False,
            ))

        items.sort(key=lambda i: i.match_score or 0, reverse=True)

        if items:
            return JobFeedResponse(items=items[:limit], is_fallback=False)

        return JobFeedResponse(items=self._fallback_items(roles, work_type, country)[:limit], is_fallback=True)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _target_roles(self) -> list[str]:
        cfg = self.db.query(CrawlerConfig).filter_by(user_id=self.user_id).first()
        if cfg and cfg.job_roles:
            try:
                roles = json.loads(cfg.job_roles)
                if roles:
                    return roles
            except (TypeError, ValueError):
                pass

        profile = self.db.query(UserProfile).filter_by(user_id=self.user_id).first()
        if profile and profile.headline:
            return [profile.headline]

        return _DEFAULT_ROLES

    def _resume_text(self) -> str:
        row = (
            self.db.query(ParsedResumeData)
            .filter(ParsedResumeData.user_id == self.user_id, ParsedResumeData.deleted_at.is_(None))
            .order_by(ParsedResumeData.created_at.desc())
            .first()
        )
        return row.raw_text if row else ""

    def _score_job(self, raw: dict, roles: list[str], resume_text: str) -> float:
        title = (raw.get("title") or "").lower()
        desc = (raw.get("description") or "").lower()

        title_score = 0.0
        for role in roles:
            role_lower = role.lower()
            if role_lower in title:
                title_score = 40.0
                break
            words = [w for w in role_lower.split() if len(w) > 2]
            if any(w in title for w in words):
                title_score = max(title_score, 20.0)

        resume_score = 0.0
        if resume_text and desc:
            resume_words = _extract_words(resume_text)
            job_words = _extract_words(f"{title} {desc}")
            if resume_words and job_words:
                overlap = len(resume_words & job_words)
                ratio = min(overlap / max(len(job_words), 1), 0.40) / 0.40
                resume_score = round(ratio * 60.0, 1)

        return round(min(100.0, title_score + resume_score), 1)

    def _fallback_items(self, roles: list[str], work_type: str, country: Optional[str]) -> list[JobFeedItem]:
        role = roles[0] if roles else "Software Engineer"
        wt = work_type if work_type != "any" else "remote"
        loc = country.upper() if country else "Remote"

        templates = [
            ("Nova Systems", f"Senior {role}", 92.0,
             f"Nova Systems is hiring a Senior {role} to join our growing product team. "
             "Remote-first, async-friendly culture with a focus on ownership and craft."),
            ("Brightline Labs", f"{role}", 86.0,
             f"Brightline Labs is looking for a {role} to help us ship customer-facing "
             "features. Competitive pay, flexible hours, small collaborative team."),
            ("Meridian Cloud", f"{role} II", 81.0,
             f"Meridian Cloud is expanding our platform team and seeking a {role} II "
             "with a strong foundation in shipping reliable, well-tested software."),
            ("Pulsewave", f"Lead {role}", 78.0,
             f"Pulsewave is building the next generation of developer tools and needs a "
             f"Lead {role} to mentor a small team and drive technical direction."),
            ("Anchorpoint", f"{role} (Contract)", 74.0,
             f"Anchorpoint has an open contract role for a {role} to support an "
             "upcoming product launch, with potential to convert to full-time."),
            ("Glasswing", f"Junior {role}", 69.0,
             f"Glasswing is a small but fast-growing startup looking for a Junior {role} "
             "eager to learn and grow alongside a supportive team."),
        ]

        return [
            JobFeedItem(
                id=f"demo:{i + 1}",
                company=company,
                role=title,
                location=loc,
                country=country,
                work_type=wt,
                source="demo",
                source_url="https://applyforge.pro/jobs/demo",
                description=description,
                posted_at=None,
                match_score=score,
                is_fallback=True,
            )
            for i, (company, title, score, description) in enumerate(templates)
        ]
