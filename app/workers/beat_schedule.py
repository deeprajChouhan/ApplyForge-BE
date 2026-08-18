from celery.schedules import crontab

BEAT_SCHEDULE = {
    "poll-greenhouse": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=0, hour="*"), "args": ["greenhouse"]},
    "poll-lever": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=5, hour="*"), "args": ["lever"]},
    "poll-ashby": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=10, hour="*"), "args": ["ashby"]},
    "poll-workable": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=15, hour="*/3"), "args": ["workable"]},
    "poll-smartrecruiters": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=20, hour="*/3"), "args": ["smartrecruiters"]},
    "poll-jsonld": {"task": "app.services.ats.tasks.poll_provider", "schedule": crontab(minute=30, hour="4"), "args": ["jsonld"]},
    # Auto-apply orchestrator — fans out per active user every 15 minutes.
    "auto-apply-tick-all": {"task": "app.services.auto_apply.orchestrator.tick_all", "schedule": crontab(minute="*/15"), "args": []},
}
