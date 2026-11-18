from celery.schedules import crontab

BEAT_SCHEDULE = {
    # ── ATS discovery (full sweep, source of truth) ───────────────────────
    # Providers with API rate limits or lots of stable postings poll hourly;
    # noisier ones every few hours; jsonld daily.
    "poll-greenhouse": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=0, hour="*"), "args": ["greenhouse"]},
    "poll-lever": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=5, hour="*"), "args": ["lever"]},
    "poll-ashby": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=10, hour="*"), "args": ["ashby"]},
    "poll-workable": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=15, hour="*/3"), "args": ["workable"]},
    "poll-smartrecruiters": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=20, hour="*/3"), "args": ["smartrecruiters"]},
    "poll-jsonld": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=30, hour="4"), "args": ["jsonld"]},

    # ── Per-user live discovery (fast delta layer) ────────────────────────
    # Hits Greenhouse boards filtered by each user's target titles and
    # immediately triggers the orchestrator when it finds new matches.
    # Runs every 2 minutes so the app feels "always searching."
    "live-search-all": {"task": "app.services.discovery.live_search.search_for_all_users", "schedule": crontab(minute="*/2"), "args": []},

    # ── Auto-apply orchestrator ───────────────────────────────────────────
    # Baseline safety net at 5 min intervals — live_search triggers ad-hoc
    # ticks whenever it finds new jobs, so this mostly just handles
    # daily_cap timing and picks up jobs that arrived via the hourly poll.
    "auto-apply-tick-all": {"task": "app.services.auto_apply.orchestrator.tick_all", "schedule": crontab(minute="*/5"), "args": []},

    # ── Recruiter Partner API ─────────────────────────────────────────────
    # Sweep due webhook retries every minute; purge expired logs nightly.
    "partner-webhook-retries": {"task": "app.recruiter.partner.tasks.deliver_webhooks", "schedule": crontab(minute="*"), "args": []},
    # ── Email (EMAIL_JOBS_BACKEND=celery) ─────────────────────────────────
    # UTC times; 07:30/09:00 UK ≈ 07:30/09:00 UTC in winter, 06:30/08:00 UTC in summer.
    "email-sweep": {"task": "app.services.email.tasks.sweep", "schedule": crontab(minute="*"), "args": []},
    "email-daily-rules": {"task": "app.services.email.tasks.daily_rules", "schedule": crontab(minute=0, hour=9), "args": []},
    "email-daily-briefs": {"task": "app.services.email.tasks.daily_briefs", "schedule": crontab(minute=30, hour=7, day_of_week="1-5"), "args": []},
    "email-weekly-digests": {"task": "app.services.email.tasks.weekly_digests", "schedule": crontab(minute=0, hour=8, day_of_week=1), "args": []},
    "email-webhook-purge": {"task": "app.services.email.tasks.purge_webhook_events", "schedule": crontab(minute=40, hour=3), "args": []},
    "partner-log-retention": {"task": "app.recruiter.partner.tasks.purge_old_logs", "schedule": crontab(minute=17, hour="3"), "args": []},
}
