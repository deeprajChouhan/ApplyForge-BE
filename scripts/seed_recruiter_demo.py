"""
Seed a full recruiter-platform demo tenant — an agency, recruiter logins, clients,
open roles, a rich candidate pool, a live tracking pipeline (with placements), and
some usage history. Everything is agency-scoped, so it's isolated from real data.

Perfect for a live walkthrough: log in as the owner, browse the pool, generate a
shortlist for a role, open the market dashboard, and run the next-hire advisory.

Run from the backend/ directory:
    python scripts/seed_recruiter_demo.py            # create (skips if it exists)
    python scripts/seed_recruiter_demo.py --reset    # wipe the demo tenant + recreate
    python scripts/seed_recruiter_demo.py --create-tables   # also create rec_ tables (fresh DB)

Login credentials printed at the end. All passwords: DemoPass123!
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.recruiter.enums import (  # noqa: E402
    AgencyPlan,
    AgencyStatus,
    ApplicationStage,
    BillingModel,
    EmploymentType,
    RecruiterSeatRole,
    RoleStatus,
    UsageKind,
)
from app.recruiter.models import (  # noqa: E402
    Agency,
    Application,
    CandidateExperience,
    CandidateProfile,
    CandidateSkill,
    Client,
    Recruiter,
    Role,
    Shortlist,
    UsageEvent,
)
from app.recruiter.services.matching import embed_candidate, embed_role  # noqa: E402

SLUG = "northwind-demo"
PASSWORD = "DemoPass123!"
NOW = datetime.utcnow()

# ── Recruiter seats ─────────────────────────────────────────────────────────
RECRUITERS = [
    ("owner@northwind.demo", "Nadia Okafor", RecruiterSeatRole.owner),
    ("alex@northwind.demo", "Alex Rivera", RecruiterSeatRole.recruiter),
    ("sam@northwind.demo", "Sam Chen", RecruiterSeatRole.recruiter),
]

# ── Clients ─────────────────────────────────────────────────────────────────
CLIENTS = [
    ("Meridian SaaS", "B2B Software"),
    ("Atlas Financial", "Banking & Fintech"),
    ("Harborview Retail", "Retail & CPG"),
]

# Canonical skill bundles (must match app.recruiter.services.skills vocabulary).
SALES = ["sales", "account management", "negotiation", "crm", "lead generation", "pipeline management"]
CS = ["customer success", "account management", "onboarding", "customer support", "churn reduction", "saas"]
FIN = ["financial analysis", "financial modeling", "forecasting", "budgeting", "excel", "accounting"]
MKT = ["digital marketing", "seo", "content marketing", "google analytics", "social media", "email marketing"]
OPS = ["operations management", "supply chain", "process improvement", "logistics", "vendor management", "six sigma"]
HR = ["recruiting", "talent acquisition", "employee relations", "onboarding", "performance management", "hris"]

# ── Roles: (title, client_idx, seniority, min_years, salary, required, preferred) ──
ROLES = [
    ("Account Executive", 0, "mid", 3, (70_000, 110_000), SALES[:4], ["saas", "quota attainment"]),
    ("Customer Success Manager", 0, "mid", 4, (75_000, 115_000), CS[:4], ["stakeholder management"]),
    # 'valuation' and 'sem' are deliberately scarce in the pool → the market
    # dashboard surfaces them as skill shortages (demand with no supply).
    ("Financial Analyst", 1, "mid", 2, (65_000, 95_000), FIN[:4], ["fp&a", "tableau", "valuation"]),
    ("HR Business Partner", 1, "senior", 5, (80_000, 120_000), HR[:4], ["compensation and benefits", "leadership"]),
    ("Marketing Manager", 2, "senior", 5, (90_000, 130_000), MKT[:4], ["hubspot", "brand management", "sem"]),
    ("Operations Manager", 2, "senior", 6, (95_000, 140_000), OPS[:4], ["six sigma", "leadership"]),
]

# ── Candidates: (name, location, years, function-skills, extra-skills) ──
# Function bundles give each role strong + partial matches; extras add texture.
CANDIDATES = [
    # Sales
    ("Priya Nair", "Austin, TX", 6, SALES, ["saas", "quota attainment", "leadership"]),
    ("Marcus Bell", "Chicago, IL", 4, SALES[:5], ["saas"]),
    ("Elena Duarte", "Remote", 3, SALES[:4], ["communication"]),
    ("Tom Vasquez", "Denver, CO", 8, SALES, ["leadership", "stakeholder management", "quota attainment"]),
    # Customer success
    ("Hannah Weiss", "Boston, MA", 5, CS, ["stakeholder management", "leadership"]),
    ("Diego Santos", "Remote", 3, CS[:5], ["communication"]),
    ("Aisha Bello", "Atlanta, GA", 4, CS[:4], ["account management"]),
    # Finance
    ("Raj Mehta", "New York, NY", 7, FIN, ["fp&a", "tableau"]),
    ("Sophie Laurent", "Remote", 3, FIN[:5], ["fp&a"]),
    ("Kenji Watanabe", "San Jose, CA", 5, FIN, ["gaap", "quickbooks"]),
    # HR
    ("Grace Adeyemi", "Charlotte, NC", 9, HR, ["compensation and benefits", "leadership", "stakeholder management"]),
    ("Liam O'Brien", "Remote", 4, HR[:5], ["communication"]),
    ("Nina Patel", "Seattle, WA", 6, HR, ["hris", "performance management"]),
    # Marketing
    ("Carlos Nunez", "Miami, FL", 7, MKT, ["hubspot", "brand management", "paid advertising"]),
    ("Yuki Tanaka", "Remote", 4, MKT[:5], ["copywriting"]),
    ("Bella Rossi", "Los Angeles, CA", 5, MKT, ["campaign management", "google analytics"]),
    # Operations
    ("Omar Haddad", "Houston, TX", 10, OPS, ["six sigma", "leadership", "inventory management"]),
    ("Fatima Zahra", "Remote", 5, OPS[:5], ["vendor management"]),
    ("Peter Novak", "Columbus, OH", 6, OPS, ["inventory management", "process improvement"]),
    # Generalist / analyst crossover
    ("Zoe Campbell", "Remote", 4, ["data analysis", "excel", "powerpoint"], ["financial analysis", "tableau", "communication"]),
]


def _purge(db) -> None:
    ag = db.query(Agency).filter(Agency.slug == SLUG).first()
    if not ag:
        return
    aid = ag.id
    # Delete children not covered by ORM cascade, then the agency (cascades the rest).
    cand_ids = [c.id for c in db.query(CandidateProfile.id).filter(CandidateProfile.agency_id == aid)]
    db.query(Application).filter(Application.agency_id == aid).delete(synchronize_session=False)
    db.query(UsageEvent).filter(UsageEvent.agency_id == aid).delete(synchronize_session=False)
    db.query(Shortlist).filter(Shortlist.agency_id == aid).delete(synchronize_session=False)
    if cand_ids:
        db.query(CandidateSkill).filter(CandidateSkill.candidate_id.in_(cand_ids)).delete(synchronize_session=False)
        db.query(CandidateExperience).filter(CandidateExperience.candidate_id.in_(cand_ids)).delete(synchronize_session=False)
    db.query(CandidateProfile).filter(CandidateProfile.agency_id == aid).delete(synchronize_session=False)
    db.query(Role).filter(Role.agency_id == aid).delete(synchronize_session=False)
    db.query(Client).filter(Client.agency_id == aid).delete(synchronize_session=False)
    db.query(Recruiter).filter(Recruiter.agency_id == aid).delete(synchronize_session=False)
    db.delete(ag)
    db.commit()
    print("• Purged existing demo tenant.")


def _cv_text(name: str, headline: str, years: int, skills: list[str]) -> str:
    return (
        f"{name}\n{headline}\n{years} years of experience.\n"
        f"Core strengths: {', '.join(skills)}.\n"
        f"Delivered measurable results across cross-functional teams and owned outcomes end to end."
    )


def seed(reset: bool, create_tables: bool) -> None:
    db = SessionLocal()
    try:
        if create_tables:
            from app.recruiter.models import RECRUITER_TABLES
            from app.db.base import Base  # noqa: F401

            for table in RECRUITER_TABLES:
                table.create(bind=engine, checkfirst=True)
            print("• Ensured rec_ tables exist.")

        existing = db.query(Agency).filter(Agency.slug == SLUG).first()
        if existing and not reset:
            print(f"Demo agency '{SLUG}' already exists (id={existing.id}). Use --reset to rebuild.")
            return
        if reset:
            _purge(db)

        # ── Agency (Pro plan so every feature is unlocked; active subscription so
        #    it's never trial-locked) ──
        agency = Agency(
            name="Northwind Talent",
            slug=SLUG,
            plan=AgencyPlan.pro,
            billing_model=BillingModel.flat,
            status=AgencyStatus.active,
            subscription_status="active",
            seat_limit=None,
        )
        db.add(agency)
        db.flush()

        # ── Recruiter seats ──
        for email, full_name, role in RECRUITERS:
            db.add(
                Recruiter(
                    agency_id=agency.id,
                    email=email,
                    full_name=full_name,
                    role=role,
                    password_hash=hash_password(PASSWORD),
                    is_active=True,
                )
            )

        # ── Clients ──
        clients: list[Client] = []
        for name, industry in CLIENTS:
            c = Client(agency_id=agency.id, name=name, industry=industry)
            db.add(c)
            clients.append(c)
        db.flush()

        # ── Roles ──
        roles: list[Role] = []
        for title, client_idx, seniority, min_years, (smin, smax), required, preferred in ROLES:
            r = Role(
                agency_id=agency.id,
                client_id=clients[client_idx].id,
                title=title,
                description=f"{title} for {clients[client_idx].name}. Own outcomes and partner across the business.",
                status=RoleStatus.open,
                employment_type=EmploymentType.full_time,
                location="Remote / Hybrid",
                seniority=seniority,
                required_skills=list(dict.fromkeys(required)),
                preferred_skills=list(dict.fromkeys(preferred)),
                min_years_experience=min_years,
                salary_min=smin,
                salary_max=smax,
            )
            db.add(r)
            db.flush()
            r.embedding = embed_role(r)
            roles.append(r)

        # ── Candidate pool ──
        candidates: list[CandidateProfile] = []
        for name, location, years, base_skills, extras in CANDIDATES:
            skills = list(dict.fromkeys(base_skills + extras))
            headline = f"{name.split()[0]} — {years}y professional"
            profile = CandidateProfile(
                agency_id=agency.id,
                full_name=name,
                email=f"{name.split()[0].lower()}.{name.split()[-1].lower()}@example.com",
                phone=None,
                headline=f"{skills[0].title()} specialist",
                location=location,
                years_experience=float(years),
                summary=f"{years} years across {', '.join(skills[:3])} and adjacent work.",
                raw_cv_text=_cv_text(name, headline, years, skills),
            )
            db.add(profile)
            db.flush()
            for s in skills:
                db.add(CandidateSkill(candidate_id=profile.id, name=s))
            # A little dated work history for realism.
            db.add(
                CandidateExperience(
                    candidate_id=profile.id,
                    title=f"{skills[0].title()} Lead" if years >= 6 else skills[0].title(),
                    company="Prior Employer",
                    start_date=(NOW - timedelta(days=365 * min(years, 6))).date(),
                    end_date=None,
                    description=f"Owned {skills[0]} initiatives.",
                )
            )
            db.flush()
            db.refresh(profile)
            profile.embedding = embed_candidate(profile)
            candidates.append(profile)

        db.flush()

        # ── Tracking pipeline: applications across stages, incl. placements ──
        by_name = {c.full_name: c for c in candidates}
        # (candidate, role_idx, stage, days_ago_created, days_ago_activity)
        pipeline = [
            ("Priya Nair", 0, ApplicationStage.placed, 42, 8),
            ("Tom Vasquez", 0, ApplicationStage.offer, 20, 3),
            ("Marcus Bell", 0, ApplicationStage.interview, 14, 2),
            ("Elena Duarte", 0, ApplicationStage.screening, 9, 1),
            ("Hannah Weiss", 1, ApplicationStage.placed, 36, 5),
            ("Diego Santos", 1, ApplicationStage.interview, 12, 2),
            ("Aisha Bello", 1, ApplicationStage.sourced, 5, 5),
            ("Raj Mehta", 2, ApplicationStage.offer, 18, 4),
            ("Sophie Laurent", 2, ApplicationStage.screening, 8, 2),
            ("Grace Adeyemi", 3, ApplicationStage.interview, 15, 3),
            ("Liam O'Brien", 3, ApplicationStage.submitted, 6, 1),
            ("Carlos Nunez", 4, ApplicationStage.screening, 10, 2),
            ("Omar Haddad", 5, ApplicationStage.submitted, 7, 1),
            ("Fatima Zahra", 5, ApplicationStage.rejected, 22, 12),
        ]
        for cand_name, role_idx, stage, created_days, activity_days in pipeline:
            cand = by_name.get(cand_name)
            if cand is None:
                continue
            role = roles[role_idx]
            app = Application(
                agency_id=agency.id,
                candidate_id=cand.id,
                role_id=role.id,
                company_name=None,
                job_title=role.title,
                stage=stage,
                notes=None,
            )
            app.created_at = NOW - timedelta(days=created_days)
            app.last_activity_at = NOW - timedelta(days=activity_days)
            db.add(app)

        # ── Usage history (so the owner/operator usage views show numbers) ──
        usage = [
            (UsageKind.cv_ingested, len(candidates)),
            (UsageKind.shortlist_generated, 6),
            (UsageKind.listing_drafted, 3),
            (UsageKind.role_match_run, 5),
            (UsageKind.advisory_run, 2),
        ]
        for kind, qty in usage:
            db.add(UsageEvent(agency_id=agency.id, kind=kind.value, quantity=qty, created_at=NOW))

        db.commit()

        print("\n✅ Demo tenant ready\n" + "=" * 44)
        print(f"Agency:      Northwind Talent  (slug: {SLUG}, plan: pro)")
        print(f"Clients:     {len(clients)}   Roles: {len(roles)}   Candidates: {len(candidates)}   Pipeline: {len(pipeline)}")
        print("\nLogins (password for all: DemoPass123!):")
        for email, full_name, role in RECRUITERS:
            print(f"  • {role.value:9s} {full_name:16s} {email}")
        print("\nDemo flow to show:")
        print("  1. Log in as owner → Pool: browse ~20 candidates with skills")
        print("  2. Roles → pick 'Account Executive' → Generate shortlist (ranked fits + reasons)")
        print("  3. Market → demand vs supply, skill shortages, salary bands, pipeline funnel")
        print("  4. Clients → 'Meridian SaaS' → Next hire (AI advisory)")
        print("  5. Team & plan (owner) → seats, usage, invites, billing")
        print("=" * 44)
    finally:
        db.close()


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv, create_tables="--create-tables" in sys.argv)
