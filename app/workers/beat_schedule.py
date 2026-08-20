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
}
