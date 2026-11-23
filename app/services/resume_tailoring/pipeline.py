"""
Resume tailoring pipeline — evidence-mapped, verified ATS optimisation.

    JD analysis ──► 1. MAP   (LLM)  atomic ATS terms + where the candidate's
                                     evidence for each one lives
                    2. VALIDATE      every evidence quote must exist in the source
                       (no LLM)      resume; unverifiable claims become gaps
                    3. WRITE (LLM)   rewrite headline / summary / skills / bullets
                                     following the validated placement plan
                    4. VERIFY        coverage, invented numbers, unsupported skills,
                       (no LLM)      keyword stuffing, hallucination regex checks
                    5. REPAIR (LLM,  one targeted pass for misses, then deterministic
                       optional)     fallbacks (revert bad bullets, drop bad skills)

Nothing here touches the DB — `ResumeTailoringService` (service.py) loads the
inputs and persists the output. That keeps the pipeline unit-testable with a
scripted LLM.

Truthfulness guarantees (enforced in code, not just in the prompt):
  • A term is only placed if it is literally in the resume, an alias of it is,
    or the LLM cited a quote that verifiably exists in the resume.
  • No number may appear in the output unless it appears in the source.
  • Skills may only be added if they are approved (explicit / implicit) terms.
  • Employers, titles, dates, education and certifications are never rewritten.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.services.ai.resume_writing import XYZ_BULLET_RULES
from app.services.resume_tailoring.aliases import ALIASES_VERSION
from app.services.resume_tailoring.source import SourceResume
from app.services.resume_tailoring.terms import (
    Term, count_term, coverage, find_alias, has_term, norm, quote_in_source, unsupported_numbers,
)

logger = structlog.get_logger(__name__)

PIPELINE_VERSION = 1
MAX_TERMS = 30
MAX_BULLETS = 5              # the preview + PDF/DOCX renderers show at most 5 bullets per role
MAX_TERM_REPEATS = 4          # more than this across the resume reads as stuffing
SKILL_TERM_MAX_WORDS = 4      # longer phrases are requirements, not skill-list entries
STATUSES = ("explicit", "implicit", "adjacent", "absent")


# ── helpers ────────────────────────────────────────────────────────────────

def parse_json_obj(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            v = json.loads(s[start:end + 1])
            return v if isinstance(v, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if isinstance(x, (str, int, float)) and str(x).strip()]


def _words(s: str) -> int:
    return len(norm(s).split())


@dataclass
class PlannedTerm:
    term: str
    importance: str
    status: str
    variants: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)   # [{ref, quote}]
    placements: list[str] = field(default_factory=list)  # refs
    note: str = ""
    is_title: bool = False                                 # the job title itself — never a "skill"

    def as_term(self) -> Term:
        return Term(self.term, self.importance, self.variants)

    @property
    def placeable(self) -> bool:
        return self.status in ("explicit", "implicit", "adjacent")

    @property
    def skill_eligible(self) -> bool:
        return (self.status in ("explicit", "implicit") and not self.is_title
                and _words(self.term) <= SKILL_TERM_MAX_WORDS)


@dataclass
class TailoringResult:
    tailored: dict
    report: dict
    text: str
    llm_calls: int


# ── prompts ────────────────────────────────────────────────────────────────

MAP_SYSTEM = """\
You are an ATS (applicant tracking system) optimisation analyst.
You receive a job description, a prior analysis of it, and the candidate's resume where every
line has a ref like [E1.3] (experience 1, bullet 3), [P2.1], [SUMMARY], [SKILLS].

Task: list the ATS search terms a recruiter's keyword filter would use for THIS job, and for each
one find the candidate's evidence.

Rules for terms:
• Atomic, short, in the JD's EXACT spelling (e.g. "Postgres", "prompt engineering", "TypeScript",
  "evaluation pipelines"). Split long requirements into their searchable terms. Max 25 terms.
• importance: "required" (must-have in the JD), "preferred" (nice-to-have), "keyword" (other
  important JD vocabulary). Include the job title as a "keyword" term.
• variants: other spellings an ATS or recruiter might also search (acronym <-> long form).

Rules for status (be strict and honest):
• "explicit": the term (or the same word) is already written in the resume.
• "implicit": the candidate clearly has it under a different name or it is directly demonstrated
  (e.g. resume says "built LLM workflows with tool calling" -> JD term "AI agents"; resume lists
  "Next.js" -> JD term "React").
• "adjacent": not the same thing, but closely related experience that can be honestly framed
  toward it (e.g. "MySQL" -> "Postgres databases"). Never use adjacent for a skill they lack.
• "absent": no evidence. Do NOT stretch.
• evidence: for explicit/implicit/adjacent give 1-2 items {"ref": "E1.3", "quote": "<VERBATIM span
  copied from that resume line>"}. Quotes are machine-checked against the resume — a paraphrased or
  invented quote will be rejected and the term marked absent.
• placements: refs where the term should appear in the tailored resume. Use "SKILLS" only for
  explicit/implicit tool/skill names; "SUMMARY"/"HEADLINE" for the most important terms; and the
  bullet refs that contain the evidence. Absent terms get no placements.

Also return:
• "target_title": the job title exactly as written in the JD.
• "bullet_priority": for each experience ref, its bullet refs ordered most -> least relevant to this JD.

Return ONLY JSON:
{"target_title": "...",
 "terms": [{"term": "...", "importance": "required|preferred|keyword", "variants": ["..."],
            "status": "explicit|implicit|adjacent|absent",
            "evidence": [{"ref": "E1.3", "quote": "..."}], "placements": ["SKILLS", "E1.3"],
            "note": "one short reason"}],
 "bullet_priority": {"E1": ["E1.3", "E1.1"]}}
"""

WRITE_SYSTEM = """\
You are an expert ATS resume writer. You rewrite ONE candidate's resume for ONE job, following a
verified keyword placement plan. You never invent facts.

""" + XYZ_BULLET_RULES + """
PLACEMENT RULES:
• Use each planned term in the JD's EXACT spelling at the planned places. Weave it into a real
  sentence about the evidence — never a bare keyword list inside a bullet.
• "implicit" terms: name the JD term next to what the candidate actually did, e.g.
  "Built LLM-based AI agents with tool calling…" when the resume says "LLM workflows with tool calling".
• "adjacent" terms: only frame honestly ("relational databases (MySQL), transferable to Postgres" is
  NOT allowed — instead mention the actual skill and the general category, e.g. "relational
  database design (MySQL)"). Do not claim the adjacent term as experience.
• Absent terms must NOT appear anywhere.
• Each term at most 3 times across the whole resume. No keyword stuffing.

SECTION RULES:
• headline: one line, <= 12 words: the candidate's real professional identity aligned to the target
  role + 2-3 top matching skills. Use the target job title ONLY if the candidate has held that
  title or one clearly equivalent; otherwise use their actual most recent title.
• summary: 2-4 sentences, leads with the strongest evidence for the top required terms. Only facts
  from the resume (no years of experience unless stated in the resume).
• skills: the candidate's existing skills reordered most-relevant-first, plus any APPROVED SKILL
  TERMS given below (in their JD spelling). Never add anything else.
• experiences: keep EVERY experience ref. 3-5 bullets each (fewer if the source has fewer), most
  relevant to this job first. Each bullet lists the source bullet refs it is based on in "from".
  You may merge or split source bullets but never add facts that are not in them.
• projects: keep every project ref; rewrite bullets the same way.
• Do NOT output employers, titles, dates, education or certifications — they are kept as-is.

Return ONLY JSON:
{"headline": "...", "summary": "...", "skills": ["..."],
 "experiences": [{"ref": "E1", "bullets": [{"text": "...", "from": ["E1.2"]}]}],
 "projects": [{"ref": "P1", "bullets": [{"text": "...", "from": ["P1.1"]}]}]}
"""

REPAIR_SYSTEM = """\
You fix a tailored resume draft. Apply ONLY the listed fixes, keep everything else identical, keep
all truthfulness rules (no invented numbers, employers, titles, skills or achievements; every bullet
lists the source refs it is based on in "from"). Return the full corrected JSON in the same schema.
"""


# ── pipeline ───────────────────────────────────────────────────────────────

class ResumeTailoringPipeline:
    def __init__(self, llm):
        self.llm = llm
        self.calls = 0

    def _llm(self, system: str, user: str) -> str | None:
        self.calls += 1
        try:
            return self.llm.generate(system, user)
        except Exception as exc:  # provider errors must not kill the pipeline
            logger.warning("tailoring_llm_failed", error=str(exc)[:200])
            return None

    # 0 ─ seed terms from the stored JD analysis ───────────────────────────
    @staticmethod
    def seed_terms(jd_analysis: dict) -> list[Term]:
        out: list[Term] = []
        seen: set[str] = set()
        for key, imp in (("required_skills", "required"), ("suggested_keywords", "keyword"),
                         ("keywords", "keyword"), ("preferred_skills", "preferred")):
            for t in _str_list(jd_analysis.get(key)):
                k = norm(t)
                if k and k not in seen:
                    seen.add(k)
                    out.append(Term(t, imp))
        return out

    # 1 ─ MAP ───────────────────────────────────────────────────────────────
    def map_terms(self, src: SourceResume, jd: str, jd_analysis: dict, role_title: str) -> dict | None:
        analysis = {k: jd_analysis.get(k) for k in
                    ("required_skills", "preferred_skills", "keywords", "suggested_keywords",
                     "strengths", "unsupported_gaps") if jd_analysis.get(k)}
        user = (
            f"=== TARGET ROLE ===\n{role_title}\n\n"
            f"=== JOB DESCRIPTION ===\n{jd[:9000]}\n\n"
            f"=== PRIOR JD ANALYSIS ===\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
            f"=== CANDIDATE RESUME (with refs) ===\n{src.for_prompt()}"
        )
        return parse_json_obj(self._llm(MAP_SYSTEM, user) or "")

    # 2 ─ VALIDATE ──────────────────────────────────────────────────────────
    def validate_plan(self, raw: dict | None, src: SourceResume, seeds: list[Term],
                      role_title: str = "") -> tuple[list[PlannedTerm], dict]:
        corpus = src.corpus()
        refs = src.valid_refs()
        planned: list[PlannedTerm] = []
        seen: set[str] = set()
        meta = {"target_title": "", "bullet_priority": {}, "rejected_evidence": 0, "map_ok": raw is not None}

        def classify(term: str, claimed: str, variants: list[str], evidence_in: list) -> PlannedTerm:
            ev: list[dict] = []
            for e in evidence_in if isinstance(evidence_in, list) else []:
                if not isinstance(e, dict):
                    continue
                quote = str(e.get("quote") or "").strip()
                ref = str(e.get("ref") or "").strip().upper()
                if quote and quote_in_source(quote, corpus):
                    ev.append({"ref": ref if ref in refs else "", "quote": quote[:240]})
                elif quote:
                    meta["rejected_evidence"] += 1
            literal = has_term(corpus, term)
            alias = None if literal else find_alias(corpus, term, variants)
            note = ""
            if literal:
                status = "explicit"
            elif claimed in ("explicit", "implicit"):
                if alias or ev:
                    status = "implicit"
                else:
                    status, note = "absent", "Evidence could not be verified in your resume"
            elif claimed == "adjacent":
                status = "adjacent" if (ev or alias) else "absent"
                if status == "absent":
                    note = "Evidence could not be verified in your resume"
            else:  # claimed absent (or unknown)
                status = "implicit" if alias else "absent"
            if alias and not any(alias in norm(x["quote"]) for x in ev):
                ev.append({"ref": "", "quote": alias})
            return PlannedTerm(term=term, importance="keyword", status=status, variants=variants,
                               evidence=ev[:2], note=note)

        items = raw.get("terms") if isinstance(raw, dict) else None
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            term = str(it.get("term") or "").strip().strip(".,;:")
            k = norm(term)
            if not term or len(term) > 60 or k in seen:
                continue
            seen.add(k)
            pt = classify(term, str(it.get("status") or "absent").lower(),
                          _str_list(it.get("variants"))[:5], it.get("evidence") or [])
            imp = str(it.get("importance") or "keyword").lower()
            pt.importance = imp if imp in ("required", "preferred", "keyword") else "keyword"
            if not pt.note:
                pt.note = str(it.get("note") or "")[:200]
            pt.placements = [r for r in (str(x).strip().upper() for x in _str_list(it.get("placements")))
                             if r in refs]
            planned.append(pt)

        # Never lose a required seed: add short required skills the map skipped.
        for s in seeds:
            k = norm(s.term)
            if k in seen or _words(s.term) > 6 or len(s.term) > 60:
                continue
            if any(k in norm(p.term) or norm(p.term) in k for p in planned):
                continue
            seen.add(k)
            pt = classify(s.term, "absent", [], [])
            pt.importance = s.importance
            planned.append(pt)

        titles = {norm(role_title)}
        if isinstance(raw, dict) and raw.get("target_title"):
            titles.add(norm(str(raw["target_title"])))
        titles.discard("")

        # Placement hygiene
        for pt in planned:
            pt.is_title = norm(pt.term) in titles
            if not pt.placeable:
                pt.placements = []
                continue
            pl = [r for r in pt.placements]
            if not pt.skill_eligible:
                pl = [r for r in pl if r != "SKILLS"]
            if _words(pt.term) > SKILL_TERM_MAX_WORDS:
                pl = [r for r in pl if r != "HEADLINE"]
            ev_refs = [e["ref"] for e in pt.evidence if e.get("ref")]
            if pt.status == "adjacent":
                # adjacent terms only go where their evidence is
                pl = [r for r in pl if r in ev_refs or r.split(".")[0] in {x.split(".")[0] for x in ev_refs}]
            if not pl:
                if pt.skill_eligible:
                    pl.append("SKILLS")
                pl.extend(r for r in ev_refs[:1] if r not in pl)
            pt.placements = list(dict.fromkeys(pl))[:4]

        order = {"required": 0, "keyword": 1, "preferred": 2}
        planned.sort(key=lambda p: order.get(p.importance, 3))
        planned = planned[:MAX_TERMS]

        if isinstance(raw, dict):
            meta["target_title"] = str(raw.get("target_title") or "")[:120]
            bp = raw.get("bullet_priority")
            if isinstance(bp, dict):
                meta["bullet_priority"] = {
                    str(k).upper(): [r for r in (str(x).upper() for x in _str_list(v)) if r in refs]
                    for k, v in bp.items() if str(k).upper() in refs
                }
        return planned, meta

    # 3 ─ WRITE ─────────────────────────────────────────────────────────────
    @staticmethod
    def _plan_text(planned: list[PlannedTerm], meta: dict) -> str:
        lines = []
        for p in planned:
            if not p.placeable:
                continue
            ev = "; ".join(f"{e['ref'] or 'resume'}: \"{e['quote'][:90]}\"" for e in p.evidence)
            lines.append(f"- \"{p.term}\" [{p.importance}, {p.status}] -> place in {', '.join(p.placements)}"
                         + (f" | evidence {ev}" if ev else ""))
        absent = [p.term for p in planned if not p.placeable]
        approved = [p.term for p in planned if p.skill_eligible]
        out = "PLANNED TERMS:\n" + ("\n".join(lines) or "(none)")
        out += "\n\nAPPROVED SKILL TERMS (may be added to skills): " + (", ".join(approved) or "(none)")
        out += "\n\nABSENT — MUST NOT APPEAR: " + (", ".join(absent) or "(none)")
        if meta.get("bullet_priority"):
            out += "\n\nBULLET PRIORITY: " + json.dumps(meta["bullet_priority"])
        return out

    def write(self, src: SourceResume, jd: str, planned: list[PlannedTerm], meta: dict,
              role_title: str, company: str) -> dict | None:
        user = (
            f"=== TARGET ===\n{meta.get('target_title') or role_title} at {company}\n\n"
            f"=== JOB DESCRIPTION (for context) ===\n{jd[:6000]}\n\n"
            f"=== {self._plan_text(planned, meta)}\n\n"
            f"=== CANDIDATE RESUME (with refs) ===\n{src.for_prompt()}"
        )
        return parse_json_obj(self._llm(WRITE_SYSTEM, user) or "")

    # 4 ─ VERIFY (deterministic, returns a clean draft + issues) ───────────
    def verify(self, draft: dict | None, src: SourceResume, planned: list[PlannedTerm]) -> tuple[dict, list[str], list[dict]]:
        corpus = src.corpus()
        warnings: list[dict] = []
        draft = draft if isinstance(draft, dict) else {}

        def clean_text(text: Any, fallback: str, where: str, max_len: int) -> str:
            t = str(text or "").strip()
            if not t:
                return fallback
            bad = unsupported_numbers(t, corpus)
            if bad:
                warnings.append({"kind": "invented_number", "where": where,
                                 "detail": f"Numbers not in your resume ({', '.join(bad)}) — kept your original text"})
                return fallback
            return t[:max_len]

        headline = clean_text(draft.get("headline"), src.headline, "Headline", 160)
        summary = clean_text(draft.get("summary"), src.summary, "Summary", 1200)

        # Skills — only source skills or approved terms.
        approved = {norm(p.term): p.term for p in planned if p.skill_eligible}
        src_skill_keys = {norm(s): s for s in src.skills}
        skills: list[str] = []
        seen: set[str] = set()
        for s in _str_list(draft.get("skills")):
            k = norm(s)
            if k in seen:
                continue
            if k in src_skill_keys or k in approved or any(has_term(s, x) and _words(s) <= SKILL_TERM_MAX_WORDS
                                                           for x in src.skills if len(x) > 2):
                seen.add(k)
                skills.append(s)
            else:
                warnings.append({"kind": "unsupported_skill", "where": "Skills",
                                 "detail": f"Removed \"{s}\" — not in your resume"})
        for s in src.skills:                       # never silently drop the candidate's skills
            if norm(s) not in seen:
                seen.add(norm(s))
                skills.append(s)

        def clean_items(kind: str, src_items: list, drafted: Any) -> list[dict]:
            by_ref = {}
            for d in drafted if isinstance(drafted, list) else []:
                if isinstance(d, dict) and str(d.get("ref") or "").upper():
                    by_ref[str(d["ref"]).upper()] = d
            out = []
            for item in src_items:
                d = by_ref.get(item.ref)
                bullets: list[str] = []
                used_from: set[str] = set()
                label = src.label(item.ref)
                if d is None:
                    if item.bullets:
                        warnings.append({"kind": "missing_section", "where": label,
                                         "detail": "Not rewritten — kept your original bullets"})
                    bullets = list(item.bullets)
                else:
                    for b in d.get("bullets") if isinstance(d.get("bullets"), list) else []:
                        if isinstance(b, str):
                            text, frm = b, []
                        elif isinstance(b, dict):
                            text, frm = str(b.get("text") or ""), _str_list(b.get("from"))
                        else:
                            continue
                        text = re.sub(r"^\s*[•\-*]\s*", "", text).strip()
                        frm = [f.upper() for f in frm if src.bullet(f.upper()) is not None]
                        if not text:
                            continue
                        bad = unsupported_numbers(text, corpus)
                        if bad:
                            fallback = src.bullet(frm[0]) if frm else None
                            warnings.append({"kind": "invented_number", "where": label,
                                             "detail": f"Numbers not in your resume ({', '.join(bad)}) — "
                                                       + ("kept your original bullet" if fallback else "bullet removed")})
                            if fallback and fallback not in bullets:
                                bullets.append(fallback)
                                used_from.add(frm[0])
                            continue
                        bullets.append(text[:400])
                        used_from.update(frm)
                    # Too much dropped? top up with unused source bullets.
                    floor = min(3, len(item.bullets))
                    for i, sb in enumerate(item.bullets, 1):
                        if len(bullets) >= floor:
                            break
                        if f"{item.ref}.{i}" not in used_from and sb not in bullets:
                            bullets.append(sb)
                    if not bullets:
                        bullets = list(item.bullets)
                entry = {"ref": item.ref, "bullets": bullets[:MAX_BULLETS]}
                if kind == "exp":
                    entry.update(role=item.role, company=item.company)
                else:
                    entry.update(name=item.name)
                out.append(entry)
            return out

        tailored = {
            "headline": headline,
            "summary": summary,
            "skills": skills,
            "experiences": clean_items("exp", src.experiences, draft.get("experiences")),
            "projects": clean_items("proj", src.projects, draft.get("projects")),
        }

        # Issues for the repair pass
        text = tailored_text(tailored, src)
        issues: list[str] = []
        for p in planned:
            if p.placeable and p.placements and not has_term(text, p.term):
                issues.append(f"Missing planned term \"{p.term}\" — add it at {', '.join(p.placements)} "
                              f"using the evidence: " + "; ".join(e["quote"][:80] for e in p.evidence))
            if not p.placeable and has_term(text, p.term) and not has_term(src.resume_text(), p.term):
                issues.append(f"Remove \"{p.term}\" — the candidate has no evidence for it")
            n = count_term(text, p.term)
            if n > MAX_TERM_REPEATS:
                issues.append(f"\"{p.term}\" appears {n} times — reduce to at most 3 (keyword stuffing)")
        return tailored, issues, warnings

    # 5 ─ REPAIR ────────────────────────────────────────────────────────────
    def repair(self, tailored: dict, issues: list[str], src: SourceResume) -> dict | None:
        draft = {
            "headline": tailored["headline"], "summary": tailored["summary"], "skills": tailored["skills"],
            "experiences": [{"ref": e["ref"], "bullets": [{"text": b, "from": []} for b in e["bullets"]]}
                            for e in tailored["experiences"]],
            "projects": [{"ref": p["ref"], "bullets": [{"text": b, "from": []} for b in p["bullets"]]}
                         for p in tailored["projects"]],
        }
        user = (
            "=== FIXES TO APPLY ===\n" + "\n".join(f"- {i}" for i in issues[:20]) +
            f"\n\n=== CURRENT DRAFT ===\n{json.dumps(draft, ensure_ascii=False)}\n\n"
            f"=== CANDIDATE RESUME (source of truth, with refs) ===\n{src.for_prompt()}"
        )
        return parse_json_obj(self._llm(REPAIR_SYSTEM, user) or "")

    # ── orchestration ─────────────────────────────────────────────────────
    def run(self, src: SourceResume, jd: str, jd_analysis: dict, role_title: str, company: str) -> TailoringResult:
        seeds = self.seed_terms(jd_analysis)
        raw_plan = self.map_terms(src, jd, jd_analysis, role_title)
        planned, meta = self.validate_plan(raw_plan, src, seeds, role_title)

        draft = self.write(src, jd, planned, meta, role_title, company)
        write_ok = draft is not None
        tailored, issues, warnings = self.verify(draft, src, planned)

        repaired = False
        if write_ok and issues:
            fixed_raw = self.repair(tailored, issues, src)
            if fixed_raw is not None:
                # Repair output has no reliable "from" refs; keep the source refs by position.
                fixed, issues2, warnings2 = self.verify(_inherit_from(fixed_raw, draft), src, planned)
                before = coverage([p.as_term() for p in planned], tailored_text(tailored, src))["weighted_pct"]
                after = coverage([p.as_term() for p in planned], tailored_text(fixed, src))["weighted_pct"]
                if after >= before and len(issues2) <= len(issues):
                    tailored, issues, warnings, repaired = fixed, issues2, warnings + warnings2, True

        flags = _hallucination_flags(tailored, src)
        # High-confidence flag on headline/summary -> revert that section to the original.
        for f in flags:
            ctx = norm(f.get("context") or f.get("claim") or "")
            if f.get("confidence", 0) >= 0.7 and ctx:
                for sec, orig in (("headline", src.headline), ("summary", src.summary)):
                    if tailored.get(sec) and ctx[:40] and ctx[:40] in norm(tailored[sec]):
                        tailored[sec] = orig
                        warnings.append({"kind": "hallucination_check", "where": sec.title(),
                                         "detail": f"{f.get('reason', 'Unsupported claim')} — kept your original"})

        if not write_ok:
            warnings.insert(0, {"kind": "generation_failed", "where": "Resume",
                                "detail": "AI rewrite unavailable — showing your original resume with the keyword map"})

        report = self._report(src, planned, meta, tailored, warnings, flags, issues, repaired)
        return TailoringResult(tailored=_overlay(tailored), report=report,
                               text=render_text(tailored, src), llm_calls=self.calls)

    # ── report ─────────────────────────────────────────────────────────────
    def _report(self, src, planned, meta, tailored, warnings, flags, open_issues, repaired) -> dict:
        terms = [p.as_term() for p in planned]
        before_text = src.resume_text()
        after_text = tailored_text(tailored, src)
        sections = _section_texts(tailored)

        rows = []
        for p in planned:
            placed = [lbl for lbl, txt in sections if has_term(txt, p.term)]
            rows.append({
                "term": p.term,
                "importance": p.importance,
                "status": p.status,
                "found_before": has_term(before_text, p.term),
                "found_after": has_term(after_text, p.term),
                "count_after": count_term(after_text, p.term),
                "placed_in": placed,
                "evidence": [{"ref": e.get("ref", ""), "label": src.label(e["ref"]) if e.get("ref") else "Resume",
                              "quote": e["quote"]} for e in p.evidence],
                "note": p.note,
            })
        gaps = [{"term": r["term"], "importance": r["importance"],
                 "note": r["note"] or "No evidence in your resume — not added. Address it in your cover letter or interview if you have relevant experience."}
                for r in rows if r["status"] == "absent"]
        return {
            "version": PIPELINE_VERSION,
            "aliases_version": ALIASES_VERSION,
            "target_title": meta.get("target_title") or "",
            "source": {"resume_id": src.resume_id, "parse_confidence": src.parse_confidence},
            "coverage": {"before": coverage(terms, before_text), "after": coverage(terms, after_text)},
            "terms": rows,
            "gaps": gaps,
            "warnings": _dedupe(warnings)[:40],
            "hallucination_flags": flags[:20],
            "open_issues": open_issues[:20],
            "rejected_evidence": meta.get("rejected_evidence", 0),
            "repair_pass": repaired,
            "plan_source": "llm" if meta.get("map_ok") else "deterministic",
            "llm_calls": self.calls,
        }


# ── rendering / overlay helpers ────────────────────────────────────────────

def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for w in items:
        k = (w.get("kind"), w.get("where"), w.get("detail"))
        if k not in seen:
            seen.add(k)
            out.append(w)
    return out


def _inherit_from(fixed: dict, original_draft: dict | None) -> dict:
    """Give repaired bullets the 'from' refs of the bullet at the same position in the first draft."""
    if not isinstance(fixed, dict) or not isinstance(original_draft, dict):
        return fixed
    for key in ("experiences", "projects"):
        orig = {str(d.get("ref", "")).upper(): d for d in original_draft.get(key) or [] if isinstance(d, dict)}
        for d in fixed.get(key) or []:
            if not isinstance(d, dict):
                continue
            o = orig.get(str(d.get("ref", "")).upper())
            ob = (o or {}).get("bullets") or []
            nb = []
            for i, b in enumerate(d.get("bullets") or []):
                text = b.get("text") if isinstance(b, dict) else b
                frm = (b.get("from") if isinstance(b, dict) else None) or \
                      (ob[i].get("from") if i < len(ob) and isinstance(ob[i], dict) else [])
                nb.append({"text": text, "from": frm})
            d["bullets"] = nb
    return fixed


def _section_texts(t: dict) -> list[tuple[str, str]]:
    out = [("Headline", t.get("headline") or ""), ("Summary", t.get("summary") or ""),
           ("Skills", ", ".join(t.get("skills") or []))]
    for e in t.get("experiences") or []:
        out.append((f"{e.get('role', '')} @ {e.get('company', '')}".strip(" @"), "\n".join(e.get("bullets") or [])))
    for p in t.get("projects") or []:
        out.append((f"Project {p.get('name', '')}", "\n".join(p.get("bullets") or [])))
    return out


def tailored_text(t: dict, src: SourceResume) -> str:
    parts = [txt for _, txt in _section_texts(t)]
    parts += [e.role for e in src.experiences]
    parts += [p.technologies for p in src.projects if p.technologies]
    parts += src.certifications + src.education
    return "\n".join(x for x in parts if x)


def _overlay(t: dict) -> dict:
    """Shape consumed by the preview (useResumeData) and the PDF/DOCX exporter."""
    return {
        "headline": t.get("headline") or "",
        "summary": t.get("summary") or "",
        "skills": t.get("skills") or [],
        "experiences": [{"ref": e["ref"], "role": e.get("role", ""), "company": e.get("company", ""),
                         "description": "\n".join(f"• {b}" for b in e.get("bullets") or [])}
                        for e in t.get("experiences") or []],
        "projects": [{"ref": p["ref"], "name": p.get("name", ""),
                      "description": "\n".join(f"• {b}" for b in p.get("bullets") or [])}
                     for p in t.get("projects") or []],
    }


def render_text(t: dict, src: SourceResume) -> str:
    """Plain ATS text version, stored as the GeneratedDocument for versioning and evaluation."""
    out: list[str] = []
    out.append("CONTACT\n" + "\n".join([src.name] + src.contact))
    if t.get("headline"):
        out.append(t["headline"])
    if t.get("summary"):
        out.append("PROFESSIONAL SUMMARY\n" + t["summary"])
    if t.get("skills"):
        out.append("SKILLS\n" + ", ".join(t["skills"]))
    exps = {e["ref"]: e for e in t.get("experiences") or []}
    if src.experiences:
        block = ["WORK EXPERIENCE"]
        for e in src.experiences:
            dates = " – ".join(x for x in (e.start_date, e.end_date or "Present") if x)
            head = f"{e.role} — {e.company}" + (f", {e.location}" if e.location else "") + (f"  {dates}" if dates else "")
            block.append(head)
            block += [f"• {b}" for b in (exps.get(e.ref) or {}).get("bullets", e.bullets)]
            block.append("")
        out.append("\n".join(block).rstrip())
    projs = {p["ref"]: p for p in t.get("projects") or []}
    if src.projects:
        block = ["PROJECTS"]
        for p in src.projects:
            block.append(p.name + (f" ({p.technologies})" if p.technologies else ""))
            block += [f"• {b}" for b in (projs.get(p.ref) or {}).get("bullets", p.bullets)]
            block.append("")
        out.append("\n".join(block).rstrip())
    if src.education:
        out.append("EDUCATION\n" + "\n".join(src.education))
    if src.certifications:
        out.append("CERTIFICATIONS\n" + "\n".join(f"• {c}" for c in src.certifications))
    return "\n---\n".join(out)


def _hallucination_flags(t: dict, src: SourceResume) -> list[dict]:
    """Regex hallucination checker (no LLM, no token cost) over the tailored text."""
    try:
        from evaluation.hallucination import HallucinationChecker
    except Exception:  # offline package not importable in some deployments
        return []
    try:
        text = "\n".join(txt for _, txt in _section_texts(t))
        return HallucinationChecker(llm_provider=None).check_as_dicts(text, src.corpus(), use_llm_fallback=False)
    except Exception as exc:
        logger.warning("tailoring_hallucination_check_failed", error=str(exc)[:200])
        return []
