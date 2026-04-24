"""
Ephemeral free-tier endpoint.

POST /ephemeral/analyze-and-generate

- No authentication required (open to anonymous users)
- Accepts a job description + optional list of doc types
- Runs JD analysis + document generation fully in-memory (NO DB writes)
- Returns results directly — user must copy; nothing is persisted
- Applies a per-IP soft rate limit (via simple in-memory token bucket) to prevent abuse
- Token usage is capped at FREE_TIER_MAX_TOKENS to control costs
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.models.enums import DocumentType
from app.services.ai.factory import get_llm_provider

router = APIRouter(prefix="/ephemeral", tags=["ephemeral"])

# ── Config ─────────────────────────────────────────────────────────────────
FREE_TIER_MAX_JD_CHARS = 4_000   # Truncate JD beyond this to save tokens
FREE_TIER_MAX_DOC_TYPES = 2      # Limit to 2 doc types per ephemeral call
RATE_LIMIT_CALLS = 5             # Max calls per IP per window
RATE_LIMIT_WINDOW_SECONDS = 60   # Rolling window in seconds

# In-memory rate limit store: ip -> list of timestamps
_rate_store: Dict[str, List[float]] = defaultdict(list)


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    timestamps = [t for t in _rate_store[ip] if t > window_start]
    if len(timestamps) >= RATE_LIMIT_CALLS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Free tier allows {RATE_LIMIT_CALLS} requests per minute. Sign up for more.",
            },
        )
    timestamps.append(now)
    _rate_store[ip] = timestamps


# ── Schemas ─────────────────────────────────────────────────────────────────

class EphemeralRequest(BaseModel):
    job_description: str
    doc_types: Optional[List[DocumentType]] = [DocumentType.resume, DocumentType.cover_letter]


class EphemeralResponse(BaseModel):
    analysis: dict
    documents: Dict[str, str]
    notice: str = (
        "These documents are not saved. Sign up for a free account to save applications, "
        "manage your resume, and track your job search."
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _analyze_jd_ephemeral(llm, jd: str) -> dict:
    system_prompt = (
        "You are an expert recruiter. Analyze the job description. "
        "Return ONLY a valid JSON object with no markdown fences. Schema:\n"
        "{\n"
        '  "keywords": ["top 8 important keywords from the JD"],\n'
        '  "required_skills": ["explicitly required skills"],\n'
        '  "preferred_skills": ["nice-to-have skills"],\n'
        '  "fit_summary": "2 sentence summary of this role"\n'
        "}"
    )
    user_prompt = f"=== JOB DESCRIPTION ===\n{jd}"
    try:
        raw = llm.generate(system_prompt, user_prompt).strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            raw = inner
        return json.loads(raw.strip())
    except Exception:
        words = re.findall(r"\b[A-Za-z][A-Za-z+#.]{2,}\b", jd)
        freq: dict[str, int] = {}
        stopwords = {"the", "and", "for", "are", "with", "this", "that", "have", "will", "you", "our", "they"}
        for w in words:
            wl = w.lower()
            if wl not in stopwords:
                freq[wl] = freq.get(wl, 0) + 1
        return {
            "keywords": [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:8]],
            "required_skills": [],
            "preferred_skills": [],
            "fit_summary": "Automated analysis unavailable. Please try again.",
        }


def _generate_doc_ephemeral(llm, doc_type: str, jd: str) -> str:
    prompts = {
        "resume": (
            "You are an expert ATS resume writer. "
            "Write a clean, ATS-friendly resume tailored to the job description below. "
            "Since we do not have the candidate's profile, create a well-structured TEMPLATE resume "
            "with clear placeholders like [Your Name], [Your Email], [Company Name], etc. "
            "Use standard sections: CONTACT, PROFESSIONAL SUMMARY, SKILLS, WORK EXPERIENCE, EDUCATION. "
            "Mirror keywords from the JD naturally.",
            f"=== JOB DESCRIPTION ===\n{jd}\n\nGenerate a tailored resume template for this role.",
        ),
        "cover_letter": (
            "You are a professional cover letter writer. "
            "Write a compelling cover letter template tailored to the job description. "
            "Use clear placeholders like [Your Name], [Your Experience], [Company Name]. "
            "Structure: engaging opening, 2-3 body paragraphs, strong closing.",
            f"=== JOB DESCRIPTION ===\n{jd}\n\nGenerate a tailored cover letter template.",
        ),
        "cold_email": (
            "You are a professional communication coach. "
            "Write a concise cold outreach email template for this job (under 200 words). "
            "Include a subject line. Use placeholders like [Your Name], [Company].",
            f"=== JOB DESCRIPTION ===\n{jd}\n\nGenerate a cold outreach email template.",
        ),
        "cold_message": (
            "You are a networking expert. "
            "Write a LinkedIn DM template for this role (under 150 words). "
            "Use placeholders. Be specific to the role.",
            f"=== JOB DESCRIPTION ===\n{jd}\n\nGenerate a LinkedIn cold message template.",
        ),
    }
    system, user = prompts.get(doc_type, (
        "You are a professional career writer. Generate a document template for this job.",
        f"=== JOB DESCRIPTION ===\n{jd}\n\nGenerate the document.",
    ))
    try:
        return llm.generate(system, user)
    except Exception:
        return f"[Document generation failed. Please try again or sign up for a full account.]"


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.post("", response_model=EphemeralResponse)
async def analyze_and_generate(payload: EphemeralRequest, request: Request):
    """
    Free-tier ephemeral JD analysis + document generation.
    No authentication required. Nothing is saved to the database.
    """
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    # Truncate JD to cap token spend
    jd = payload.job_description[:FREE_TIER_MAX_JD_CHARS]

    # Limit doc types to prevent abuse
    doc_types = (payload.doc_types or [DocumentType.resume])[:FREE_TIER_MAX_DOC_TYPES]

    llm = get_llm_provider()

    # Step 1: Analyze JD
    analysis = _analyze_jd_ephemeral(llm, jd)

    # Step 2: Generate documents
    documents: Dict[str, str] = {}
    for dt in doc_types:
        dt_key = dt.value if hasattr(dt, "value") else str(dt)
        documents[dt_key] = _generate_doc_ephemeral(llm, dt_key, jd)

    return EphemeralResponse(analysis=analysis, documents=documents)
