"""
CrawlerService — orchestrates daily job discovery for a single user.

Flow:
  1. Load CrawlerConfig for the user.
  2. Fetch raw postings from all enabled sources.
  3. Deduplicate:
     a. Against existing crawled_jobs (source + external_id).
     b. Against existing job_applications (jd_link URL match).
  4. AI-score each new posting against the user's resume text.
  5. Persist CrawledJob rows.
  6. Update last_run_at on CrawlerConfig.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import CrawledJob, CrawlerConfig, JobApplication, ParsedResumeData
from app.services.crawler.sources import fetch_arbeitnow, fetch_adzuna, fetch_jobicy, fetch_linkedin, fetch_remoteok

logger = logging.getLogger(__name__)


class CrawlerService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    # ── Config helpers ──────────────────────────────────────────────────────

    def get_or_create_config(self) -> CrawlerConfig:
        cfg = self.db.query(CrawlerConfig).filter_by(user_id=self.user_id).first()
        if not cfg:
            cfg = CrawlerConfig(user_id=self.user_id)
            self.db.add(cfg)
            self.db.commit()
            self.db.refresh(cfg)
        return cfg

    def update_config(self, data: dict) -> CrawlerConfig:
        cfg = self.get_or_create_config()
        for key, value in data.items():
            if value is None:
                continue
            if key == "job_roles":
                setattr(cfg, key, json.dumps(value))
            else:
                setattr(cfg, key, value)
        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    # ── Main crawl entry point ──────────────────────────────────────────────

    def run_crawl(self) -> dict:
        """
        Execute a full crawl for this user and return stats.
        Returns {"jobs_found": N, "jobs_added": M}
        """
        cfg = self.get_or_create_config()
        if not cfg.is_enabled:
            logger.info("crawler_skip_not_enabled user_id=%s", self.user_id)
            return {"jobs_found": 0, "jobs_added": 0, "skipped": True, "skip_reason": "not_enabled"}

        job_roles: list[str] = json.loads(cfg.job_roles) if cfg.job_roles else []
        if not job_roles:
            logger.info("crawler_skip_no_roles user_id=%s", self.user_id)
            return {"jobs_found": 0, "jobs_added": 0, "skipped": True, "skip_reason": "no_roles"}

        country    = cfg.country or "us"
        work_type  = cfg.work_type or "any"
        salary_min = cfg.salary_min

        # 1. Fetch from all sources (country passed to every source)
        raw_jobs: list[dict] = []
        src_remoteok  = fetch_remoteok(job_roles, work_type, country)
        src_arbeitnow = fetch_arbeitnow(job_roles, work_type, country)
        src_jobicy    = fetch_jobicy(job_roles, work_type, country)
        src_linkedin  = fetch_linkedin(job_roles, work_type, country)
        src_adzuna    = fetch_adzuna(
            job_roles,
            country=country,
            salary_min=salary_min,
            work_type=work_type,
            app_id=getattr(settings, "adzuna_app_id", None),
            app_key=getattr(settings, "adzuna_app_key", None),
        )
        raw_jobs = src_remoteok + src_arbeitnow + src_jobicy + src_linkedin + src_adzuna

        jobs_found = len(raw_jobs)
        logger.info(
            "crawler_fetched user_id=%s total=%s "
            "remoteok=%s arbeitnow=%s jobicy=%s linkedin=%s adzuna=%s",
            self.user_id, jobs_found,
            len(src_remoteok), len(src_arbeitnow),
            len(src_jobicy), len(src_linkedin), len(src_adzuna),
        )

        # 2. Build existing-URL set for duplicate detection
        existing_urls = self._existing_application_urls()

        # 3. Build existing crawled (source, external_id) set
        existing_crawled = self._existing_crawled_keys()

        # 4. Get resume text for scoring
        resume_text = self._get_resume_text(cfg.selected_resume_id)

        # 5. Process and persist new jobs
        jobs_added = 0
        for raw in raw_jobs:
            key = (raw["source"], raw["external_id"])
            if key in existing_crawled:
                continue
            if raw["apply_url"] in existing_urls:
                continue

            score, reason = self._score_job(raw, job_roles, resume_text)

            job = CrawledJob(
                user_id=self.user_id,
                source=raw["source"],
                external_id=raw["external_id"],
                title=raw["title"],
                company=raw["company"],
                location=raw.get("location"),
                work_type=raw.get("work_type"),
                salary_range=raw.get("salary_range"),
                description=raw.get("description"),
                apply_url=raw["apply_url"],
                tags=json.dumps([str(t) for t in (raw.get("tags") or []) if t and not isinstance(t, (list, dict))]),
                match_score=score,
                match_reason=reason,
                crawled_at=datetime.utcnow(),
            )
            self.db.add(job)
            existing_crawled.add(key)
            jobs_added += 1

        # 6. Update last_run_at
        cfg.last_run_at = datetime.utcnow()
        self.db.commit()

        logger.info("crawler_done user_id=%s added=%s", self.user_id, jobs_added)
        return {"jobs_found": jobs_found, "jobs_added": jobs_added}

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _existing_application_urls(self) -> set[str]:
        rows = (
            self.db.query(JobApplication.jd_link)
            .filter(JobApplication.user_id == self.user_id, JobApplication.jd_link.isnot(None))
            .all()
        )
        return {r.jd_link for r in rows if r.jd_link}

    def _existing_crawled_keys(self) -> set[tuple]:
        """
        Only deduplicate against jobs crawled in the last 30 days.
        Older records are expired — the same job re-posted is fair game.
        """
        cutoff = datetime.utcnow() - timedelta(days=30)
        rows = (
            self.db.query(CrawledJob.source, CrawledJob.external_id)
            .filter(
                CrawledJob.user_id == self.user_id,
                CrawledJob.crawled_at >= cutoff,
            )
            .all()
        )
        return {(r.source, r.external_id) for r in rows}

    def _get_resume_text(self, resume_id: Optional[int]) -> str:
        """Return raw resume text for the selected (or latest) parsed resume."""
        q = self.db.query(ParsedResumeData).filter_by(user_id=self.user_id)
        if resume_id:
            q = q.filter_by(id=resume_id)
        else:
            q = q.order_by(ParsedResumeData.created_at.desc())
        row = q.first()
        return row.raw_text if row else ""

    def _score_job(
        self, raw: dict, job_roles: list[str], resume_text: str
    ) -> tuple[float, str]:
        """
        Lightweight AI-free scoring based on keyword overlap.
        Falls back gracefully; if an LLM is available it will use it.
        """
        title = (raw.get("title") or "").lower()
        desc  = (raw.get("description") or "").lower()
        # Flatten tags — guard against nested lists from some API responses
        raw_tags = raw.get("tags") or []
        tags = " ".join(str(t) for t in raw_tags if t and not isinstance(t, (list, dict))).lower()

        # Keyword score (0-60 points)
        kw_hits = sum(1 for k in job_roles if k.lower() in f"{title} {tags} {desc}")
        kw_score = min(60.0, (kw_hits / max(len(job_roles), 1)) * 60)

        # Resume keyword overlap (0-40 points)
        resume_score = 0.0
        if resume_text:
            resume_lower = resume_text.lower()
            overlap = sum(1 for k in job_roles if k.lower() in resume_lower)
            resume_score = min(40.0, (overlap / max(len(job_roles), 1)) * 40)

        total = round(kw_score + resume_score, 1)
        reason = (
            f"Matched {kw_hits}/{len(job_roles)} keywords in job listing"
            + (f" — strong resume alignment" if resume_score > 20 else "")
        )

        # Optional LLM upgrade (best-effort, won't block if it fails)
        try:
            if resume_text:
                total, reason = self._llm_score(raw, job_roles, resume_text, total, reason)
        except Exception as exc:
            logger.debug("crawler_llm_score_failed: %s", exc)

        return total, reason

    def _llm_score(
        self, raw: dict, job_roles: list[str], resume_text: str,
        fallback_score: float, fallback_reason: str
    ) -> tuple[float, str]:
        """Use LLM to produce a richer match score. Returns (score, reason)."""
        from app.services.ai.factory import get_llm_provider

        llm = get_llm_provider()
        snippet = resume_text[:1500]
        jd_snippet = f"{raw.get('title')} at {raw.get('company')}\n{(raw.get('description') or '')[:800]}"

        prompt = (
            "You are a recruiter AI. Rate how well this candidate matches the job posting.\n\n"
            f"TARGET ROLES: {', '.join(job_roles)}\n\n"
            f"JOB:\n{jd_snippet}\n\n"
            f"CANDIDATE RESUME EXCERPT:\n{snippet}\n\n"
            "Respond with a JSON object ONLY: "
            '{"score": <0-100 integer>, "reason": "<one sentence max 120 chars>"}'
        )
        response = llm.generate(prompt, max_tokens=120)
        import re, json as _json
        m = re.search(r'\{.*\}', response, re.DOTALL)
        if not m:
            return fallback_score, fallback_reason
        obj = _json.loads(m.group())
        return float(obj.get("score", fallback_score)), str(obj.get("reason", fallback_reason))

    # ── Query helpers ────────────────────────────────────────────────────────

    def list_jobs(
        self,
        date_str: Optional[str] = None,
        show_dismissed: bool = False,
        min_score: Optional[float] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CrawledJob]:
        q = self.db.query(CrawledJob).filter(CrawledJob.user_id == self.user_id)
        if not show_dismissed:
            q = q.filter(CrawledJob.is_dismissed == False)
        if date_str:
            from datetime import date
            d = date.fromisoformat(date_str)
            q = q.filter(
                CrawledJob.crawled_at >= datetime(d.year, d.month, d.day, 0, 0, 0),
                CrawledJob.crawled_at <  datetime(d.year, d.month, d.day, 23, 59, 59),
            )
        if min_score is not None:
            q = q.filter(CrawledJob.match_score >= min_score)
        return (
            q.order_by(CrawledJob.match_score.desc(), CrawledJob.crawled_at.desc())
            .offset(offset).limit(limit).all()
        )

    def action_job(self, job_id: int, is_dismissed: Optional[bool], is_saved: Optional[bool]) -> CrawledJob:
        from fastapi import HTTPException
        job = self.db.query(CrawledJob).filter_by(id=job_id, user_id=self.user_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Crawled job not found")
        if is_dismissed is not None:
            job.is_dismissed = is_dismissed
        if is_saved is not None:
            job.is_saved = is_saved
            if is_saved:
                # Auto-create a job application draft
                app = JobApplication(
                    user_id=self.user_id,
                    company_name=job.company,
                    role_title=job.title,
                    job_description=job.description or f"{job.title} at {job.company}",
                    jd_link=job.apply_url,
                )
                self.db.add(app)
                self.db.flush()
                job.application_id = app.id
        self.db.commit()
        self.db.refresh(job)
        return job
