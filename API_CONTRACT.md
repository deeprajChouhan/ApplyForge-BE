# API Contract (Frontend Reference)

Base URL: `/api/v1`

Auth: Bearer access token in `Authorization: Bearer <token>` for all protected endpoints.

## AUTH

### `POST /auth/register`
Public.

Request:
```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

Response 200:
```json
{
  "id": 1,
  "email": "user@example.com"
}
```

### `POST /auth/login`
Public.

Request:
```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

Response 200:
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer"
}
```

### `POST /auth/refresh`
Public.

Request:
```json
{
  "refresh_token": "..."
}
```

Response 200:
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer"
}
```

### `POST /auth/logout`
Public (refresh-token based).

Request:
```json
{
  "refresh_token": "..."
}
```

Response 200:
```json
{
  "message": "logged out"
}
```

### `GET /auth/me`
Protected.

Response 200:
```json
{
  "id": 1,
  "email": "user@example.com"
}
```

---

## PROFILE

### `GET /profile`
Protected. Returns existing profile or auto-creates blank profile.

Response 200:
```json
{
  "id": 1,
  "user_id": 1,
  "full_name": "Jane Doe",
  "headline": "Backend Engineer",
  "summary": "...",
  "location": "Austin, TX"
}
```

### `PUT /profile`
Protected.

Request:
```json
{
  "full_name": "Jane Doe",
  "headline": "Backend Engineer",
  "summary": "...",
  "location": "Austin, TX"
}
```

Response: same shape as `GET /profile`.

### CRUD by section
Protected.

Sections:
- `experiences`
- `educations`
- `projects`
- `skills`
- `certifications`

#### `GET /profile/{section}`
Returns array of records for that section.

#### `POST /profile/{section}`
Create one record for section.

#### `PUT /profile/{section}/{item_id}`
Update owned record.

#### `DELETE /profile/{section}/{item_id}`
Delete owned record.

Payload schemas by section:

- `experiences`
```json
{
  "company": "Acme",
  "role": "Software Engineer",
  "description": "...",
  "start_date": "2023-01-01",
  "end_date": "2024-01-01"
}
```

- `educations`
```json
{
  "institution": "MIT",
  "degree": "BS",
  "field_of_study": "CS",
  "start_date": "2018-01-01",
  "end_date": "2022-01-01"
}
```

- `projects`
```json
{
  "name": "ApplyForge",
  "description": "...",
  "technologies": "Python,FastAPI"
}
```

- `skills`
```json
{
  "name": "Python",
  "level": "advanced"
}
```

- `certifications`
```json
{
  "name": "AWS Solutions Architect",
  "issuer": "Amazon",
  "issue_date": "2025-01-01"
}
```

### `POST /profile/resume/upload`
Protected. `multipart/form-data` with `file` (PDF/DOCX/TXT).

Response 200:
```json
{
  "file_id": 10,
  "filename": "resume.pdf"
}
```

### `POST /profile/resume/{file_id}/parse`
Protected.

Response 200:
```json
{
  "parse_id": 99,
  "confidence_score": 0.65,
  "structured_data": {
    "summary": "...",
    "skills": ["Python", "FastAPI"]
  }
}
```

### `POST /profile/knowledge/rebuild`
Protected.

Response 200:
```json
{
  "chunks_indexed": 42
}
```

---

## KNOWLEDGE

### `POST /knowledge/reindex`
Protected.

Response 200:
```json
{
  "chunks_indexed": 42
}
```

### `POST /knowledge/search`
Protected.

Request:
```json
{
  "query": "What backend skills do I have?",
  "top_k": 5
}
```

Response 200:
```json
[
  {
    "chunk_id": 1,
    "content": "...",
    "score": 0.88
  }
]
```

---

## APPLICATIONS

### `POST /applications`
Protected.

Request:
```json
{
  "company_name": "Acme",
  "role_title": "Senior Backend Engineer",
  "job_description": "..."
}
```

Response 200:
```json
{
  "id": 1,
  "company_name": "Acme",
  "role_title": "Senior Backend Engineer",
  "job_description": "...",
  "status": "draft",
  "jd_analysis_json": null,
  "created_at": "2026-04-22T00:00:00"
}
```

### `GET /applications?status=<status>`
Protected. Optional status filter.

Allowed statuses:
- `draft`
- `ready`
- `applied`
- `follow_up`
- `interview`
- `replied`
- `rejected`
- `offer`
- `archived`

Response: `ApplicationOut[]`

### `GET /applications/kanban`
Protected. Returns grouped applications by status.

Response 200:
```json
{
  "draft": [],
  "ready": [],
  "applied": []
}
```

### `GET /applications/{app_id}`
Protected.

Response: `ApplicationOut`.

### `PATCH /applications/{app_id}`
Protected.

Request (partial):
```json
{
  "company_name": "NewCo",
  "role_title": "Principal Engineer",
  "job_description": "updated jd"
}
```

Response: `ApplicationOut`.

### `POST /applications/{app_id}/status`
Protected.

Request:
```json
{
  "status": "applied",
  "note": "Applied via company portal"
}
```

Response: `ApplicationOut`.

### `POST /applications/{app_id}/analyze`
Protected.

Request:
```json
{
  "job_description": "..."
}
```

Response 200:
```json
{
  "keywords": ["..."],
  "required_skills": [],
  "preferred_skills": [],
  "strengths": ["Evidence-backed profile alignment"],
  "unsupported_gaps": ["Explicitly mark missing requirements manually"],
  "fit_summary": "Preliminary fit summary based on user evidence only."
}
```

### `POST /applications/{app_id}/generate`
Protected.

Request:
```json
{
  "doc_types": ["resume", "cover_letter", "cold_email", "cold_message"]
}
```

Response 200:
```json
[
  {
    "id": 1,
    "user_id": 1,
    "application_id": 1,
    "doc_type": "resume",
    "version": 1,
    "content": "...",
    "format": "txt",
    "created_at": "2026-04-22T00:00:00",
    "updated_at": "2026-04-22T00:00:00"
  }
]
```

---

## CHAT

### `POST /chat/{application_id}/messages`
Protected.

Request:
```json
{
  "content": "How should I prepare for this role?"
}
```

Response 200:
```json
{
  "ok": true
}
```

### `GET /chat/{application_id}/messages`
Protected.

Response 200:
```json
[
  {
    "id": 1,
    "sender_role": "user",
    "content": "...",
    "created_at": "2026-04-22T00:00:00"
  },
  {
    "id": 2,
    "sender_role": "assistant",
    "content": "...",
    "created_at": "2026-04-22T00:00:00"
  }
]
```

---

## DOCUMENTS

### `GET /documents/{doc_id}/download`
Protected.

Returns `text/plain` with header:
- `Content-Disposition: attachment; filename="<doc_type>_v<version>.txt"`

---


## RESUME TEMPLATES

Resume templates are first-class persisted objects. Every user gets access to
a fixed set of read-only **system templates** (Classic ATS, Modern,
Two-Column Sidebar, Executive) plus any **user-owned templates** they create
by duplicating or building from scratch. All queries are scoped by `user_id`
(system templates are the only shared rows and are never mutated by users).

Template config schema (`config`):

```json
{
  "version": 1,
  "layout": "single_column | two_column | centered",
  "sections": [
    { "key": "header",     "label": "Header",               "enabled": true, "order": 0 },
    { "key": "summary",    "label": "Professional Summary", "enabled": true, "order": 1 },
    { "key": "skills",     "label": "Skills",               "enabled": true, "order": 2 },
    { "key": "experience", "label": "Work Experience",      "enabled": true, "order": 3 },
    { "key": "projects",   "label": "Projects",             "enabled": true, "order": 4 },
    { "key": "education",  "label": "Education",            "enabled": true, "order": 5 },
    { "key": "certifications", "label": "Certifications",   "enabled": true, "order": 6 }
  ],
  "styles": {
    "font_family": "Helvetica",
    "body_font_size": 10,
    "heading_font_size": 12,
    "line_height": 1.35,
    "section_spacing": 8,
    "margin_top": 40, "margin_bottom": 40, "margin_left": 46, "margin_right": 46,
    "accent_color": "#1D4ED8",
    "header_style": "left"
  }
}
```

Section `key` values are a closed set — unknown keys are dropped on write.
Missing sections are appended, disabled, at the end so every renderer
receives a fully-populated list.

### `GET /resume-templates`
List every template visible to the current user (system + user-owned).

Response 200:
```json
{
  "templates": [
    { "id": 1, "user_id": null, "name": "Classic ATS", "base_template": "classic",
      "is_system": true,  "is_default": true,  "config": { ... },
      "created_at": "...", "updated_at": "..." },
    { "id": 12, "user_id": 4, "name": "My Modern", "base_template": "modern",
      "is_system": false, "is_default": false, "config": { ... }, ... }
  ],
  "default_template_id": 1
}
```

### `GET /resume-templates/{id}`
Fetch one template. Returns `404` if it is neither a system template nor
owned by the current user.

### `POST /resume-templates`
Create a user-owned template.

Request:
```json
{ "name": "My Custom", "base_template": "modern", "config": { ... } }
```
Response `201`: single `ResumeTemplateOut` (as above).

Errors:
- `422` unknown `base_template` (must be one of `classic|modern|sidebar|executive`).

### `PATCH /resume-templates/{id}`
Update an existing user-owned template. Body may include `name`, `config`,
or both. System templates return `403` — duplicate first.

### `POST /resume-templates/{id}/duplicate`
Duplicate any accessible template (including a system template) into a new
user-owned template. Response `201`: the new template.

Request:
```json
{ "name": "Optional new name" }
```

### `DELETE /resume-templates/{id}`
Soft-delete a user-owned template. Any application/user default currently
pointing at it is cleared to `null`, so subsequent renders fall back to
Classic ATS. System templates return `403`.

### `POST /resume-templates/default`
Set the current user's account-level default template.

Request:
```json
{ "template_id": 12 }
```
Response `200`: the newly-defaulted `ResumeTemplateOut` (with
`is_default: true`).

### `PATCH /applications/{app_id}/template`
Set (or clear) which template this specific application uses. Does NOT
change the account-level default.

Request:
```json
{ "template_id": 12 }
```
`template_id: null` detaches — subsequent renders resolve back to the
user's default and then Classic ATS.

Response 200:
```json
{
  "application_id": 5,
  "template_id": 12,
  "resolved": {
    "id": 12, "name": "My Modern", "base_template": "modern",
    "is_system": false, "source": "application"
  }
}
```

### `GET /applications/{app_id}/template`
Return the template currently resolved for this application, including the
full normalized `config` so the frontend can render a live preview without
a second call.

`source` in the response is one of: `application`, `generated_document`,
`user_default`, `classic_fallback`.

---

## Common Error Responses

- `400` Bad Request (validation/business rule)
- `401` Unauthorized (missing/invalid token, invalid refresh token)
- `404` Not Found (resource not found or not owned by current user)
- `422` Validation error (Pydantic/FastAPI request validation)

## RECRUITER — pipeline unification & user-testing fixes (2026-11-02)

Base: `/api/v1/recruiter`. This contract did not previously cover the recruiter
module; this section documents only what changed in this release.

### Timestamps
All recruiter `*Out` models now serialise naive-UTC datetimes with a trailing
`Z` (e.g. `"2026-11-05T10:00:00Z"`). Clients must render with the viewer's clock.

### Stage automation (single source of truth: `Application.stage`)
Pipeline order: `sourced → screening → submitted → interview → offer → placed`, `rejected` terminal.
Auto-advances are forward-only and logged as `system` activity notes:

| Action | New stage |
|---|---|
| `PATCH /agencies/{a}/applications/{id}/screening` with `mark_completed: true` | `screening` |
| `POST /agencies/{a}/submissions` with `mark_submitted: true`, or `PATCH …/submissions/{id}` → `status: submitted` | `submitted` |
| `POST /agencies/{a}/interviews` | `interview` |
| `POST /agencies/{a}/offers` | `offer` |
| `POST /agencies/{a}/placements` (`application_id` optional; else matched on role+candidate) | `placed` |
| Client decision `reject` (submission feedback, public share link, or submission `status: rejected`) while at `submitted` | `rejected` |

`PATCH /agencies/{a}/applications/{id}/stage` — body `{ "stage": ApplicationStage, "reason"?: string ≤500 }`. Manual moves may go any direction; `reason` is logged.

### Duplicates
`(role_id, candidate_id)` is unique on `rec_applications` (migration `0034`).
`POST …/roles/{r}/pipeline/assign` de-dupes repeated ids; a lost race returns **409**.
`POST /agencies/{a}/applications` with a `role_id` already in the pipeline returns **409**.

### Interviews
`InterviewCreate` / `InterviewUpdate` / `InterviewOut` gain `timezone?: string` (IANA, display context).
`confirmed_time` accepts tz-aware ISO input and is stored as UTC; `proposed_times` are returned as `…Z` strings.
`interviewers[]` items may carry `timezone`.

`POST /agencies/{a}/interviews/{id}/feedback` — one row **per interviewer**: upserts on
`interviewer_email` (case-insensitive), else on `interviewer_name`. New field `interviewer_email`.
Scores must be 1–5. If neither name nor email is sent, the logged-in recruiter is recorded.
`InterviewFeedbackOut` gains `interviewer_email`, `updated_at`.

### Keyword densification
`POST /agencies/{a}/roles/keywords/densify`
```json
{ "title": "Data Engineer", "seniority": null, "description": "…",
  "required_skills": ["python", "sql"], "preferred_skills": [] }
```
→ `200`
```json
{ "suggestions": [{ "skill": "aws", "kind": "required", "category": "domain",
    "sources": ["jd", "pool"], "reason": "Mentioned in the role title/description",
    "confidence": 0.95, "pool_count": 12, "aliases": ["amazon web services"] }],
  "synonyms": { "python": [] }, "boolean_search": "python AND sql AND (…)",
  "keyword_count": 2, "is_thin": true, "advice": "…", "used_llm": true }
```
Sources: `jd` (found in title/description), `pool` (co-occurs in this agency's candidate pool), `ai` (LLM, grounded on the inputs; deterministic adjacency fallback when no LLM). Suggestions only — nothing is written to the role.

### Fit score v2 (`ShortlistEntry.score_breakdown`)
Keys: `semantic` (30), `required_skills` (35), `preferred_skills` (10), `experience` (15), `title` (10).
Re-generate shortlists to refresh scores computed by v1.

---

## RECRUITER — SECURE CANDIDATE DOCUMENTS

CVs are stored through the StorageService (S3/MinIO/Blob; local disk in dev) under
`recruiter/{agency}/candidates/{candidate}/`. The storage location is never returned.
`CandidateDetailOut.source_file` is now the **original filename only** (older rows that held
a server path are stripped to the basename).

| Method | Path (under `/api/v1/recruiter`) | Notes |
|---|---|---|
| GET | `/agencies/{a}/candidates/{c}/documents` | `[{id, candidate_id, application_id, kind, original_filename, content_type, size_bytes, uploaded_via, created_at}]` |
| GET | `/agencies/{a}/candidates/{c}/documents/{d}/download` | Streams the file. `Content-Disposition: attachment`, `nosniff`, `no-store`. |
| DELETE | `/agencies/{a}/candidates/{c}` | Erasure: stored files, applications and every child row. `204`. Fires `candidate.deleted`. |

Bulk ingest (`POST /agencies/{a}/candidates/ingest`) now validates each file (PDF/DOCX/DOC/TXT, ≤10 MB,
magic bytes must match the extension, ≤50 files) and returns `400` with a reason instead of
silently creating a profile without a file.

---

## RECRUITER PARTNER API v1

Base: **`/api/v1/recruiter/partner/v1`**. Key-authenticated integration surface for agencies and
their clients. Everything is agency-scoped; ids are opaque GUIDs (raw ints → `404`).

### Authentication
* `Authorization: Bearer <key>` (or `X-ApplyForge-Key: <key>`). **Never** in the URL — a key-like
  query parameter returns `400 key_in_url`.
* **Secret key** `afk_live_…` / `afk_test_…` — server-to-server. Refused (`403 secret_key_in_browser`)
  when the request carries an `Origin` header.
* **Publishable key** `afp_live_…` — browser careers forms. Scopes are capped at `jobs:read_public` +
  `applications:create`; the request `Origin` must exactly match the key's allow-list.
* **Client-scoped key** — narrowed to one client: only that client's jobs, and only applications that
  client's keys submitted. It can edit/close only jobs it created through the API (agency-created
  jobs → `403 agency_managed_job`). Its submissions never link to an agency-sourced profile: if the
  email already exists in the agency pool, a separate profile is created and the recruiter is told
  to review/merge — the client never sees the agency's record. Pipeline counts on `GET /jobs/{id}`
  count only its own applicants. Max scopes: `jobs:read`, `jobs:write`, `jobs:read_public`,
  `applications:read`, `applications:create`, `documents:read`.
* **Test keys** (`*_test_*`) validate and dry-run writes (`{"test_mode": true, "valid": true}`) and
  cannot read private data (`403 test_key_private_read`).
* API access = plan feature `partner_api` (Pro/Enterprise) unless the operator overrides it
  (`plan` | `enabled` | `disabled`). Suspended/pending agencies → `403`. Trial-locked → writes `402`.

Errors: `{"detail": {"error": "<code>", "message": "<text>"}}`. Common codes: `unauthenticated`,
`invalid_key`, `insufficient_scope`, `origin_not_allowed`, `rate_limited`, `not_found`,
`agency_key_required`, `job_closed`, `invalid_application`, `invalid_resume`, `idempotency_mismatch`.

Request size: bodies over 11 MB → `413`; chunked bodies without `Content-Length` → `411`.
Pre-auth limit: 600 requests/min per IP (IPv6 grouped by /64) before any key lookup.

Rate limits: per key per minute (default 120 secret / 30 publishable; owner may lower, operator may
raise). Public application submissions are additionally limited per client IP per hour (default 20).
Every response carries `X-RateLimit-Limit/Remaining/Reset`; `429` carries `Retry-After`.

Idempotency: send `Idempotency-Key` (1–128 chars `[A-Za-z0-9-_.:]`) on POSTs with a secret key. The
same key + same body replays the stored response (`Idempotent-Replayed: true`) for 24 h; same key +
different body → `422 idempotency_mismatch`.

Pagination: `limit` (≤100, default 50) + `offset` → `{data: [...], has_more, next_offset}`.

### Scopes
`jobs:read`, `jobs:write`, `jobs:read_public`, `applications:read`, `applications:create`,
`applications:write`, `candidates:read`, `candidates:write`, `candidates:delete`, `documents:read`,
`clients:read`, `clients:write`, `matching:run`, `usage:read`.
Default secret key = all except `candidates:delete` (erasure must be granted explicitly).

### Endpoints
| Method | Path | Scope | Notes |
|---|---|---|---|
| GET | `/me` | any | Key name/prefix/kind/env, agency, client scope, scopes, rate limit. Connection test. |
| GET | `/meta` | any | Pipeline stages, webhook events, scope descriptions. |
| GET | `/public/jobs` · `/public/jobs/{job_id}` | `jobs:read_public` | Open, published jobs: title, description, type, location, country, seniority, skills, salary range, currency, posted_at. **No** client, budget or notes. |
| GET | `/jobs` | `jobs:read` | Filters `status`, `external_ref`, `updated_since`. |
| GET | `/jobs/{job_id}` | `jobs:read` | Adds `pipeline: {stage: count}`. |
| POST | `/jobs` | `jobs:write` | `JobCreate` (below). `201`. `409` if `external_ref` exists. |
| PUT | `/jobs/by-external-ref/{external_ref}` | `jobs:write` | Upsert by your own id. `201` created / `200` updated. |
| PATCH | `/jobs/{job_id}` | `jobs:write` | Partial update. |
| POST | `/jobs/{job_id}/close?status=closed\|filled\|on_hold` | `jobs:write` | Fires `job.closed`. |
| POST | `/jobs/parse` | `jobs:write` | `{text}` → `{used_llm, draft}`; nothing saved. |
| POST | `/jobs/{job_id}/applications` | `applications:create` | **multipart** (below). `202`. |
| GET | `/applications` | `applications:read` | Filters `job_id`, `stage`, `updated_since`. |
| GET | `/jobs/{job_id}/applicants?stage=sourced\|<stage>\|all&sort=fit_score\|applied_at` | `applications:read` | Client-portal list, best fit first (unscored last). `{job_id, stage, total, scored, data[], has_more, next_offset}`. Each row: `rank, application_id, candidate_id, name, headline, location, years_experience, fit_score, match{reasons,gaps,breakdown,parse_quality}, skills{matched_required, matched_preferred, missing_required, other}, stage, source, channel, processing_status, applied_at, last_activity_at, cover_letter, answers[], contact{email,phone,linkedin_url}\|null, documents[]\|null, screening{…}`. `contact` needs `candidates:read` (agency key) or the client's own applicants; `documents` needs `documents:read`; `screening` = recruiter fields the agency left client-visible (via `to_client_view`, never internal notes). Client keys see only applicants they submitted. |
| GET | `/applications/{application_id}` | `applications:read` | Stage, processing status, fit score, `match {reasons, gaps, breakdown, parse_quality}`. |
| GET | `/applications/{application_id}/status` | `applications:read` | Interviews (stage/type/status/time/tz/duration), offers (status), placement (status, start date). No fees, notes or compensation. |
| POST | `/applications/{application_id}/stage` | `applications:write` (agency key) | `{stage, reason?}`. Non-optimistic: response is stored state. |
| POST | `/applications/{application_id}/notes` | `applications:write` (agency key) | `{body}` ≤5000. |
| POST | `/jobs/{job_id}/pipeline?candidate_id=` | `applications:write` (agency key) | Put an existing pool candidate on a job at `sourced`. |
| GET | `/applications/{application_id}/documents` | `documents:read` | Metadata. |
| GET | `/documents/{document_id}/download` | `documents:read` | Streams the file (no shareable URL is ever minted). |
| GET | `/candidates/{candidate_id}` | `candidates:read` (agency key) | Name, email, phone, headline, location, YoE, skills, LinkedIn, source, documents. |
| POST | `/candidates` | `candidates:write` (agency key) | multipart: `email` (req), `full_name`, `phone`, `headline`, `location`, `linkedin_url`, `resume`. Dedupes on email (`200 {created:false}`). |
| DELETE | `/candidates/{candidate_id}` | `candidates:delete` (agency key) | GDPR erasure → `{erased, files_removed, applications_removed}`. |
| GET / POST | `/clients` | `clients:read` / `clients:write` (agency key) | `{name, industry?, website?, external_ref?}` |
| PUT | `/clients/by-external-ref/{external_ref}` | `clients:write` (agency key) | Upsert. |
| POST | `/jobs/{job_id}/match?limit=25` | `matching:run` (agency key) | Runs inverted matching → ranked entries with `reasons`, `gaps`, `breakdown`. Metered. |
| GET | `/jobs/{job_id}/shortlist` | `matching:run` (agency key) | Latest shortlist. |
| GET | `/usage` | `usage:read` (agency key) | This month's metered actions + request count. |
| GET | `/feeds/{agency_slug}/jobs.xml` | none | Indeed-style XML of open jobs. **Off by default**; owner toggles it. Public job fields only. |

`JobCreate` / `JobWrite` (extra fields rejected):
`title` (req on create), `description` ≤20000, `status` (open|on_hold|filled|closed), `is_draft`,
`employment_type` (full_time|part_time|contract|internship|temporary), `location`,
`country_code` (ISO-2), `seniority`, `required_skills[]`, `preferred_skills[]` (≤50 × ≤60 chars),
`min_years_experience` (0–60), `salary_min`, `salary_max`, `currency` (ISO-3), `client_id` or
`client_external_ref` (agency keys; client keys are pinned to their client), `external_ref`
(`[A-Za-z0-9._:-]{1,128}`). If `description` is given without skills, structured fields are filled in
the background from the description (empty fields only).

Application form (multipart):
`resume` (file, req — PDF/DOCX/DOC/TXT ≤10 MB, magic bytes checked), `first_name`, `last_name`,
`email` (req), `consent=true` (req), `privacy_notice_version` (req), `phone`, `linkedin_url`
(`https://linkedin.com/…`), `location`, `cover_letter` ≤10000, `answers` (JSON array of
`{question, answer}`, ≤30), `consent_text`, `source_site`, `utm_source|medium|campaign`,
`external_ref`, `captcha_token` (required for publishable keys when Turnstile is configured).

Behaviour: dedupes the candidate on email within the agency. A **new** profile is enriched from the
CV; an **existing** profile is never modified by the form (the new CV is attached for the recruiter).
Creates the application at `sourced` with `source=partner_api`, records consent (notice version,
text hash, timestamp, IP), stores the CV, notifies the agency, meters `api_application_received`,
then parses + scores in the background and fires `application.processed`.
Responses: secret key → `202 {application_id, candidate_id, job_id, stage, processing_status}`
(duplicate → `200 … duplicate: true`); publishable key → always `202 {"status": "received"}` so a
browser can't probe which emails already exist.

### Webhooks
Events: `job.created`, `job.updated`, `job.closed`, `application.created`, `application.processed`,
`application.stage_changed`, `candidate.deleted`, `shortlist.completed` (+ `ping` from the test button).
Payload (ids and state only, never PII): `{"id": "evt_…", "type": "...", "created_at": "...Z", "data": {"application_id": "<guid>", "job_id": "<guid>", "stage": "screening", ...}}`.
Headers: `ApplyForge-Event`, `ApplyForge-Delivery`, `ApplyForge-Signature: t=<unix>,v1=<hex>` where
`v1 = HMAC_SHA256(secret, "<t>." + raw_body)`. Reject if |now − t| > 300 s.
Targets: https, public IPs only (re-checked at delivery), no redirects followed, 5 s timeout.
Retries at 1m, 5m, 15m, 1h, 3h, 6h, 12h, 24h; endpoint auto-disables after 20 consecutive failures.
Client-scoped endpoints receive `job.*` for that client's jobs and `application.*` only for
applicants that client submitted — never agency-sourced candidates, shortlists or erasures.
Delivery pins the validated IP (no DNS rebinding), ignores proxy env vars, and reports only
coarse errors (`blocked`, `timeout`, `connection_failed`, or the HTTP status).

### Management — agency owner (`/api/v1/recruiter/agency/integrations`, recruiter owner token)
`GET ""` overview · `PATCH /settings {public_job_feed_enabled}` ·
`GET|POST /api-keys` · `PATCH /api-keys/{key_id}` · `POST /api-keys/{key_id}/revoke {reason?}` ·
`POST /api-keys/{key_id}/roll` · `GET|POST /webhooks` · `PATCH|DELETE /webhooks/{webhook_id}` ·
`POST /webhooks/{webhook_id}/rotate-secret` · `POST /webhooks/{webhook_id}/test` ·
`GET /webhook-deliveries?webhook_id&status` · `POST /webhook-deliveries/{delivery_id}/retry` ·
`GET /request-logs?errors_only&key_prefix` · `GET /audit`.
Create key: `{name, kind: secret|publishable, environment: live|test, scopes?, client_id?, allowed_origins[], expires_in_days? (1–730), rate_limit_per_minute?}` → includes `secret` **once**.
Create webhook → includes signing `secret` **once**. Limits: 25 active keys, 10 webhooks per agency.

### Management — operator (`/api/v1/recruiter/admin/integrations`, consumer admin token)
`GET /agencies` (per-agency access/limits/keys/webhooks/24h traffic) ·
`GET|PATCH /agencies/{agency_id}` `{partner_api_access: plan|enabled|disabled, partner_api_rate_limit, clear_rate_limit}` ·
`GET /agencies/{agency_id}/api-keys` · `PATCH /agencies/{agency_id}/api-keys/{key_id}` `{rate_limit_per_minute}` only ·
`POST /agencies/{agency_id}/api-keys/{key_id}/revoke` · `GET /agencies/{agency_id}/webhooks` ·
`PATCH /agencies/{agency_id}/webhooks/{webhook_id}` `{is_active}` only · `GET /agencies/{agency_id}/webhook-deliveries` ·
`GET /agencies/{agency_id}/request-logs` · `GET /agencies/{agency_id}/audit` ·
`POST /maintenance/backfill-cv-files` (moves legacy CV files into secure storage; idempotent).
Operators **cannot create keys**, widen scopes/origins, or repoint webhooks — they can only
toggle access, tune rate limits, revoke keys and disable/enable endpoints. Secrets are only ever
shown to the agency owner who creates them. Erasure fails closed (`503 erasure_incomplete`) if a
stored file can't be deleted, and also removes notifications/tasks tied to the candidate and
clears the agency's AI insight cache. `POST /jobs/parse` and background JD enrichment are metered
as `api_jd_parsed`; test keys never trigger LLM calls.

### Job sync — linking to roles built in the app (2026-11-11)

`PUT /jobs/by-external-ref/{ref}`: when no role carries `ref` yet, the API first looks for exactly one
role in the agency with **no** `external_ref` whose title slug equals `ref` (or the payload's `title`),
preferring open roles. If found it is **linked** (returns `200`, not `201`) and only empty fields are
filled from the payload — recruiter-written content is never overwritten on that first sync. Zero or
several matches → a new `partner_api` role is created as before. Client-scoped keys never link.

Recruiters can set/clear the link on `PATCH /agencies/{agency_id}/roles/{role_id}` with
`external_ref` (`""`/`null` clears; `409` if another role in the agency already holds it; `422` on bad
characters). `RoleOut` now includes `external_ref` and `source`.

---

## RECRUITER — OPERATOR OPS TOOLS

Base: `/api/v1/recruiter/admin/ops` · consumer admin token (`require_admin`) · ids are public GUIDs.
Every write is appended to `rec_integration_audit` as `operator:<email>`
(actions `role.merged`, `role.external_ref_set`, `role.external_ref_cleared`).
Admin UI: **Admin → Operations → Ops tools** (`/admin/ops`).

| Method | Path | Notes |
|---|---|---|
| GET | `/agencies/{agency_id}/roles?q=` | Search by title or careers-site ref. Returns `OpsRole[]` (`id, title, status, is_draft, source, external_ref, client_id, location, applications, created_at`). |
| GET | `/agencies/{agency_id}/duplicate-roles` | Groups of ≥2 roles whose titles slug to the same key: `{key, title, suggested_keep_id, has_portal_copy, roles: OpsRole[]}`. Suggested keep = the app-built role, else the oldest. |
| POST | `/agencies/{agency_id}/roles/merge` | Body `{source_role_id, target_role_id, move_external_ref=true, delete_source=false, rescore=true, dry_run=true}`. **`dry_run` defaults to true** — returns the plan and writes nothing. With `dry_run=false`: moves the careers-site ref (if asked), moves applications and their consents / submissions / interviews / offers / placements to the target, rescores moved applications against the target spec, optionally deletes the source. Applications whose candidate is already on the target are left on the source (`clashes`). Response: `{source, target, applications_to_move, clashes, related_rows, role_only_rows, warnings, applied, rescored, …options}`. Errors: `422` same role; `409` target already linked to a different ref, or `delete_source` with clashes; `404` role not in agency. |
| PATCH | `/agencies/{agency_id}/roles/{role_id}/external-ref` | Body `{external_ref: string \| null}`. Link / unlink. `409` if held by another role, `422` bad characters. Returns `OpsRole`. |
| GET | `/agencies/{agency_id}/history?limit=50` | Ops audit rows for the agency: `{id, actor, action, target, detail, created_at}`. |

CLI fallback when the API is down: `python scripts/merge_duplicate_role.py --ref <ref> [--apply]`.

---

## RECRUITER — PER-RECRUITER ROLE VISIBILITY (2026-11-12)

Agency owners decide which roles each recruiter seat can see (Team & plan → **Role access**).

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/recruiter/agency/team` | Each member now carries `role_access` (`"all"` \| `"selected"`) and `granted_roles` (count). |
| GET | `/api/v1/recruiter/agency/team/{member_id}/role-access` | Owner only. `{recruiter_id, mode, is_owner, roles: [{id, title, status, client_name, granted}]}` — every role in the agency with whether this seat can see it. |
| PUT | `/api/v1/recruiter/agency/team/{member_id}/role-access` | Owner only. Body `{mode: "all" \| "selected", role_ids: [...]}`. `400` for an owner seat (owners always see everything); `404` if any role isn't in the agency. Grants are kept when switching to `all`, so switching back restores them. |

**Semantics**
- `all` (default for every seat) — sees every role, including new ones.
- `selected` — sees only granted roles plus roles the seat creates itself (auto-granted). New roles stay hidden until granted.
- Owners, platform operators, partner-API keys, public share links and background jobs are never restricted.

**Enforcement** — `app/recruiter/services/role_visibility.py`, bound in `get_agency` / `get_current_recruiter`.
For a restricted seat every ORM statement on the request Session is filtered (`with_loader_criteria`):
`Role` by id; every `rec_*` model with `role_id`; every model with a FK to a role-bearing table
(`application_id`, `shortlist_id`, `submission_id`, `interview_id`, `offer_id`, `placement_id`, …);
`Notification` / `RecruiterTask` by their `parent_kind`/`parent_id`; `AiInsightCache` hidden entirely.
Lists, detail lookups, aggregates/dashboards and lazy relationships all drop hidden data; hidden ids
return `404`; writes that point a row at a hidden role/application are rejected with `404`.
The candidate pool itself is shared — only a candidate's applications/journey on hidden roles is hidden.

Storage: `rec_recruiter_role_restrictions` (row = seat is restricted) and `rec_recruiter_role_grants`
(Alembic `0036_recruiter_role_visibility`; also created at boot). No existing tables change.
