"""
Seed script: creates a fully populated demo user "John Doe" with
realistic profile, work history, skills, projects, certifications,
job applications (multiple statuses), generated documents, and
parsed resume data — perfect for taking screenshots.

Run from backend/ directory:
    python scripts/seed_john_doe.py

Re-running is safe — idempotent on the user record.
"""
import json
import sys
import os
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.enums import (
    ApplicationStatus, DocumentType, FeatureFlag,
    FileType, PlanTier, SubscriptionStatus, UserRole,
)
from app.models.models import (
    PLAN_TOKEN_BUDGETS,
    Certification, Education, GeneratedDocument, JobApplication,
    ParsedResumeData, Project, Skill, UploadedFile,
    User, UserFeature, UserProfile, WorkExperience,
)

EMAIL = "john.doe@example.com"
PASSWORD = "Demo@1234"


def seed():
    db = SessionLocal()
    try:
        # ── 1. User ────────────────────────────────────────────────────────
        user = db.query(User).filter(User.email.ilike(EMAIL)).first()
        if user:
            print(f"[~] User already exists (id={user.id}), refreshing data...")
        else:
            user = User(
                email=EMAIL,
                password_hash=hash_password(PASSWORD),
                role=UserRole.user,
                plan=PlanTier.pro,
                subscription_status=SubscriptionStatus.active,
                token_budget_monthly=PLAN_TOKEN_BUDGETS[PlanTier.pro],
            )
            db.add(user)
            db.flush()
            print(f"[+] Created user {EMAIL} (id={user.id})")

        uid = user.id

        # Grant all pro features
        for feat in [FeatureFlag.jd_analyze, FeatureFlag.applications,
                     FeatureFlag.kanban, FeatureFlag.resume, FeatureFlag.chat]:
            if not db.query(UserFeature).filter_by(user_id=uid, feature=feat).first():
                db.add(UserFeature(user_id=uid, feature=feat, enabled=True))

        # ── 2. Profile ─────────────────────────────────────────────────────
        profile = db.query(UserProfile).filter_by(user_id=uid).first()
        if not profile:
            profile = UserProfile(user_id=uid)
            db.add(profile)
        profile.full_name = "John Doe"
        profile.headline = "Senior Full Stack Engineer · AI & Cloud Specialist"
        profile.location = "San Francisco, CA"
        profile.summary = (
            "Passionate software engineer with 7+ years building scalable web applications "
            "and AI-powered products. Experienced in Python, React, and cloud-native architectures. "
            "Proven track record of leading cross-functional teams and delivering high-impact features "
            "at both startups and Fortune 500 companies. Strong communicator with a product-first mindset."
        )

        # ── 3. Work Experience ─────────────────────────────────────────────
        if not db.query(WorkExperience).filter_by(user_id=uid).first():
            experiences = [
                WorkExperience(
                    user_id=uid,
                    company="Stripe", role="Senior Software Engineer",
                    start_date=date(2022, 3, 1), end_date=None,
                    description=(
                        "Led development of the Stripe Radar fraud detection dashboard, reducing false positives by 34%. "
                        "Built real-time streaming pipelines processing 2M+ events/day using Kafka and Python. "
                        "Mentored 4 junior engineers and drove adoption of TypeScript across 3 frontend teams. "
                        "Collaborated with ML team to deploy GPT-4 powered dispute resolution assistant."
                    ),
                ),
                WorkExperience(
                    user_id=uid,
                    company="Airbnb", role="Full Stack Engineer",
                    start_date=date(2019, 6, 1), end_date=date(2022, 2, 28),
                    description=(
                        "Developed host onboarding flow that increased listing creation rate by 28%. "
                        "Built A/B testing infrastructure used by 40+ product teams. "
                        "Migrated legacy Rails monolith to microservices architecture using Node.js and GraphQL. "
                        "Implemented accessibility improvements achieving WCAG 2.1 AA compliance."
                    ),
                ),
                WorkExperience(
                    user_id=uid,
                    company="Palantir Technologies", role="Software Engineer",
                    start_date=date(2017, 8, 1), end_date=date(2019, 5, 31),
                    description=(
                        "Built data pipeline tooling for government and healthcare clients. "
                        "Developed interactive dashboards using React and D3.js for geospatial analytics. "
                        "Reduced data ingestion latency by 60% through Apache Spark optimizations."
                    ),
                ),
            ]
            db.add_all(experiences)
            print("[+] Added 3 work experiences")

        # ── 4. Education ───────────────────────────────────────────────────
        if not db.query(Education).filter_by(user_id=uid).first():
            db.add_all([
                Education(
                    user_id=uid,
                    institution="University of California, Berkeley",
                    degree="Bachelor of Science",
                    field_of_study="Computer Science",
                    start_date=date(2013, 9, 1), end_date=date(2017, 5, 31),
                ),
                Education(
                    user_id=uid,
                    institution="Stanford University (Online)",
                    degree="Certificate",
                    field_of_study="Machine Learning",
                    start_date=date(2021, 1, 1), end_date=date(2021, 6, 30),
                ),
            ])
            print("[+] Added 2 education entries")

        # ── 5. Skills ──────────────────────────────────────────────────────
        if not db.query(Skill).filter_by(user_id=uid).first():
            skills_data = [
                ("Python", "Expert"), ("TypeScript", "Expert"), ("React", "Expert"),
                ("Node.js", "Advanced"), ("PostgreSQL", "Advanced"), ("Redis", "Advanced"),
                ("AWS", "Advanced"), ("Kubernetes", "Intermediate"), ("GraphQL", "Advanced"),
                ("Docker", "Expert"), ("Apache Kafka", "Intermediate"), ("Terraform", "Intermediate"),
                ("LLM Integration", "Advanced"), ("FastAPI", "Expert"), ("Next.js", "Advanced"),
            ]
            db.add_all([Skill(user_id=uid, name=n, level=l) for n, l in skills_data])
            print(f"[+] Added {len(skills_data)} skills")

        # ── 6. Projects ────────────────────────────────────────────────────
        if not db.query(Project).filter_by(user_id=uid).first():
            db.add_all([
                Project(
                    user_id=uid,
                    name="AI Code Review Bot",
                    description="Built an automated code review assistant using GPT-4 and GitHub Actions. Reduced PR review time by 40% across a 20-person engineering team.",
                    technologies="Python, FastAPI, OpenAI API, GitHub Actions, PostgreSQL",
                ),
                Project(
                    user_id=uid,
                    name="Real-Time Collaborative IDE",
                    description="Open-source browser-based IDE supporting real-time multi-user editing with operational transforms. 2.4k GitHub stars.",
                    technologies="React, Node.js, WebSockets, Monaco Editor, Redis",
                ),
                Project(
                    user_id=uid,
                    name="Distributed Task Scheduler",
                    description="High-throughput task scheduling system handling 500k+ jobs/day with priority queuing, retries, and dead letter queues.",
                    technologies="Python, Celery, Redis, PostgreSQL, Docker, Prometheus",
                ),
            ])
            print("[+] Added 3 projects")

        # ── 7. Certifications ──────────────────────────────────────────────
        if not db.query(Certification).filter_by(user_id=uid).first():
            db.add_all([
                Certification(user_id=uid, name="AWS Certified Solutions Architect – Professional", issuer="Amazon Web Services", issue_date=date(2023, 4, 1)),
                Certification(user_id=uid, name="Google Cloud Professional Data Engineer", issuer="Google Cloud", issue_date=date(2022, 11, 1)),
                Certification(user_id=uid, name="Kubernetes Application Developer (CKAD)", issuer="CNCF", issue_date=date(2023, 8, 1)),
            ])
            print("[+] Added 3 certifications")

        # ── 8. Job Applications ────────────────────────────────────────────
        apps_data = [
            {
                "company_name": "OpenAI",
                "role_title": "Senior Software Engineer – API Platform",
                "status": ApplicationStatus.interview,
                "jd_analysis_json": json.dumps({
                    "keywords": ["API design", "Python", "distributed systems", "LLM", "scalability", "OpenAPI", "FastAPI", "Kubernetes"],
                    "required_skills": ["Python", "API design", "distributed systems", "PostgreSQL"],
                    "preferred_skills": ["LLM integration", "Kubernetes", "Rust"],
                    "strengths": ["Strong Python + FastAPI match", "LLM integration experience", "Distributed systems background at Stripe"],
                    "unsupported_gaps": ["Rust experience not on resume"],
                    "fit_summary": "Excellent match — John's Stripe experience with high-throughput Python APIs and LLM work aligns directly with this role's requirements.",
                }),
                "resume_content": """JOHN DOE
john.doe@example.com | linkedin.com/in/johndoe | San Francisco, CA

PROFESSIONAL SUMMARY
Senior engineer with 7+ years building scalable APIs and AI-powered products. Deep expertise in Python, distributed systems, and LLM integration. Led teams at Stripe delivering fraud detection systems processing 2M+ events/day.

EXPERIENCE
Senior Software Engineer — Stripe (Mar 2022 – Present)
• Led Stripe Radar dashboard reducing fraud false positives by 34%
• Built Python streaming pipelines processing 2M+ events/day via Kafka
• Deployed GPT-4 powered dispute resolution assistant, cutting resolution time 45%

Full Stack Engineer — Airbnb (Jun 2019 – Feb 2022)
• Increased host listing creation rate 28% through improved onboarding flow
• Built A/B testing infrastructure used by 40+ product teams

Software Engineer — Palantir Technologies (Aug 2017 – May 2019)
• Reduced data ingestion latency 60% via Apache Spark optimizations

SKILLS: Python · FastAPI · TypeScript · React · PostgreSQL · Kafka · AWS · Kubernetes · LLM Integration

EDUCATION: B.S. Computer Science, UC Berkeley (2017) | ML Certificate, Stanford Online (2021)

CERTIFICATIONS: AWS Certified Solutions Architect – Professional | CKAD""",

                "cover_letter": """Dear OpenAI Hiring Team,

I'm writing to express my strong interest in the Senior Software Engineer – API Platform role at OpenAI. Your mission to build safe and beneficial AI aligns deeply with my professional values and recent work integrating large language models into production systems at Stripe.

At Stripe, I led the development of a GPT-4 powered dispute resolution assistant that reduced average resolution time by 45%. Building this required deep collaboration with ML researchers to design an API layer that could reliably serve 50,000+ inferences per day — directly relevant to the API Platform work at OpenAI.

My experience with high-throughput Python services (2M+ events/day at Stripe), Kubernetes orchestration, and distributed PostgreSQL systems maps well to what you're looking for. I'm particularly excited about the opportunity to work on APIs that millions of developers depend on globally.

I'd love to discuss how my background could contribute to OpenAI's platform team.

Warmly,
John Doe""",
            },
            {
                "company_name": "Anthropic",
                "role_title": "Staff Engineer – Infrastructure",
                "status": ApplicationStatus.applied,
                "jd_analysis_json": json.dumps({
                    "keywords": ["infrastructure", "Python", "Kubernetes", "reliability", "distributed systems", "SRE", "Terraform", "observability"],
                    "required_skills": ["Kubernetes", "Terraform", "Python", "distributed systems"],
                    "preferred_skills": ["Rust", "eBPF", "ML infrastructure"],
                    "strengths": ["Kubernetes expertise", "distributed systems at Stripe", "Terraform certified"],
                    "unsupported_gaps": ["No ML infrastructure specific experience"],
                    "fit_summary": "Strong infrastructure fit. ML infrastructure gap is manageable given John's rapid learning track record.",
                }),
                "resume_content": None,
                "cover_letter": None,
            },
            {
                "company_name": "Figma",
                "role_title": "Senior Full Stack Engineer",
                "status": ApplicationStatus.offer,
                "jd_analysis_json": json.dumps({
                    "keywords": ["React", "TypeScript", "WebSockets", "real-time collaboration", "performance", "Node.js", "PostgreSQL"],
                    "required_skills": ["React", "TypeScript", "Node.js", "real-time systems"],
                    "preferred_skills": ["WebGL", "CRDT", "Rust"],
                    "strengths": ["Real-time collaboration project (IDE)", "React expert", "TypeScript expert"],
                    "unsupported_gaps": ["No WebGL or graphics programming"],
                    "fit_summary": "Near-perfect match. John's open-source real-time collaborative IDE is a direct proof point for this role.",
                }),
                "resume_content": None,
                "cover_letter": None,
            },
            {
                "company_name": "Vercel",
                "role_title": "Senior Software Engineer – Edge Runtime",
                "status": ApplicationStatus.draft,
                "jd_analysis_json": None,
                "resume_content": None,
                "cover_letter": None,
            },
            {
                "company_name": "Notion",
                "role_title": "Software Engineer – AI Features",
                "status": ApplicationStatus.rejected,
                "jd_analysis_json": json.dumps({
                    "keywords": ["AI", "Python", "React", "TypeScript", "LLM", "product thinking", "collaboration"],
                    "required_skills": ["React", "TypeScript", "Python", "LLM integration"],
                    "preferred_skills": ["Electron", "SQLite", "Notion API"],
                    "strengths": ["LLM integration", "React", "TypeScript"],
                    "unsupported_gaps": ["No Electron or desktop app experience"],
                    "fit_summary": "Good skill match but desktop experience gap was a concern.",
                }),
                "resume_content": None,
                "cover_letter": None,
            },
            {
                "company_name": "Databricks",
                "role_title": "Senior Engineer – Data Platform",
                "status": ApplicationStatus.follow_up,
                "jd_analysis_json": json.dumps({
                    "keywords": ["Apache Spark", "Python", "distributed computing", "Delta Lake", "data pipelines", "Scala", "Kafka"],
                    "required_skills": ["Apache Spark", "Python", "data pipelines", "distributed systems"],
                    "preferred_skills": ["Scala", "Delta Lake", "ML Ops"],
                    "strengths": ["Apache Spark experience at Palantir", "Kafka expertise at Stripe", "distributed systems"],
                    "unsupported_gaps": ["Scala not in tech stack"],
                    "fit_summary": "Strong data engineering background. Palantir + Stripe experience is directly relevant.",
                }),
                "resume_content": None,
                "cover_letter": None,
            },
        ]

        existing_apps = db.query(JobApplication).filter_by(user_id=uid).count()
        if existing_apps == 0:
            jd_templates = {
                "OpenAI": "We are looking for a Senior Software Engineer to join our API Platform team. You will design and build the infrastructure that millions of developers use to access OpenAI's models. Requirements: 5+ years Python, distributed systems experience, API design expertise, familiarity with Kubernetes and PostgreSQL. Nice to have: LLM integration experience, Rust, high-throughput systems.",
                "Anthropic": "Staff Engineer to lead infrastructure reliability at Anthropic. You'll own our Kubernetes clusters, Terraform configurations, and observability stack. Requirements: Kubernetes, Terraform, Python, SRE practices. We value engineers who think deeply about reliability and can build self-healing systems.",
                "Figma": "We're looking for a Senior Full Stack Engineer passionate about real-time collaboration. You'll work on the core editor infrastructure powering 10M+ users. Stack: React, TypeScript, Node.js, WebSockets. Experience with operational transforms or CRDTs is a big plus.",
                "Vercel": "Senior Software Engineer for our Edge Runtime team. You'll build the next generation of edge computing infrastructure. Requirements: Node.js, TypeScript, distributed systems, performance optimization. Experience with V8 or Cloudflare Workers is a plus.",
                "Notion": "Software Engineer – AI Features. Join our AI team to build the next generation of AI-powered productivity tools. Requirements: React, TypeScript, Python, LLM integration experience. You'll work closely with product and design to ship AI features used by millions.",
                "Databricks": "Senior Engineer for our Data Platform team. You'll build and optimize data ingestion pipelines, work with Apache Spark at scale, and help customers get more value from their data. Requirements: Spark, Python, distributed computing. Scala experience is a plus.",
            }

            for app_data in apps_data:
                app = JobApplication(
                    user_id=uid,
                    company_name=app_data["company_name"],
                    role_title=app_data["role_title"],
                    job_description=jd_templates[app_data["company_name"]],
                    status=app_data["status"],
                    jd_analysis_json=app_data["jd_analysis_json"],
                )
                db.add(app)
                db.flush()

                # Add generated documents for the first app only (OpenAI)
                if app_data["resume_content"]:
                    db.add(GeneratedDocument(
                        user_id=uid,
                        application_id=app.id,
                        doc_type=DocumentType.resume,
                        version=1,
                        content=app_data["resume_content"],
                        format="txt",
                    ))
                if app_data["cover_letter"]:
                    db.add(GeneratedDocument(
                        user_id=uid,
                        application_id=app.id,
                        doc_type=DocumentType.cover_letter,
                        version=1,
                        content=app_data["cover_letter"],
                        format="txt",
                    ))

            print(f"[+] Added {len(apps_data)} job applications")
        else:
            print(f"[~] {existing_apps} applications already exist, skipping")

        # ── 9. Parsed Resume Data ──────────────────────────────────────────
        if not db.query(ParsedResumeData).filter_by(user_id=uid).first():
            raw_resume = """JOHN DOE
john.doe@example.com | (415) 555-0182 | linkedin.com/in/johndoe | github.com/johndoe
San Francisco, CA

PROFESSIONAL SUMMARY
Senior software engineer with 7+ years of experience designing and building scalable web applications, real-time systems, and AI-powered products. Adept at leading engineering teams, partnering with product managers, and delivering high-impact features in fast-paced environments.

WORK EXPERIENCE

Senior Software Engineer | Stripe | March 2022 – Present | San Francisco, CA
• Led development of Stripe Radar dashboard, reducing fraud false positives by 34% and saving $12M annually
• Architected real-time event streaming pipelines handling 2M+ events/day using Apache Kafka and Python
• Deployed production GPT-4 integration for dispute resolution, reducing average resolution time by 45%
• Mentored 4 junior engineers; conducted 200+ technical interviews

Full Stack Engineer | Airbnb | June 2019 – February 2022 | San Francisco, CA
• Redesigned host onboarding flow, increasing new listing creation rate by 28%
• Built internal A/B testing platform used by 40+ product teams across the company
• Led migration of legacy Rails monolith to GraphQL/Node.js microservices (12 services)
• Achieved WCAG 2.1 AA accessibility compliance across all core user flows

Software Engineer | Palantir Technologies | August 2017 – May 2019 | Washington, DC
• Developed geospatial analytics dashboards used by US government health agencies
• Optimized Apache Spark data ingestion pipelines, reducing latency by 60%
• Built React + D3.js interactive visualizations for classified dataset exploration

EDUCATION
B.S. Computer Science | University of California, Berkeley | 2013–2017 | GPA: 3.8
Certificate in Machine Learning | Stanford University (Online) | 2021

SKILLS
Languages: Python, TypeScript, JavaScript, SQL, Go (learning)
Frameworks: FastAPI, React, Next.js, Node.js, GraphQL, Django
Databases: PostgreSQL, Redis, MySQL, Elasticsearch
Infrastructure: AWS, Kubernetes, Docker, Terraform, Apache Kafka
AI/ML: OpenAI API, LangChain, Embeddings, RAG pipelines, Prompt Engineering

CERTIFICATIONS
• AWS Certified Solutions Architect – Professional (2023)
• Google Cloud Professional Data Engineer (2022)
• Certified Kubernetes Application Developer – CKAD (2023)

PROJECTS
AI Code Review Bot — Python, FastAPI, OpenAI API, GitHub Actions
• Automated PR review assistant; reduced team review cycle time by 40%
Real-Time Collaborative IDE — React, Node.js, WebSockets, Monaco Editor
• Open-source browser IDE with real-time multi-user editing; 2.4k GitHub stars
Distributed Task Scheduler — Python, Celery, Redis, PostgreSQL
• 500k+ jobs/day with priority queuing, retries, and observability"""

            structured = {
                "full_name": "John Doe",
                "email": "john.doe@example.com",
                "phone": "(415) 555-0182",
                "location": "San Francisco, CA",
                "summary": "Senior software engineer with 7+ years building scalable applications and AI-powered products.",
                "skills": ["Python", "TypeScript", "React", "FastAPI", "Node.js", "PostgreSQL", "Redis", "AWS", "Kubernetes", "Docker", "Terraform", "Apache Kafka", "LLM Integration", "Next.js", "GraphQL"],
                "experiences": [
                    {"company": "Stripe", "role": "Senior Software Engineer", "start": "2022-03", "end": "Present"},
                    {"company": "Airbnb", "role": "Full Stack Engineer", "start": "2019-06", "end": "2022-02"},
                    {"company": "Palantir Technologies", "role": "Software Engineer", "start": "2017-08", "end": "2019-05"},
                ],
                "education": [
                    {"institution": "UC Berkeley", "degree": "B.S. Computer Science", "year": 2017},
                    {"institution": "Stanford Online", "degree": "ML Certificate", "year": 2021},
                ],
                "certifications": ["AWS Solutions Architect Professional", "Google Cloud Data Engineer", "CKAD"],
            }

            db.add(ParsedResumeData(
                user_id=uid,
                uploaded_file_id=None,
                raw_text=raw_resume,
                structured_json=json.dumps(structured),
                confidence_score=0.97,
            ))
            print("[+] Added parsed resume data (confidence: 97%)")

        db.commit()

        print(f"""
╔══════════════════════════════════════════════════════╗
║            John Doe Demo Account Created!            ║
╠══════════════════════════════════════════════════════╣
║  Email    : {EMAIL:<42}║
║  Password : {PASSWORD:<42}║
║  Plan     : Pro (all features enabled)               ║
║  Apps     : 6 (draft/applied/screening/interview/    ║
║             offered/rejected)                        ║
║  Resume   : Parsed with 97% confidence               ║
╚══════════════════════════════════════════════════════╝
        """)

    except Exception as e:
        db.rollback()
        print(f"[!] Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
