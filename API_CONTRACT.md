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
