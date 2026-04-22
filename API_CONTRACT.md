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

## Common Error Responses

- `400` Bad Request (validation/business rule)
- `401` Unauthorized (missing/invalid token, invalid refresh token)
- `404` Not Found (resource not found or not owned by current user)
- `422` Validation error (Pydantic/FastAPI request validation)
