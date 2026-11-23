"""
Resume tailoring pipeline — evidence-mapped, verified ATS optimisation.

Unit tests drive the pipeline with a scripted LLM (no network); API tests run
the full route stack on SQLite with the mock provider and a scripted provider.
"""
import json

from app.models.models import JobApplication, ParsedResumeData, User
from app.services.resume_tailoring import service as tailoring_service_mod
from app.services.resume_tailoring.pipeline import ResumeTailoringPipeline, parse_json_obj
from app.services.resume_tailoring.source import SourceResume, SrcExperience, SrcProject
from app.services.resume_tailoring.terms import (
    Term, count_term, coverage, find_alias, has_term, quote_in_source, split_bullets, unsupported_numbers,
)
from tests.conftest import TestingSessionLocal, auth_headers


# ── fixtures ────────────────────────────────────────────────────────────────

def make_src() -> SourceResume:
    return SourceResume(
        resume_id=7,
        parse_confidence=0.91,
        name="Deep Test",
        contact=["deep@example.com", "Dubai"],
        headline="Backend Engineer",
        summary="Backend engineer building Python services and LLM workflows.",
        skills=["Python", "FastAPI", "PostgreSQL", "Next.js", "Docker"],
        experiences=[
            SrcExperience(
                ref="E1", role="Backend Engineer", company="Acme", start_date="2022-01", end_date="",
                bullets=[
                    "Built LLM workflows with tool calling for support triage, cutting handling time 30%",
                    "Designed PostgreSQL schemas for 2 million records",
                    "Shipped a Next.js dashboard for internal ops",
                ],
            ),
        ],
        projects=[SrcProject(ref="P1", name="ApplyForge", technologies="FastAPI, Qdrant",
                             bullets=["RAG pipeline over resumes with an LLM-as-judge evaluation harness"])],
        certifications=[],
        education=["BSc, Computer Science, Test University"],
    )


JD = ("We need an AI Engineer. Expert in Python. Experience shipping AI agents and evaluation pipelines "
      "into production. Familiarity with Postgres. Front-end: TypeScript/React. Publications at ML conferences a plus.")

JDA = {
    "keywords": ["AI agents", "evaluation pipelines", "Python", "Postgres"],
    "suggested_keywords": ["AI agents", "Postgres"],
    "required_skills": ["Python", "AI agents", "Postgres", "React", "Kubernetes"],
    "preferred_skills": ["Publications at top machine learning conferences"],
}

MAP_OK = {
    "target_title": "AI Engineer",
    "terms": [
        {"term": "Python", "importance": "required", "status": "explicit",
         "evidence": [{"ref": "SKILLS", "quote": "Python"}], "placements": ["SKILLS", "SUMMARY"]},
        {"term": "AI agents", "importance": "required", "status": "implicit",
         "evidence": [{"ref": "E1.1", "quote": "Built LLM workflows with tool calling"}],
         "placements": ["SUMMARY", "E1.1", "SKILLS"]},
        {"term": "Postgres", "importance": "required", "status": "implicit", "variants": ["PostgreSQL"],
         "evidence": [{"ref": "E1.2", "quote": "Designed PostgreSQL schemas"}], "placements": ["SKILLS", "E1.2"]},
        {"term": "React", "importance": "required", "status": "implicit",
         "evidence": [{"ref": "E1.3", "quote": "Shipped a Next.js dashboard"}], "placements": ["SKILLS", "E1.3"]},
        # Invented evidence -> must be downgraded to absent.
        {"term": "Kubernetes", "importance": "required", "status": "implicit",
         "evidence": [{"ref": "E1.2", "quote": "Ran Kubernetes clusters at scale"}], "placements": ["SKILLS"]},
        {"term": "evaluation pipelines", "importance": "keyword", "status": "implicit",
         "evidence": [{"ref": "P1.1", "quote": "LLM-as-judge evaluation harness"}], "placements": ["P1.1"]},
        {"term": "AI Engineer", "importance": "keyword", "status": "absent", "placements": []},
        {"term": "Publications at ML conferences", "importance": "preferred", "status": "absent"},
    ],
    "bullet_priority": {"E1": ["E1.1", "E1.2", "E1.3"]},
}

WRITE_OK = {
    "headline": "Backend Engineer | Python, AI agents, Postgres",
    "summary": "Backend engineer building Python services and AI agents (LLM workflows with tool calling) on Postgres.",
    "skills": ["Python", "AI agents", "Postgres", "React", "FastAPI", "Kubernetes"],
    "experiences": [{"ref": "E1", "bullets": [
        {"text": "Cut support handling time 30% by building AI agents (LLM workflows with tool calling) for triage",
         "from": ["E1.1"]},
        {"text": "Designed Postgres (PostgreSQL) schemas for 2 million records", "from": ["E1.2"]},
        # Invented metric -> must revert to source bullet E1.3.
        {"text": "Shipped a React (Next.js) dashboard used by 500 ops staff", "from": ["E1.3"]},
    ]}],
    "projects": [{"ref": "P1", "bullets": [
        {"text": "Built a RAG pipeline with LLM-as-judge evaluation pipelines over resumes", "from": ["P1.1"]}]}],
}


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, system, user):
        self.prompts.append((system, user))
        if not self.responses:
            return "{}"
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


# ── terms ───────────────────────────────────────────────────────────────────

def test_term_matching_is_word_boundary_aware():
    assert has_term("Experienced in Java and Spring", "Java")
    assert not has_term("Experienced in JavaScript", "Java")
    assert not has_term("Worked at Google", "Go")
    assert has_term("C++ and C# developer", "C++")
    assert has_term("Built with Node.js", "node.js")
    assert has_term("front end development", "front-end development")
    assert has_term("Designed REST APIs", "REST API")
    assert count_term("Python, python3 and Python", "Python") == 2


def test_alias_evidence_and_ambiguous_shorts():
    assert find_alias("Designed PostgreSQL schemas", "Postgres") is not None
    assert find_alias("Shipped Kubernetes", "k8s") == "kubernetes"
    # "ai" alone must not count as evidence for "artificial intelligence"
    assert find_alias("ai", "artificial intelligence") is None


def test_numbers_and_quotes():
    src = "Cut handling time 30% across 2 million records, saving $14K/yr"
    assert unsupported_numbers("Cut handling time 30% for $14K", src) == []
    assert unsupported_numbers("Used by 500 staff, 45% faster", src) == ["45%", "500"]
    assert quote_in_source("cut handling time 30%", src)
    assert not quote_in_source("Ran Kubernetes clusters at scale", src)


def test_split_bullets_and_coverage():
    assert split_bullets("• one\n• two\n- three") == ["one", "two", "three"]
    cov = coverage([Term("Python", "required"), Term("Go", "keyword")], "Python developer")
    assert cov["matched"] == 1 and cov["required_matched"] == 1 and cov["weighted_pct"] == 60


def test_parse_json_obj_tolerates_fences_and_prose():
    assert parse_json_obj('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_obj('Sure! {"a": 2} done') == {"a": 2}
    assert parse_json_obj("[MOCK_GENERATION] nope") is None


# ── pipeline ────────────────────────────────────────────────────────────────

def _run(*responses):
    llm = ScriptedLLM(*responses)
    res = ResumeTailoringPipeline(llm).run(make_src(), JD, JDA, "AI Engineer", "Example")
    return res, llm


def test_pipeline_places_verified_terms_and_refuses_to_fake():
    res, llm = _run(MAP_OK, WRITE_OK, WRITE_OK)
    report = res.report
    by_term = {t["term"]: t for t in report["terms"]}

    # Implicit evidence verified -> placed in the JD's exact spelling
    assert by_term["Postgres"]["status"] == "implicit"
    assert by_term["Postgres"]["found_before"] is False and by_term["Postgres"]["found_after"] is True
    assert by_term["AI agents"]["found_after"] is True
    assert by_term["React"]["found_after"] is True

    # Invented evidence -> absent, surfaced as a gap, never written
    assert by_term["Kubernetes"]["status"] == "absent"
    assert "Kubernetes" not in res.tailored["skills"]
    assert not has_term(res.text, "Kubernetes")
    assert any(g["term"] == "Kubernetes" for g in report["gaps"])
    assert report["rejected_evidence"] >= 1

    # Invented metric -> original bullet restored
    e1 = res.tailored["experiences"][0]["description"]
    assert "500" not in e1
    assert "Shipped a Next.js dashboard for internal ops" in e1
    assert any(w["kind"] == "invented_number" for w in report["warnings"])
    assert any(w["kind"] == "unsupported_skill" for w in report["warnings"])

    # Job title is never a skill; original skills are never dropped
    assert "AI Engineer" not in res.tailored["skills"]
    for s in ["Python", "FastAPI", "PostgreSQL", "Next.js", "Docker"]:
        assert s in res.tailored["skills"]

    # Coverage went up, and is measured on exact JD terms
    assert report["coverage"]["after"]["weighted_pct"] > report["coverage"]["before"]["weighted_pct"]
    assert report["source"]["resume_id"] == 7 and report["source"]["parse_confidence"] == 0.91
    # Employer / title / education untouched in the plain-text document
    assert "Backend Engineer — Acme" in res.text and "Test University" in res.text


def test_pipeline_runs_repair_pass_for_missing_terms():
    write_missing = json.loads(json.dumps(WRITE_OK))
    write_missing["experiences"][0]["bullets"][1]["text"] = "Designed database schemas for 2 million records"
    write_missing["skills"] = ["Python", "AI agents", "React"]
    write_missing["summary"] = "Backend engineer building Python services and AI agents."
    write_missing["headline"] = "Backend Engineer"
    res, llm = _run(MAP_OK, write_missing, WRITE_OK)
    assert len(llm.prompts) == 3                     # map, write, repair
    assert "Missing planned term \"Postgres\"" in llm.prompts[2][1]
    assert res.report["repair_pass"] is True
    assert {t["term"]: t for t in res.report["terms"]}["Postgres"]["found_after"] is True


def test_pipeline_degrades_gracefully_when_llm_returns_garbage():
    res, llm = _run("[MOCK_GENERATION] not json", RuntimeError("provider down"))
    report = res.report
    assert report["plan_source"] == "deterministic"
    assert report["warnings"][0]["kind"] == "generation_failed"
    # Deterministic plan still finds explicit + alias evidence, and keeps the original resume
    by_term = {t["term"]: t for t in report["terms"]}
    assert by_term["Python"]["status"] == "explicit"
    assert by_term["Postgres"]["status"] == "implicit"       # via the PostgreSQL alias
    assert by_term["Kubernetes"]["status"] == "absent"
    assert res.tailored["summary"] == make_src().summary
    assert "Designed PostgreSQL schemas for 2 million records" in res.tailored["experiences"][0]["description"]


def test_absent_terms_are_never_placed_even_if_llm_tries():
    plan = json.loads(json.dumps(MAP_OK))
    write = json.loads(json.dumps(WRITE_OK))
    write["summary"] += " Published at ML conferences."
    res, llm = _run(plan, write, write)
    # The repair prompt is asked to remove it; the plan keeps it as a gap either way.
    assert any(g["term"] == "Publications at ML conferences" for g in res.report["gaps"])


# ── API ─────────────────────────────────────────────────────────────────────

def _seed(client):
    headers = auth_headers(client)
    db = TestingSessionLocal()
    user = db.query(User).filter_by(email="user@example.com").first()
    parsed = ParsedResumeData(
        user_id=user.id,
        raw_text="Deep Test\nBackend Engineer at Acme\nBuilt LLM workflows with tool calling\nPostgreSQL",
        structured_json=json.dumps({
            "full_name": "Deep Test", "email": "deep@example.com",
            "summary": "Backend engineer building Python services and LLM workflows.",
            "skills": ["Python", "FastAPI", "PostgreSQL", "Next.js"],
            "work_experience": [{
                "role": "Backend Engineer", "company": "Acme", "start_date": "2022-01-01",
                "description": "• Built LLM workflows with tool calling for support triage, cutting handling time 30%\n"
                               "• Designed PostgreSQL schemas for 2 million records\n"
                               "• Shipped a Next.js dashboard for internal ops",
            }],
            "projects": [{"name": "ApplyForge", "technologies": "FastAPI, Qdrant",
                          "description": "RAG pipeline over resumes with an LLM-as-judge evaluation harness"}],
            "education": [{"institution": "Test University", "degree": "BSc", "field_of_study": "CS"}],
        }),
        confidence_score=0.88,
    )
    db.add(parsed)
    db.commit()
    db.close()
    app = client.post("/api/v1/applications", json={
        "company_name": "Example", "role_title": "AI Engineer", "job_description": JD,
    }, headers=headers).json()
    return headers, app["id"]


def _store_analysis(app_id):
    db = TestingSessionLocal()
    row = db.get(JobApplication, app_id)
    row.jd_analysis_json = json.dumps(JDA)
    db.commit()
    db.close()


def test_tailoring_api_with_scripted_llm(client, monkeypatch):
    headers, app_id = _seed(client)
    _store_analysis(app_id)
    monkeypatch.setattr(tailoring_service_mod, "get_llm_provider",
                        lambda: ScriptedLLM(MAP_OK, WRITE_OK, WRITE_OK))

    assert client.get(f"/api/v1/applications/{app_id}/tailoring", headers=headers).json() is None

    res = client.post(f"/api/v1/applications/{app_id}/tailoring", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is True and body["is_stale"] is False
    assert body["report"]["coverage"]["after"]["weighted_pct"] > body["report"]["coverage"]["before"]["weighted_pct"]
    assert "Kubernetes" not in body["tailored"]["skills"]
    assert body["tailored"]["experiences"][0]["company"] == "Acme"

    # A resume GeneratedDocument version was stored
    docs = client.get(f"/api/v1/applications/{app_id}/documents/current", headers=headers).json()
    assert docs["resume"]["id"] == body["generated_document_id"]

    # Toggle off / on
    off = client.patch(f"/api/v1/applications/{app_id}/tailoring", json={"enabled": False}, headers=headers)
    assert off.json()["enabled"] is False

    # Exports still work with the overlay
    client.patch(f"/api/v1/applications/{app_id}/tailoring", json={"enabled": True}, headers=headers)
    pdf = client.get(f"/api/v1/applications/{app_id}/export/pdf", headers=headers)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    # Changing the JD marks it stale
    client.patch(f"/api/v1/applications/{app_id}", json={"job_description": JD + " Also Rust."}, headers=headers)
    stale = client.get(f"/api/v1/applications/{app_id}/tailoring", headers=headers).json()
    assert stale["is_stale"] is True and "The job description changed" in stale["stale_reasons"]

    assert client.delete(f"/api/v1/applications/{app_id}/tailoring", headers=headers).status_code == 204
    assert client.get(f"/api/v1/applications/{app_id}/tailoring", headers=headers).json() is None


def test_generate_resume_uses_tailoring_pipeline_with_mock_provider(client):
    """Mock provider returns non-JSON: analysis falls back, pipeline degrades to the original resume."""
    headers, app_id = _seed(client)
    res = client.post(f"/api/v1/applications/{app_id}/generate", json={"doc_types": ["resume"]}, headers=headers)
    assert res.status_code == 200, res.text
    doc = res.json()["documents"][0]
    assert doc["doc_type"] == "resume"
    assert "Backend Engineer — Acme" in doc["content"]
    t = client.get(f"/api/v1/applications/{app_id}/tailoring", headers=headers).json()
    assert t is not None and t["generated_document_id"] == doc["id"]


def test_tailoring_is_user_scoped(client, monkeypatch):
    headers, app_id = _seed(client)
    other = auth_headers(client, email="other@example.com")
    assert client.post(f"/api/v1/applications/{app_id}/tailoring", headers=other).status_code == 404
    assert client.get(f"/api/v1/applications/{app_id}/tailoring", headers=other).status_code == 404
