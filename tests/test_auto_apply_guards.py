"""Regression tests: auto-apply must stop for inactive users, respect caps
across all stages, and honour the global kill switch."""
from datetime import datetime
from unittest.mock import patch

import pytest

from app.core import config as app_config
from app.models.auto_apply import AutoApplySettings
from app.models.models import JobApplication, User
from app.services.auto_apply import dispatcher, orchestrator
from tests.conftest import TestingSessionLocal


@pytest.fixture(autouse=True)
def _session():
    with patch.object(orchestrator, "SessionLocal", TestingSessionLocal), \
         patch.object(dispatcher, "SessionLocal", TestingSessionLocal):
        yield


def _mk_user(db, active=True, aa_active=True, daily_cap=5, weekly_cap=50):
    u = User(email=f"u{datetime.utcnow().timestamp()}@x.com", password_hash="x", is_active=active)
    db.add(u); db.commit(); db.refresh(u)
    db.add(AutoApplySettings(user_id=u.id, is_active=aa_active, daily_cap=daily_cap,
                             weekly_cap=weekly_cap, target_titles_json=["engineer"]))
    db.commit()
    return u.id


def _mk_app(db, uid, stage):
    db.add(JobApplication(user_id=uid, company_name="c", role_title="r", job_description="d",
                          status="draft", auto_apply_stage=stage))
    db.commit()


def test_kill_switch_blocks_everything(monkeypatch):
    monkeypatch.setattr(app_config.settings, "auto_apply_enabled", False)
    with TestingSessionLocal() as db:
        uid = _mk_user(db)
    assert orchestrator.tick_all()["reason"] == "auto_apply_disabled_globally"
    assert orchestrator.tick_user(uid)["reason"] == "auto_apply_disabled_globally"


def test_inactive_user_not_ticked(monkeypatch):
    monkeypatch.setattr(app_config.settings, "auto_apply_enabled", True)
    with TestingSessionLocal() as db:
        active = _mk_user(db, active=True)
        dead = _mk_user(db, active=False)
    with patch.object(orchestrator.tick_user, "delay") as d:
        orchestrator.tick_all()
    ticked = {c.args[0] for c in d.call_args_list}
    assert active in ticked and dead not in ticked
    assert orchestrator.tick_user(dead)["reason"] == "user_inactive"


def test_cap_counts_all_stages(monkeypatch):
    monkeypatch.setattr(app_config.settings, "auto_apply_enabled", True)
    with TestingSessionLocal() as db:
        uid = _mk_user(db, daily_cap=3)
        for st in ("awaiting_review", "failed", "preparing"):
            _mk_app(db, uid, st)
    r = orchestrator.tick_user(uid)
    assert r.get("reason") == "cap_reached", r


def test_dispatcher_cancels_when_user_deactivated(monkeypatch):
    monkeypatch.setattr(app_config.settings, "auto_apply_enabled", True)
    with TestingSessionLocal() as db:
        uid = _mk_user(db, active=False)
        _mk_app(db, uid, "queued")
        app_id = db.query(JobApplication).filter_by(user_id=uid).one().id
    r = dispatcher.prepare_application(app_id)
    assert r["reason"] == "user_inactive"
    with TestingSessionLocal() as db:
        assert db.get(JobApplication, app_id).auto_apply_stage == "cancelled"
