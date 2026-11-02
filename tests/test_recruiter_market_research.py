"""Market research keeps only postings comparable to the role's domain."""
from __future__ import annotations

from datetime import datetime

from app.db.session import SessionLocal
from app.recruiter.models import Agency, Role
from app.recruiter.services import market_crawler as mc
from app.recruiter.services.market_crawler import ExternalPosting, build_research_profile, crawl_role_market

REQ = ["embedded c", "c++", "linux", "mqtt", "can", "firmware development"]
PREF = ["embedded linux", "yocto", "raspberry pi", "ota", "azure iot", "python", "bsp", "device drivers"]

FIRMWARE_TEXT = (
    "Build embedded Linux firmware for our IoT gateway. Strong Embedded C and C++, "
    "MQTT, CAN bus, Yocto, OTA updates, device drivers. Firmware development on ARM."
)

LOCAL = [  # Adzuna India, INR
    ExternalPosting("Embedded Firmware Engineer", 1_800_000, 2_600_000, "INR", [], "adzuna", FIRMWARE_TEXT, "Pune", "in"),
    ExternalPosting("Senior Embedded Linux Developer", 2_200_000, 3_000_000, "INR", [], "adzuna", FIRMWARE_TEXT, "Bengaluru", "in"),
    ExternalPosting("IoT Firmware Engineer", 1_500_000, 2_400_000, "INR", [], "adzuna", FIRMWARE_TEXT, "Hyderabad", "in"),
    ExternalPosting("Firmware Engineer - Telematics", 2_000_000, 2_800_000, "INR", [], "adzuna", FIRMWARE_TEXT, "Pune", "in"),
]
NOISE = [  # what the old crawler returned
    ExternalPosting("Director, Site Reliability Engineering", 180_000, 240_000, "USD", ["exec", "engineer"], "remoteok",
                    "Lead SRE org. Kubernetes, Linux, Terraform.", "Remote"),
    ExternalPosting("Security Research Engineer", 150_000, 200_000, "USD", ["security", "dev"], "remoteok",
                    "Find vulnerabilities in web apps. Python, Go.", "Remote"),
    ExternalPosting("Account Development Representative - France", None, None, "EUR", ["marketing", "legion"], "arbeitnow",
                    "Sales development. You can prospect.", "Paris"),
    ExternalPosting("Linux Systems Administrator", 90_000, 120_000, "USD", ["linux", "sysadmin"], "remoteok",
                    "Administer Linux servers, bash, ansible. You can be on call.", "Remote"),
    ExternalPosting("AI agent engineer", 160_000, 220_000, "USD", ["ai", "engineer"], "remoteok",
                    "LLM agents in Python.", "Remote"),
]


def _role(db):
    ag = Agency(name="Mkt Co", slug=f"mkt-{datetime.utcnow().timestamp()}")
    db.add(ag); db.commit(); db.refresh(ag)
    role = Role(agency_id=ag.id, title="Embedded Linux / IoT Gateway Firmware Engineer",
                location="Pune, India", country_code="IN", budget_currency="INR",
                required_skills=REQ, preferred_skills=PREF)
    db.add(role); db.commit(); db.refresh(role)
    return ag, role


def test_profile_uses_domain_not_generic_words():
    prof = build_research_profile(
        Role(title="Embedded Linux / IoT Gateway Firmware Engineer", country_code="IN",
             budget_currency="INR", required_skills=REQ, preferred_skills=PREF),
        use_llm=False,
    )
    assert "engineer" not in prof.domain_terms
    assert "embedded linux" in prof.domain_terms and "firmware" in prof.domain_terms
    assert "linux" not in prof.domain_terms  # bare skill word is not an anchor
    assert any("firmware engineer" in q for q in prof.queries)
    assert prof.country == "in" and prof.currency == "INR"


def test_crawl_filters_noise_and_uses_local_band(monkeypatch):
    monkeypatch.setitem(mc._SOURCES, "adzuna", lambda q, p: list(LOCAL))
    monkeypatch.setitem(mc._SOURCES, "remoteok", lambda q, p: [n for n in NOISE if n.source == "remoteok"])
    monkeypatch.setitem(mc._SOURCES, "arbeitnow", lambda q, p: [n for n in NOISE if n.source == "arbeitnow"])
    db = SessionLocal()
    ag, role = _role(db)
    snap = crawl_role_market(db, ag.id, role=role, use_llm=False)
    db.refresh(role)
    m = role.market_snapshot
    assert snap.sample_size == 4 and m["scanned"] == 9
    assert set(snap.competing_roles) == {p.title for p in LOCAL}
    assert m["salary_basis"] == "local" and snap.currency == "INR"
    assert 1_500_000 <= snap.salary_p25 <= snap.salary_p50 <= snap.salary_p75 <= 3_000_000
    demand = {d["skill"]: d["pct"] for d in m["skill_demand"]}
    assert demand["embedded c"] == 100 and demand["mqtt"] == 100 and demand["can"] == 100
    for junk in ("legion", "exec", "marketing", "engineer", "dev"):
        assert junk not in snap.top_skills
    assert snap.sources == ["adzuna"]
    db.close()


def test_global_remote_band_is_converted_and_labelled(monkeypatch):
    remote = [ExternalPosting(f"Embedded Firmware Engineer {i}", 100_000 + i * 10_000, 140_000 + i * 10_000, "USD",
                              ["c++"], "remoteok", FIRMWARE_TEXT, "Remote") for i in range(4)]
    monkeypatch.setitem(mc._SOURCES, "adzuna", lambda q, p: [])
    monkeypatch.setitem(mc._SOURCES, "remoteok", lambda q, p: remote)
    monkeypatch.setitem(mc._SOURCES, "arbeitnow", lambda q, p: [])
    db = SessionLocal()
    ag, role = _role(db)
    snap = crawl_role_market(db, ag.id, role=role, use_llm=False)
    db.refresh(role)
    m = role.market_snapshot
    assert m["salary_basis"] == "global" and "not the local market" in m["salary_note"]
    assert snap.currency == "INR" and snap.salary_p50 > 5_000_000  # USD converted, not relabelled
    db.close()


def test_too_few_paid_postings_gives_no_band(monkeypatch):
    monkeypatch.setitem(mc._SOURCES, "adzuna", lambda q, p: LOCAL[:2])
    monkeypatch.setitem(mc._SOURCES, "remoteok", lambda q, p: list(NOISE))
    monkeypatch.setitem(mc._SOURCES, "arbeitnow", lambda q, p: [])
    db = SessionLocal()
    ag, role = _role(db)
    snap = crawl_role_market(db, ag.id, role=role, use_llm=False)
    db.refresh(role)
    assert snap.salary_p50 is None and "too few" in role.market_snapshot["salary_note"]
    db.close()
