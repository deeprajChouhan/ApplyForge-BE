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
  "job_description": "updated jd",
  "jd_link": "https://…",
  "notes": "Recruiter: Sam. Call Thursday."
}
```

`notes` *(new 2026-11-23)*: free text, max 20 000 characters (`422` above that). Omitted or `null`
leaves it unchanged; `""` clears it. Every field is optional; only the ones sent are changed.

Response: `ApplicationOut`.

`ApplicationOut` *(2026-11-23)* now also returns `notes: string | null` and `updated_at: datetime`.
It does **not** return `date_applied` or `status_changed_at`; status changes live in
`application_status_history`, which no endpoint exposes yet.

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

**Resume generation (2026-11-23):** `doc_types` containing `"resume"` now runs the evidence-mapped
tailoring pipeline below (same as `POST /applications/{app_id}/tailoring`) and returns its plain-text
document; it falls back to the legacy single prompt only if the pipeline errors. Other doc types now use
RAG scoped to the application's `selected_resume_id` plus the stored JD analysis (strengths to lead with,
JD terms to use, gaps not to claim).

### Resume tailoring — `/applications/{app_id}/tailoring` *(new 2026-11-23)*
Protected (feature `jd_analyze`). One row per application in `application_tailorings` (user-scoped,
`ON DELETE CASCADE`). The source resume, profile and accepted suggestions are never mutated — the
tailored resume is an **overlay** applied by the preview and by the PDF/DOCX exporter.

Pipeline: JD analysis (auto-run if missing) → **MAP** (LLM: atomic ATS terms in the JD's exact spelling +
evidence refs into the resume) → **VALIDATE** (no LLM: every evidence quote must exist in the resume,
alias table `aliases.py`; unverifiable → `absent`) → **WRITE** (LLM: headline/summary/skills/bullets per
placement plan, XYZ rules) → **VERIFY** (no LLM: invented numbers revert to the original bullet, unsupported
skills dropped, stuffing > 4 repeats, regex hallucination checker) → **REPAIR** (≤ 1 LLM pass, kept only if
coverage does not drop). 2–3 LLM calls (+1 if the JD analysis had to run).

| Method | Body | Response |
|---|---|---|
| `GET` | — | `TailoringOut` or `null` if never tailored |
| `POST` | — | `TailoringOut` (runs the pipeline; also stores a new `resume` GeneratedDocument version). `422` if no resume/profile data |
| `PATCH` | `{"enabled": bool}` | `TailoringOut` — switch between tailored and original resume |
| `DELETE` | — | `204` |

`TailoringOut`:
```json
{
  "application_id": 12, "enabled": true, "source_resume_id": 7, "generated_document_id": 88,
  "is_stale": false, "stale_reasons": [],
  "tailored": {
    "headline": "Backend Engineer | Python, AI agents, Postgres",
    "summary": "...",
    "skills": ["Python", "AI agents", "Postgres", "FastAPI", "PostgreSQL"],
    "experiences": [{"ref": "E1", "role": "Backend Engineer", "company": "Acme", "description": "• ...\n• ..."}],
    "projects": [{"ref": "P1", "name": "ApplyForge", "description": "• ..."}]
  },
  "report": {
    "version": 1, "aliases_version": "2026-11-23.1", "target_title": "AI Engineer",
    "plan_source": "llm | deterministic", "repair_pass": false, "llm_calls": 2, "rejected_evidence": 1,
    "source": {"resume_id": 7, "parse_confidence": 0.91, "jd_hash": "..."},
    "coverage": {
      "before": {"weighted_pct": 41, "matched": 5, "total": 14, "required_matched": 2, "required_total": 6},
      "after":  {"weighted_pct": 83, "matched": 11, "total": 14, "required_matched": 5, "required_total": 6}
    },
    "terms": [{"term": "Postgres", "importance": "required", "status": "explicit|implicit|adjacent|absent",
               "found_before": false, "found_after": true, "count_after": 2,
               "placed_in": ["Skills", "Backend Engineer @ Acme"],
               "evidence": [{"ref": "E1.2", "label": "Backend Engineer @ Acme · bullet 2", "quote": "Designed PostgreSQL schemas"}],
               "note": ""}],
    "gaps": [{"term": "Kubernetes", "importance": "required", "note": "No evidence in your resume — not added. ..."}],
    "warnings": [{"kind": "invented_number|unsupported_skill|missing_section|hallucination_check|generation_failed",
                  "where": "Backend Engineer @ Acme", "detail": "..."}],
    "hallucination_flags": [], "open_issues": [], "generated_at": "2026-11-23T07:00:00Z"
  }
}
```
Coverage is weighted (required 3, keyword 2, preferred 1) and counts only **exact** JD-term matches
(word-boundary, hyphen/space and plural tolerant) — what a literal ATS keyword filter sees.
`is_stale` is true when the selected resume, the JD text, or the applied suggestions changed after tailoring.

### `PATCH /applications/{app_id}/documents/{doc_type}` *(documented 2026-11-23; live before that)*
Protected (feature `applications`). Saves a manual edit to the current document **in place**: no version
bump, so it overwrites the highest non-deleted version. (Chat's `apply-edit` creates a new version instead.)

Request: `{ "content": "…" }` → Response: `GeneratedDocumentOut`.

Errors: `400` unknown `doc_type` · `404` application not owned, or no document of that type generated yet.

### `POST /applications/{app_id}/export/cover-letter`
Protected (feature `applications`). Exports the newest non-deleted `cover_letter` document.

Request:
```json
{
  "recipient_name": "Hiring Manager",
  "recipient_title": null,
  "company_address": null,
  "export_format": "pdf"
}
```

Response 200: binary body. `application/pdf` or
`application/vnd.openxmlformats-officedocument.wordprocessingml.document`, with
`Content-Disposition: attachment; filename="<Name>_<Company>_cover_letter.<ext>"`.
The sender block (name, email, phone, location, LinkedIn) comes from parsed resume → profile.
Known `[placeholder]` tokens in the body are substituted; unknown ones are left visible.
Body text is otherwise unchanged (it is the grounded, hallucination-checked content).

Errors: `400` bad `export_format` · `404` app not owned / no cover letter generated yet.

---

## CHAT — Coach *(2026-11-23)*

All endpoints are protected, require feature `chat`, and filter by `user_id`. An application, thread or
message that isn't the caller's returns `404`. Each application can have several threads.

Every reply is grounded: evidence is numbered `[E1]..[En]` across resume chunks (retrieval scoped to the
application's `selected_resume_id`, falling back to all the user's chunks, the same as `/generate`), the JD
and the current written documents. Only `[En]` markers that map to real evidence are kept, as citations;
unverifiable ones are removed from the text. Every AI call is budget-checked first and recorded after
(`UsageTracker`, feature `chat`).

**Budget:** when the monthly token budget would be exceeded, the send endpoints return the standard
`429` body (`{"error": "token_budget_exceeded", "used", "budget", "message"}`) **before** anything is stored or
streamed. The streaming endpoint never opens a stream in that case.

### Schemas

```jsonc
// ChatMessageOut
{
  "id": 412, "thread_id": 31, "sender_role": "user" | "assistant",
  "status": "streaming" | "complete" | "stopped" | "error",
  "content": "Lead with the Ledgerly payments work [E1]…",
  "citations": [Citation],
  "proposed_edit": ProposedEdit | null,
  "applied_document_id": null,
  "context": { "intent": "qa" | "score" | "edit", "score"?: ScoreTable, "doc_type"?: "cover_letter" } | null,
  "usage": { "tokens_in": 980, "tokens_out": 240 } | null,
  "created_at": "…"
}

// Citation: quote is always a verbatim slice of its source
{ "id": "c1", "source": "resume" | "jd" | "document" | "score", "label": "Resume · excerpt 1",
  "quote": "Built payment services…", "chunk_id": 5531 | null,
  "doc_type": "cover_letter" | null, "doc_version": 2 | null, "char_start": 0, "char_end": 92 }

// ProposedEdit (written documents only: cover_letter, cold_email, cold_message)
{ "doc_type": "cover_letter", "base_document_id": 88, "base_version": 2,
  "new_content": "…full text…",
  "diff": [ { "op": "keep" | "del" | "add", "text": "one sentence" } ],
  "word_count": { "before": 142, "after": 98 },
  "unsupported_claims": [ { "claim", "claim_type", "reason", "confidence", "context" } ],  // HallucinationChecker ≥ 0.6
  "reverted_numbers": ["300%"] }   // sentences with numbers not in the resume, current doc or JD were removed

// ScoreTable (context.score, intent "score"): numbers come from the scorer, the LLM only phrases them
{ "priority_score", "fit_score", "competition_score", "fit_breakdown": {…}, "competition_breakdown": [CompetitionRow] }

// ChatThreadOut
{ "id": 31, "application_id": 12, "title": "Shorten the cover letter", "last_message_at": "…" | null,
  "message_count": 6, "created_at": "…" }

// ChatContextOut: drives the "Knows" chips
{ "resume": { "id": 7, "label": "backend.pdf · 21 Nov 2026", "parse_confidence": 0.92 } | null,
  "jd": { "present": true, "analysed": true },
  "documents": [ { "doc_type": "resume", "version": 3 }, { "doc_type": "cover_letter", "version": 2 } ],
  "priority_score": 78.0 | null }
```

### Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/chat/{application_id}/context` | — | `ChatContextOut` |
| `GET` | `/chat/{application_id}/threads` | — | `ChatThreadOut[]`, newest activity first, archived excluded |
| `POST` | `/chat/{application_id}/threads` | `{ "title"?: str ≤120 }` | `201 ChatThreadOut` |
| `PATCH` | `/chat/threads/{thread_id}` | `{ "title"?: str ≤120, "archived"?: bool }` | `ChatThreadOut` |
| `GET` | `/chat/threads/{thread_id}/messages` | — | `ChatMessageOut[]`, oldest first |
| `POST` | `/chat/threads/{thread_id}/messages` | `{ "content": str 1–4000 }` | `text/event-stream`, below |
| `POST` | `/chat/messages/{message_id}/stop` | — | `ChatMessageOut` (`status: "stopped"` if it was streaming) |
| `POST` | `/chat/messages/{message_id}/apply-edit` | `{ "base_version": int ≥1 }` | `GeneratedDocumentOut`, a **new version** |

Threads are auto-titled from their first user message (≤60 chars, no LLM) unless a title was given.

**Intent routing** (keywords/regex, no LLM call):
- `edit`: a written-document mention (cover letter, cold email, LinkedIn note) plus a rewrite verb
  (shorten, rewrite, tighten, make it more direct, …) and that document exists. The reply is a short
  summary plus `proposed_edit`. Resume edit requests are answered as `qa` (resume changes go through
  tailoring and Suggestions). Asking to edit a document that doesn't exist yet is answered as `qa`.
- `score`: mentions of score, priority, fit, competition or reply likelihood, when the JD is analysed.
  `context.score` carries the scorer's table; a `score` citation is always attached.
- `qa`: everything else.

**`apply-edit`:** creates `GeneratedDocument` with `version = current + 1`, same doc type, same
`resume_template_id`; earlier versions are kept. The claims are re-checked at apply time.
Errors:
- `404` message not owned, or it has no `proposed_edit`
- `409 {"error": "already_applied", "document_id": N}`
- `409 {"error": "stale_base", "current_version": N}`: the document moved on since the proposal
- `422 {"error": "unsupported_claims", "flags": […]}`: the re-check still flags claims

### Stream events (`POST /chat/threads/{thread_id}/messages`)

Each event is `event: <name>` + `data: <json>`. Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

```
event: message.created   data: {"user_message": ChatMessageOut, "assistant_message_id": 412}
event: status            data: {"phase": "generating" | "checking"}
event: delta             data: {"text": "…"}           // qa/score: streamed text; edit: the summary, once
event: citation          data: Citation                // after the text, validated
event: proposed_edit     data: ProposedEdit            // edit intent only
event: done              data: {"message": ChatMessageOut}   // status "complete" or "stopped"
event: error             data: {"error": "provider_unavailable" | "internal", "message": str}
```

- The user message is stored and announced in `message.created` before any text; clients append it only
  then (non-optimistic).
- `done.message.content` is the cleaned text (unverifiable `[En]` markers removed): clients should
  replace the streamed text with it.
- **Stop:** `POST /chat/messages/{id}/stop` marks a streaming message `stopped`. The stream checks between
  chunks, keeps the partial text and ends with `done` (`status: "stopped"`). If the client disconnects,
  the partial reply is saved as `stopped` the same way. Tokens actually spent are always recorded.
- The provider is never retried mid-stream (that would duplicate text and double-charge tokens).
- Usage: real `prompt_tokens`/`completion_tokens` from the provider when it reports them
  (`stream_options.include_usage`), otherwise estimated. Recorded as endpoint `chat.stream`.

### Legacy (deprecated): kept until the old drawer and the extension move to threads

- `POST /chat/{application_id}/messages` `{ "content" }` → `{"ok": true}`. Writes to the application's most
  recent thread, creating one if needed. Not streamed. Same grounding, budget and usage rules
  (endpoint `chat.send`).
- `GET /chat/{application_id}/messages` → `ChatMessageOut[]` of the most recent thread (`[]` if none). The old
  fields (`id`, `sender_role`, `content`, `created_at`) are unchanged; the rest are additions.

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

## EXTENSION — Job Clipper (consumer)

All endpoints require a consumer access token. Every query is filtered by `user_id`.
(Previously undocumented here; the existing endpoints are listed for completeness.)

### `GET /extension/status`
`{connected, connected_at, last_seen_at, promo_dismissed_at, chrome_store_url}`

### `GET /extension/settings` *(new 2026-11-21)*
Job Hunting → Extension preferences. Defaults are returned when nothing is saved.
```json
{
  "enabled": true,
  "enabled_sites": ["linkedin", "indeed", "glassdoor", "other"],
  "default_resume_id": null,
  "default_resume_label": "cv.pdf · 01 Nov 2026",
  "auto_score": true,
  "quick_fit": true,
  "show_card_labels": true,
  "widget_position": "inline",
  "must_have_keywords": [],
  "excluded_companies": [],
  "default_save_status": "draft",
  "open_after_save": false,
  "warn_duplicates": true
}
```
- `enabled_sites` ⊆ `linkedin | indeed | glassdoor | other` (`other` = Beta JSON-LD sites). Unknown keys are dropped.
- `widget_position`: `inline | top-right | top-left | bottom-right | bottom-left`.
- `default_save_status`: `draft | ready | applied`.
- `default_resume_id`: a `ParsedResumeData.id` owned by the user; `null` = latest resume. An archived pinned resume reads back as `null`.
- `default_resume_label`: resume actually used (pinned or latest); `null` if the user has none.

### `PUT /extension/settings` *(new)*
Partial update — any subset of the fields above. Extra field `clear_default_resume: true` resets to "latest".
Lists are trimmed, de-duplicated (case-insensitive), capped at 30 items × 60 chars.
`404` if `default_resume_id` is not the user's. `422` on invalid enum values. Returns the full settings object.

### Score responses: `competition_breakdown` *(2026-11-23)*
`ScoreResponse` (returned by `POST /applications/{app_id}/score`) gains `competition_breakdown`, the signals
behind `competition_score`. The deltas sum to it, so the inspector and the Coach show the same numbers:
```json
"competition_breakdown": [
  {"signal": "base", "delta": 40.0, "evidence": null},
  {"signal": "seniority", "delta": 15.0, "evidence": "\"senior\" in the posting"},
  {"signal": "required_yoe", "delta": 15.0, "evidence": "5+ years asked"},
  {"signal": "big_name_employer", "delta": 20.0, "evidence": "Google"},
  {"signal": "urgency_markers", "delta": -10.0, "evidence": "\"immediate start\""},
  {"signal": "clamp", "delta": -5.0, "evidence": "kept within 0–100"}
]
```
Only signals that fired are listed; `clamp` appears only when the total had to be pulled into 0–100.
`[]` on the preview routes (`/utils/score-preview`, `/extension/jobs/score-preview`).

### `POST /extension/jobs/score-preview`
Body `{title, company, location, description, source_url, source_site, source?}`. Stores nothing.

`source` *(new 2026-11-23)*: `"extension"` (default) or `"web"`. Any other value → `422`.
- `extension`: marks the extension seen and connected, and logs `extension_job_scored`. Unchanged.
- `web`: the consumer web app's New application sheet. Does **not** touch the extension state
  (`connected_at` / `last_seen_at` stay as they were) and logs `web_job_scored` instead.
  The response is identical.
Response (existing fields unchanged) plus, new:
| Field | Meaning |
|---|---|
| `fit_source` | `resume_quick` (FitScorer on the chosen resume's skills + profile experience/education, JD skills from the regex extractor — no LLM) or `neutral` (fit = 50: no resume, or `quick_fit` off) |
| `fit_breakdown` | 8-signal breakdown when `resume_quick`, else `{}` |
| `resume_id`, `resume_label` | Resume the preview used |
| `matched_skills`, `missing_skills` | JD skills present / absent on the resume (word-boundary match, so `JavaScript` ≠ `java`) |
| `must_have_found`, `must_have_missing` | User's must-have keywords found / not found in the JD |
| `excluded_company` | Company matches the user's excluded list |
| `already_saved_application_id` | Existing application with the same `jd_link` (trailing slash ignored) when `warn_duplicates` is on |

### `POST /extension/jobs/save`
Body `{title, company, location, description, source_url, source_site, priority_score, score_confidence}` →
`{success, application_id, open_url}`. Now also applies settings: status = `default_save_status`, and
`selected_resume_id` = `default_resume_id` when one is pinned (otherwise `null` = latest, unchanged behaviour).

### `POST /extension/events` · `POST /extension/promo/dismiss`
Unchanged.

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

## RECRUITER — SOURCED STAGE (2026-11-13)

LinkedIn captures from the recruiter Chrome extension (v0.3.0+) now land in **Sourced**, not the pool.
Recruiters vet them (notes, contact, CV / cover letter), then promote to the pool and optionally a role pipeline.
Unvetted profiles never reach matching, shortlists, Ask-the-pool or dashboards — they live in their own tables.

Base: `/api/v1/recruiter/agencies/{agency_id}/sourced` (ids are public GUIDs like every recruiter route).

| Method | Path | Notes |
|---|---|---|
| POST | `/score-preview` | Body = capture payload + `role_id` (required). Returns `{role_id, role_title, fit_score, tier, reasons, gaps, breakdown, sourced_id, sourced_status, pool_candidate_id}`. **Stores nothing.** Uses the pool matcher (`score_candidate`) unchanged. `tier`: `strong` ≥75, `good` ≥50, else `low`. `404` foreign role. |
| POST | `` | Add/refresh in Sourced. Body = capture payload (+ optional `role_id`). Dedups on canonical LinkedIn URL per agency; a missing field never blanks a stored one; notes/docs/status kept; archived → back to `sourced`. Scores when a role is set. `201 {created, sourced}`. |
| GET | `?status=sourced\|promoted\|archived\|all&role_id=` | List (default `sourced`). |
| GET | `/{sourced_id}` | Detail: scraped about/experiences/education/certifications/languages, notes, `fit_reasons`, `fit_gaps`, `fit_breakdown`, documents. Never returns `page_text`, `storage_uri` or extracted text. |
| PATCH | `/{sourced_id}` | Any of `notes, email, phone, full_name, headline, location, role_id, status`. Changing `role_id` (incl. `null`) re-scores. `status` only `sourced`/`archived`; `400` once promoted. |
| POST | `/{sourced_id}/rescore` | Re-score against the current role using LinkedIn data + CV text + captured page text. |
| DELETE | `/{sourced_id}` | Hard delete (GDPR) incl. stored files. `204`. |
| POST | `/{sourced_id}/documents` | multipart `file` + `kind` (`cv`\|`cover_letter`\|`other`). Same validation as candidate documents (PDF/DOCX/DOC/TXT, 10 MB, magic bytes). A CV's text is extracted and folded into the score immediately. `400` once promoted. |
| GET | `/{sourced_id}/documents/{document_id}/download` | Authenticated stream. |
| DELETE | `/{sourced_id}/documents/{document_id}` | Removes stored object + row; re-scores if it was a CV. |
| POST | `/{sourced_id}/promote` | Body `{role_id?}`. Copies into the pool via the LinkedIn-capture path (dedups on URL), parses the latest CV and merges it (fills blanks, adds skills, appends CV text; LinkedIn history wins), **moves** documents onto the pool candidate (`uploaded_via="sourced"`), and with `role_id` attaches to that role's pipeline at the first stage. `{sourced_id, candidate_id, created, application_id, documents_moved}`. Repeating with a new `role_id` only adds the attachment; repeating with no role → `400`. Meters `cv_ingested` only when a new pool candidate is created. |

**Capture payload** = `LinkedInCaptureRequest` plus `education[] {school, degree, field, start_date, end_date}`,
`certifications[] {name, issuer, issued}`, `languages[]`, `page_text` (visible profile text, capped at 20k chars server-side).

**Role visibility** — the target role column is `role_id`, so restricted seats automatically don't see sourced profiles
targeted at hidden roles (profiles with no role stay visible).

Storage: `rec_sourced_profiles`, `rec_sourced_documents` (Alembic `0037_recruiter_sourced`; also created at boot).
No FK outside `rec_*`. `POST /candidates/capture-linkedin` is unchanged and still works for older extension builds.

---

## ADMIN — CRAWLED JOBS CLEANUP (2026-11-17)

Base: `/api/v1/admin` · admin token (`require_admin`).
Admin UI: **Admin → Job Crawler** (`/admin/crawler`) → "Delete old crawled jobs" card.

Covers both stores filled by background workers:
- **Worker-discovered jobs** — `jobs` (+ `job_sources`) from Celery `poll_provider` and `live_search`. Age = `first_seen_at`. **Kept:** any job referenced by `job_applications.job_id` (incl. soft-deleted) — the auto-apply orchestrator de-dupes by `Job.id`, so deleting those would let a re-poll re-insert the posting and apply twice.
- **Per-user crawler results** — `crawled_jobs` from the APScheduler crawler. Age = `crawled_at`. **Kept:** `is_saved`, `is_dismissed`, or `application_id` set.

| Method | Path | Notes |
|---|---|---|
| GET | `/crawled-jobs/cleanup/preview?older_than_hours=24` | `1 ≤ hours ≤ 2160`. Returns `{older_than_hours, cutoff, discovered_jobs: {total, eligible, protected}, user_crawled_jobs: {total, eligible, protected}}`. `eligible` = will be deleted; `protected` = older than cutoff but kept. |
| POST | `/crawled-jobs/cleanup` | Body `{older_than_hours=24, include_discovered=true, include_user_crawled=true}`. Hard delete in batches of 1000. Returns `{older_than_hours, cutoff, deleted_discovered_jobs, deleted_job_sources, deleted_user_crawled_jobs}`. Writes an `admin.crawled_jobs.cleanup` audit-log row. |

---

## RECRUITER — WEBSITE ENQUIRIES (2026-11-18)

The recruiter website's **Contact us** page (`/contact`, recruiter-frontend) posts here; the operator triages them in
**Admin → Management → Enquiries** (`/admin/enquiries`, user-frontend). Prospects aren't tenants yet, so rows have no
`agency_id` and no FK — an operator-level inbox inside the `rec_*` data wall. Ids are public GUIDs like every recruiter route.

### Public — no auth

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/recruiter/public/enquiries` | Body `{topic, full_name, email, company?, company_type?, team_size?, phone?, message, source_page?, website?}`. `topic` ∈ `sales\|demo\|api\|partnership\|other` (default `sales`); `company_type` ∈ `agency\|tech_company\|platform\|other`; `message` 10–5000 chars; email lower-cased; strings trimmed. `website` is a **honeypot**: if filled the request still answers `202` but nothing is stored. Rate limit **5 per IP per hour** (shared partner limiter; Redis when configured) → `429` + `Retry-After`. Success: `202 {ok: true}` — never echoes the record. The IP is used for limiting only and is **not stored**. |

### Operator — consumer admin token (`require_admin`)

Base: `/api/v1/recruiter/admin/enquiries`

| Method | Path | Notes |
|---|---|---|
| GET | `?status=&topic=&search=&limit=100&offset=0` | Newest first. `search` matches name, email or company (case-insensitive). `limit` ≤ 500. Returns `EnquiryOut[]`: `{id, topic, status, full_name, email, company, company_type, team_size, phone, message, source_page, admin_notes, handled_at, created_at, updated_at}`. |
| GET | `/summary` | `{total, new, by_status: {new, contacted, qualified, closed, spam}, by_topic}`. Drives the sidebar "new" badge (polled every 60 s). |
| PATCH | `/{enquiry_id}` | Any of `status` (`new\|contacted\|qualified\|closed\|spam`), `admin_notes` (≤5000; blank → null). The first move off `new` stamps `handled_at`. `404` unknown id. Non-optimistic in the UI. |
| DELETE | `/{enquiry_id}` | Hard delete — spam or an erasure request from the sender. `204`; `404` if already gone. |

Storage: `rec_enquiries` (Alembic `0038_recruiter_enquiries`; also created at boot via `RECRUITER_TABLES`).
Retention: no automatic purge yet — delete closed/spam enquiries periodically, or add a retention job before launch.

---

## ADMIN — operator console (2026-11-18)

All endpoints require an admin JWT (`require_admin`); non-admins get `403`. Prefix `/api/v1`.
Times are naive UTC ISO strings without a zone (frontend parses them with `parseApiDate`).

### Pagination totals
`GET /admin/users`, `GET /admin/events`, `GET /admin/audit-logs` return the **total row count before
pagination** in the `X-Total-Count` response header (exposed via CORS `expose_headers`). Body shape is
unchanged (a plain list).

### `GET /admin/users`
Query: `page` (≥1), `page_size` (1–100, default 20), `search` (email, full name, or `#id` / `id`),
`plan` (`free|pro|enterprise`), `role` (`admin|user`), `status` (`active|disabled`),
`sort` (`newest` default, `oldest`, `email`, `tokens` = this month's tokens desc, `last_seen`).
Each item (`AdminUserOut`) gains two fields:
```json
{ "full_name": "Ada Lovelace", "last_seen_at": "2026-11-18T09:12:00" }
```
`last_seen_at` = latest refresh-token issue (login or refresh), or `null`.

### Admin mutations are audited
Every admin write now appends an `audit_logs` row (`before`/`after` hold only changed keys, `extra_metadata`
carries a human label such as the email). Actions: `admin.user.update`, `admin.user.feature_toggle`,
`admin.user.activate`, `admin.user.deactivate`, `admin.user.delete`, `admin.feature.bulk_set`,
`admin.quality.spot_check`, `admin.agency.create`, `admin.agency.plan_update`, `admin.agency.approve`,
`admin.agency.status`, `admin.recruiter.create`, `admin.recruiter.update`, `admin.recruiter.reset_password`
(password never logged), `admin.recruiter.delete`. No-op changes are not logged. Recruiter entities are
referenced by plain integer id (`entity_type` `rec_agency` / `rec_recruiter`, no FK — data wall intact).

Guards: an admin cannot remove their own admin role or deactivate their own account → `400`.

### `GET /admin/events/facets` · `GET /admin/audit-logs/facets`
```json
{ "event_names": ["page_view", "..."], "entity_types": ["application"] }
{ "actions": ["admin.user.update", "..."], "entity_types": ["user", "rec_agency"] }
```

### `GET /admin/overview`
Month-to-date KPIs compared with the **same point last month**, a 30-day daily trend, and consumer
"needs attention" items (recruiter items come from `/recruiter/admin/billing/summary` and the enquiries
summary; the UI merges them).
```json
{
  "generated_at": "...",
  "kpis": {
    "users_total": 39, "paid_users": 15,
    "signups": {"current": 12, "previous": 7}, "active_users": {...}, "tokens": {...},
    "ai_calls": {...}, "cost_usd": {"current": 2.0, "previous": 0.6},
    "comparison_note": "Month to date vs the same point last month (18 Oct)"
  },
  "trend": [{"date": "2026-10-20", "signups": 1, "active_users": 9, "tokens": 120000}],
  "attention": {
    "tickets_awaiting_reply": {"count": 3, "urgent": 1, "items": [{"ticket_id": 4, "subject": "...", "priority": "high", "waiting_since": "...", "email": "..."}]},
    "users_near_budget": {"count": 5, "over_budget": 2, "items": [{"user_id": 3, "email": "...", "plan": "free", "tokens_used": 52000, "budget": 50000, "pct": 104}]},
    "past_due_subscriptions": {"count": 2, "items": [{"user_id": 4, "email": "...", "plan": "pro"}]},
    "low_confidence_parses": {"count": 2, "threshold": 0.6, "items": [{"user_id": 7, "email": "...", "confidence": 0.45, "parsed_at": "..."}]}
  }
}
```
"Awaiting reply" = open/in-progress ticket whose latest message is not from an admin.
"Near budget" = this month's ledger tokens ≥ 80% of `token_budget_monthly`.

### `GET /admin/costs?month=YYYY-MM`
Estimated LLM spend priced from `usage_events` (model + tokens per call) using `app/core/llm_pricing.py`.
```json
{
  "month": "2026-11", "is_current_month": true,
  "total": {"tokens": 4095014, "tokens_in": 3100000, "tokens_out": 995014, "calls": 817, "cost_usd": 2.0},
  "previous_month_total_usd": 1.77, "projected_month_end_usd": 3.43,
  "active_users": 37, "cost_per_active_user_usd": 0.0541,
  "unpriced_models": [],
  "by_model":   [{"model": "gpt-4o-mini", "tokens": 0, "tokens_in": 0, "tokens_out": 0, "calls": 0, "cost_usd": 0}],
  "by_feature": [{"feature": "jd_analyze", "...": "same totals"}],
  "top_users":  [{"user_id": 3, "email": "...", "...": "same totals"}],
  "by_day":     [{"date": "2026-11-01", "...": "same totals"}]
}
```
`unpriced_models` lists models charged at the default rate — add them to the price table.
Invalid month → `422`. `projected_month_end_usd` is `null` for past months.

**2026-11-24:** also returns `by_operation` (top 25, `{"operation": "POST /applications/{id}/tailor", ...totals}`)
and `by_kind` (`"llm"` | `"embedding"`). `top_users[].user_id` may be `null` = anonymous preview / system job
(`email: "Anonymous / system"`); `active_users` counts real users only.

### `GET /admin/quality?days=30`
```json
{
  "window_days": 30,
  "hallucination": {
    "evaluated_docs": 40, "flagged_docs": 16, "flag_rate": 0.4, "avg_flags_per_doc": 0.45, "min_confidence": 0.6,
    "by_doc_type": [{"doc_type": "cover_letter", "evaluated": 20, "flagged": 9}],
    "by_claim_type": [{"claim_type": "years_experience", "count": 14}],
    "recent_flagged": [{"application_id": 7, "user_id": 8, "email": "...", "doc_type": "cover_letter", "flag_count": 1, "evaluated_at": "...",
                        "flags": [{"claim": "12+ years", "claim_type": "years_experience", "reason": "...", "confidence": 0.8}]}],
    "last_checked_at": "..."
  },
  "generation": {"documents_generated": 40, "evaluation_coverage": 1.0},
  "parsing": {"parses": 20, "avg_confidence": 0.839, "low_confidence": 2, "threshold": 0.6},
  "outcomes": {"interview": 3}
}
```

### `POST /admin/quality/spot-check`
Body `{ "sample_size": 50, "days": 30 }` (1–200, 1–365). Runs the regex `HallucinationChecker`
(no LLM call) over the latest version of each recent generated document, grounded against the user's parsed
resume + profile, and upserts `application_evaluations` (hallucination fields only). Does not touch the
generation flow.
```json
{ "checked": 40, "flagged": 16, "skipped_no_grounding": 0, "flag_rate": 0.4, "window_days": 30 }
```

### `GET /admin/feature-flags`
New query params: `search` (user email), `enabled` (bool). Response gains:
`summary[].enabled_by_plan` (`{"free": 12, "pro": 30}`), `plan_users` (`{"free": 40, ...}`),
`overrides_total` (rows matching the filters; `overrides` is still capped at 200).

### `POST /admin/feature-flags/bulk`
Body: `{ "feature": "chat", "enabled": true, "plan": "free", "user_ids": null, "dry_run": true }` —
`plan` and/or `user_ids` (≤1000) required (`400` otherwise). Idempotent; only rows that change are written.
```json
{ "feature": "chat", "enabled": true, "matched_users": 40, "changed": 28, "dry_run": true }
```

### `GET /recruiter/admin/billing/summary`
Gains `trials_ending_soon` — agencies (not suspended) whose `trial_ends_at` is within the next 7 days.

---

## AI USAGE METERING + QUOTAS (2026-11-24)

Every LLM and embedding call made through `get_llm_provider()` / `get_embedding_provider()` is metered
centrally (`app/services/usage/metering.py`), not just Coach chat. Attribution comes from the request's
Bearer token (middleware, no DB hit); calls are labelled `METHOD /templated/path`. Recruiter and partner
paths are not metered here (data wall — recruiter metering is Phase 5 step 2). Scheduled crawler runs are
attributed to their user (`crawler.scheduled`). Calls with no user are kept as anonymous/system spend.

`usage_events` gains `kind` (`llm` | `embedding`); `user_id` becomes nullable. Embeddings are summed per
request into one row and priced, but never count toward quotas. Migration `0044_usage_metering_quotas`.

**Allowances** — enforced before every LLM call; admins exempt.
- Monthly: `users.token_budget_monthly` (unchanged).
- Rolling 24h: `users.token_budget_daily`, `NULL` = plan default (Free 20K · Pro 100K · Enterprise 1M).
  Admin reset moves `users.usage_window_reset_at` to now; only later usage counts.

**Refusal** — `429`:
```json
{"detail": {"error": "daily_token_limit_exceeded" | "token_budget_exceeded", "scope": "daily" | "monthly",
            "used": 104200, "budget": 100000, "resets_at": "2026-11-25T09:12:00", "message": "..."}}
```
Where a service swallows the refusal and returns non-AI fallback output (2xx), the response carries
`X-Usage-Limited: daily|monthly` (exposed via CORS) so the UI can still explain the missing AI output.

### `GET /usage/me` (any signed-in user)
```json
{"plan": "pro", "exempt": false, "blocked": null,
 "daily":   {"used": 12000, "limit": 100000, "remaining": 88000, "pct": 12.0, "exhausted": false,
             "resets_at": "2026-11-25T08:01:00", "window_hours": 24, "custom_limit": false, "reset_at": null},
 "monthly": {"used": 210000, "limit": 500000, "remaining": 290000, "pct": 42.0, "exhausted": false,
             "resets_at": "2026-12-01T00:00:00"},
 "packages_used_this_month": 4, "monthly_package_limit": -1}
```
Daily `resets_at`: when exhausted, the time enough old usage ages out to get back under the limit;
otherwise when the oldest counted usage drops off. `pct` can exceed 100.

### `PATCH /admin/users/{id}` — gains `token_budget_daily`
Number (≥0) overrides the plan's 24h limit; explicit `null` returns to the plan default. `AdminUserOut`
gains `token_budget_daily` (effective) and `daily_limit_is_custom`. Audited (`admin.user.update`).

### `POST /admin/users/{id}/usage/reset-daily`
Starts a fresh 24h window. Monthly usage and cost history untouched. Returns the `/usage/me`-shaped quota
status (without plan/package fields). Audited as `admin.user.usage_reset_daily` with before/after.

### `GET /admin/users/{id}/insights?days=30` (7–90)
Read-only, consumer tables only, filtered by `user_id`.
```json
{"user_id": 14, "window_days": 30,
 "quota": {"...": "same as /usage/me quota fields"},
 "spend": {"month_cost_usd": 0.41, "month_tokens": 820000, "month_calls": 311,
           "all_time_cost_usd": 0.52, "all_time_tokens": 1040000, "all_time_calls": 402,
           "last_ai_call_at": "...", "tracked_since_note": "..."},
 "by_operation": [{"operation": "POST /applications/{id}/tailor", "kind": "llm", "calls": 120, "tokens": 400000, "cost_usd": 0.2}],
 "daily_usage":  [{"date": "2026-10-27", "tokens": 0, "cost_usd": 0}],
 "search": {"declared_target_roles": [], "preferred_locations": [], "current_role": null, "headline": null,
            "location": null, "salary_expectation": null,
            "top_titles": [{"label": "AI Engineer", "count": 41}], "role_families": [], "seniority": [],
            "top_companies": [], "sites": [{"label": "LinkedIn", "count": 200}], "extension_sites": [],
            "status_counts": {"applied": 150}, "funnel": {"saved": 269, "applied": 150, "interview": 6, "offer": 0},
            "avg_priority_score": 63.4, "last_application_at": "...", "added_last_7d": 4, "added_last_30d": 20,
            "weekly_added": [{"date": "2026-09-07", "value": 3}]},
 "documents": {"resumes_uploaded": 3, "resumes_parsed": 2, "avg_parse_confidence": 0.88, "tailored_resumes": 140,
               "generated_by_type": {"cover_letter": 30}, "generated_total": 60, "applications_with_documents": 40,
               "chat_threads": 5, "chat_messages_sent": 22, "answer_library_entries": 14, "interview_sessions": 0}}
```
`sites` is derived from each application's `jd_link` host (no link → "No link (pasted JD)");
`extension_sites` from `extension_job_scored` / `extension_job_saved` events. Role families and seniority
are keyword buckets over saved titles — indicative, not a taxonomy.

---

## EMAIL — Resend: transactional, automations, operator mailbox (2026-11-18)

Transport: `EmailProvider` (Resend in prod, console in dev — `EMAIL_PROVIDER`). Every email is written to an
outbox row in the same transaction as the change that caused it and sent after commit
(`EMAIL_JOBS_BACKEND` = sync | inline | celery). Consumer rows: `email_messages` (user_id, cascade).
Recruiter rows: `rec_email_messages` (agency_id, cascade). Operator tables `ops_*` have no FKs into either product.

Categories: `transactional` (always sent) · `notification` · `digest` · `lifecycle` (soft opt-in, weekly cap,
unsubscribe link + RFC 8058 headers when `API_PUBLIC_URL` is set).

### Public / user
| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/webhooks/resend` | svix signature (`RESEND_WEBHOOK_SECRET`) | delivery events → send log + suppression; `email.received` → mailbox. De-duplicated by `svix-id`. 401 on bad signature. |
| GET/PUT | `/email/preferences` | consumer | `{notifications, digests, product_updates}` — all default `true` |
| GET | `/email/unsubscribe/info?token=` | none | `{scope, category, label}` |
| POST | `/email/unsubscribe?token=` | none | turns off that category for that person (consumer or recruiter token) |
| POST | `/auth/forgot-password` | none | `{email}` → always 200 (no enumeration); emails a 60-min single-use link |
| POST | `/auth/reset-password` | none | `{token, password≥8}` → 200, revokes refresh tokens; 400 if expired/used |
| POST | `/recruiter/auth/forgot-password` | none | same, recruiter seats |
| POST | `/recruiter/auth/reset-password` | none | same |
| GET/PUT | `/recruiter/me/email-preferences` | recruiter | `{notifications, digests, product_updates, muted_kinds[], available_kinds[]}` |

### Admin (`/admin/email`, admin only)
| Method | Path | Notes |
|---|---|---|
| GET | `/overview?days=7` | counts by status per product, suppressions, live rules, unread mail, provider, webhook configured |
| GET | `/messages?scope=all|consumer|recruiter&status&template_key&search&page&page_size` | merged in Python from the two logs (never joined) |
| GET | `/messages/{scope}/{id}` | includes rendered `html` / `text` |
| POST | `/messages/{scope}/{id}/retry` | failed / cancelled / bounced only; 400 if address suppressed |
| GET | `/templates` · `/templates/{key}` | system templates (code) merged with admin overrides |
| PUT | `/templates/{key}` | override copy (validated sandboxed Jinja; 422 on syntax error) |
| POST | `/templates` | custom template `{key, product, category, name, subject, body, …}` |
| DELETE | `/templates/{key}` | reset a system template / delete a custom one (409 if an automation uses it) |
| POST | `/templates/preview` | render with sample data |
| POST | `/templates/{key}/test` | send a `[TEST]` copy to `to` or the admin |
| GET | `/events` | trigger catalogue: events + fields, facts per scope, recipients, operators, blocks |
| GET/POST | `/rules` · PATCH/DELETE `/rules/{id}` | automations: `{name, scope, event_key, conditions, template_key, recipient, fixed_to, delay_minutes, cancel_if, cooldown_days, once_per_subject, is_active}` |
| POST | `/rules/{id}/dry-run` | daily triggers only → `{count, sample[]}` |
| GET | `/rules/{id}/stats` | sends by status |
| GET/POST | `/suppressions` · DELETE `/suppressions/{id}` | |
| GET | `/mailbox/summary` | `{support: {address, unread, open}, info: {…}}` |
| GET | `/mailbox/threads?mailbox&status=inbox|all|open|waiting|closed|spam|archived&starred&search&page` | |
| GET/PATCH | `/mailbox/threads/{id}` | GET marks read; PATCH `{status, starred, unread, labels, mailbox}` (spam ⇒ sender suppressed) |
| POST | `/mailbox/threads/{id}/reply` | `{body, cc[], close}` — sent from that mailbox with In-Reply-To/References; mirrored into the linked help-desk ticket |
| POST | `/mailbox/compose` | `{mailbox, to, subject, body, cc[]}` |
| GET | `/mailbox/lookup?email=` | sender context: consumer user + recruiter seat (two separate lookups) |
| GET | `/mailbox/messages/{id}/attachments/{idx}` | download stored attachment |
| GET/POST | `/mailbox/canned` · DELETE `/mailbox/canned/{id}` | saved replies |

Trigger keys — consumer: `user.registered`, `user.password_changed`, `resume.parsed`, `application.created`,
`application.status_changed`, `document.generated`, `support.ticket_created`, `schedule.daily`.
Recruiter: `rec.agency.created`, `rec.agency.status_changed`, `rec.invite.created`, `rec.invite.accepted`,
`rec.password_changed`, `rec.notification.created`, `rec.schedule.daily`.
