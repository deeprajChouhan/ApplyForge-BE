# Recruiter Platform — Architecture

The recruiter platform is an AI talent-matching product for recruiter agencies,
built as a **module inside the existing ApplyForge backend** (`app/recruiter/`).
It shares the app's infrastructure — database engine, config, security helpers,
deployment — but keeps its own data and API namespace so the consumer product is
never affected.

## Design principles

- **One backend, isolated data.** All recruiter tables are `rec_`-prefixed and
  hold **no foreign keys into consumer tables**. Agency candidate pools and
  consumer user data never mix. (This is the pragmatic realisation of the plan's
  "separate apps" intent, chosen to avoid running and maintaining a second
  service.)
- **Consumer product untouched.** Every recruiter route lives under
  `/api/v1/recruiter`, a path existing users never hit. The only place the two
  products meet is the provisioning bridge (below).
- **Tenant isolation is enforced, not assumed.** Each row is scoped by
  `agency_id`; the `get_agency` dependency verifies the caller may act on that
  agency on every agency-scoped request.
- **Runs offline for dev/tests.** Matching uses a real embedding provider when an
  AI key is configured, and a deterministic bag-of-words fallback otherwise, so
  the whole system works on SQLite with no external services.

## Layout

```
app/recruiter/
  __init__.py          module overview
  enums.py             RecruiterSeatRole, RoleStatus, EmploymentType,
                       CandidateSource, ApplicationStage
  models.py            SQLAlchemy models (all rec_-prefixed) + RECRUITER_TABLES
  schemas.py           Pydantic request/response models
  init_db.py           ensure_recruiter_tables() — dev-time create (checkfirst)
  bridge.py            recruiter side of the provisioning bridge
  services/
    embeddings.py      real provider when keyed, deterministic mock otherwise
    parsing.py         CV text extraction + structured fields
    skills.py          skill vocabulary + normalisation
    matching.py        inverted matching — score a candidate against a role
    shortlist.py       persist a ranked matching run
    ingestion.py       bulk-CV → CandidateProfile
    linkedin_capture.py  LinkedIn /in/* scrape → CandidateProfile (dedup on linkedin_url)
    spec_sheet.py      CandidateProfile → agency-branded CV/spec-sheet (PDF + DOCX; anonymisation)
    listing.py         grounded job-listing generation
    market.py          demand/supply, salary, funnel, time-to-fill
    placement.py       candidate → best-fit open roles
    advisory.py        client → likely next hire (benchmark-driven)
  api/
    deps.py            recruiter auth + get_agency (tenant enforcement)
    auth_routes.py     /recruiter/auth: login, refresh, me
    admin_routes.py    /recruiter/admin: agencies + recruiter logins (require_admin)
    routes.py          agency-scoped resources (clients, roles, candidates,
                       shortlist, applications, market)
    router.py          aggregate recruiter_router (mounted under /api/v1/recruiter)

app/services/provisioning/service.py   consumer side of the bridge
app/api/v1/routes/provisioning.py      the single additive /provisioning endpoint
```

## Data model (all `rec_`-prefixed, agency-scoped)

| Entity | Table | Notes |
| --- | --- | --- |
| Agency | `rec_agencies` | the tenant; owns everything below |
| Recruiter | `rec_recruiters` | a seat **with its own login** (password_hash, is_active) |
| Client | `rec_clients` | a hiring company the agency serves |
| Role | `rec_roles` | open position; required/preferred skills, cached embedding |
| CandidateProfile | `rec_candidate_profiles` | agency-owned CRM record, no login; `provisioned_user_id` once converted. Sourced from bulk CV upload OR the recruiter Chrome extension (`source=linkedin`, deduped per agency on `linkedin_url`) |
| CandidateSkill | `rec_candidate_skills` | normalised skill tokens |
| CandidateExperience | `rec_work_experiences` | class renamed to avoid clashing with the consumer `WorkExperience` on the shared Base |
| Shortlist / Entry | `rec_shortlists` / `rec_shortlist_entries` | a saved matching run + ranked entries |
| Application | `rec_applications` | tracking-only pipeline record (stage) |
| SpecSheetTemplate | `rec_spec_sheet_templates` | agency-owned CV/spec-sheet template with per-template branding overrides + `anonymise_by_default` toggle; referenced from `rec_agencies.spec_sheet_template_id` (SET NULL on delete) |

Class names are unique across the shared declarative Base (e.g. the recruiter
"work experience" is `CandidateExperience`) so relationship string resolution is
unambiguous.

## Authentication & authorization

Two principals can act on recruiter data, both verified in `api/deps.py`:

- **Recruiter** — logs in at `/api/v1/recruiter/auth/login`, receives a JWT of
  type `recruiter_access` (sub = recruiter id). Scoped to exactly their own
  agency; `get_agency` returns 403 for any other agency.
- **Platform operator** — a consumer admin JWT (`type=access`, `role=admin`).
  May act across agencies for oversight/management.

Recruiter **logins are provisioned by the operator** from the admin console
(`/api/v1/recruiter/admin/recruiters`, guarded by the consumer `require_admin`).
There is no open recruiter signup. Deactivating a recruiter blocks new logins and
invalidates existing tokens (checked on every request).

Credentials live in `rec_recruiters` (hashed with the app's shared
`hash_password`), **not** in the consumer `users` table — recruiter identities
stay inside the data wall.

## The provisioning bridge (the one additive touchpoint)

Converting a `CandidateProfile` into a real ApplyForge consumer user:

1. Recruiter triggers `POST /recruiter/agencies/{id}/candidates/{cid}/convert`
   with explicit `consent: true`.
2. `bridge.py` mints a short-lived **consent token** and builds the profile
   payload.
3. It calls the **provisioning service** — in-process by default (same
   deployment), or over HTTP to `APPLYFORGE_PROVISIONING_URL` if configured (a
   future split deployment).
4. The consumer-side `provision_user_from_profile` verifies the consent token and
   creates a free `User` + `UserProfile` + `Skill`/`WorkExperience`, granting the
   free feature set.
5. The candidate's `provisioned_user_id` is set. **One-way handoff** —
   re-converting is refused (409) and the recruiter app stops tracking that
   person.

The additive endpoint `POST /api/v1/provisioning/candidate` is **disabled unless
`APPLYFORGE_PROVISIONING_KEY` is set**, and requires that key in the
`X-Provisioning-Key` header. It exists for the split-deployment case; the
integrated deployment never needs it.

## Configuration

All optional — the module runs with defaults:

| Setting | Purpose |
| --- | --- |
| `EMBEDDING_PROVIDER` / `AI_API_KEY` | real embeddings for matching (else deterministic mock) |
| `LLM_PROVIDER` / `AI_API_KEY` | LLM polish for job listings (else deterministic template) |
| `APPLYFORGE_PROVISIONING_URL` | set only if the recruiter platform runs as a separate service |
| `APPLYFORGE_PROVISIONING_KEY` | enables + guards the additive `/provisioning` endpoint |

## API surface (under `/api/v1/recruiter`)

- `auth/login`, `auth/refresh`, `auth/me`
- `admin/agencies` (GET, POST), `admin/recruiters` (GET, POST, PATCH,
  reset-password, DELETE) — operator only
- `agencies/{id}/clients` (GET, POST, GET one), `.../{cid}/next-hire`
- `agencies/{id}/roles` (GET, POST, GET one), `.../{rid}/listing`,
  `.../{rid}/shortlist` (POST, latest)
- `agencies/{id}/candidates` (GET, GET one), `.../ingest`,
  `.../capture-linkedin` (Chrome-extension one-click capture; dedups on
  canonical `linkedin_url` per agency; optional `role_id` attaches at
  `ApplicationStage.sourced`), `.../{cid}/spec-sheet.pdf` +
  `.../{cid}/spec-sheet.docx` (agency-branded CV export; `?anonymise=true`
  strips identity, `?role_id=` adds a fit-panel, `?template_id=` overrides
  the agency's default template; meters as `spec_sheet_exported`),
  `.../{cid}/convert`, `.../{cid}/role-matches`
- `agency/branding` (PATCH; owner-scoped update of `logo_url` /
  `primary_color` / `footer_text` / `spec_sheet_template_id`)
- `agency/spec-sheet-templates` (owner-scoped CRUD: GET list, POST create,
  PATCH one, DELETE one)
- `agencies/{id}/applications` (GET, POST, PATCH stage)
- `agencies/{id}/market`

## Schema management

Production uses Alembic (`alembic/versions/0017_recruiter_platform.py` creates
the `rec_` schema). The startup `ensure_recruiter_tables()` is a **dev
convenience** (`checkfirst=True`) so the module runs on a fresh SQLite DB without
migrations; in production, migrations are the source of truth.

## Tests

`tests/test_recruiter_platform.py` is a pytest suite (SQLite + mock providers)
covering auth/isolation, ingestion + matching, LinkedIn capture (create /
dedup on recapture / URL validation / tenant isolation / role attach),
spec-sheet export (PDF via reportlab + text-extraction via pypdf, DOCX
via python-docx, anonymisation, role-fit panel, tenant isolation, template
CRUD + override), listing, market, placement, advisory, tracking, and the
provisioning bridge. `tests/recruiter_smoke.py` is the equivalent runnable script.

## Companion Chrome extension

The `extension-recruiter/` folder at the repo root is a Manifest V3 Chrome
extension for the LinkedIn capture flow. It is separate from `extension/`,
which is the shipped consumer-side job-clipper. The recruiter extension
authenticates against `/api/v1/recruiter/auth/login`, keeps tokens in
`chrome.storage.local`, and POSTs scraped `/in/*` payloads to the
`capture-linkedin` endpoint above. See `extension-recruiter/README.md`.

---

## Recruiter OS — Phase 1 (2026-09)

`rec_applications` is the **CandidateRole** join entity — same row for stage,
notes, SWOT, and now the role-specific screening block. We deliberately did
not add a parallel `rec_candidate_role` table.

### New columns

- **`rec_roles.country_code`** — ISO-3166 alpha-2. Drives the MarketConfig used
  by the UI (compensation schema, salary display, notice-period presets).
- **`rec_candidate_profiles.market_preferences`** — optional per-country
  compensation preferences JSON on the candidate's global profile; used only
  to pre-populate a fresh screening form. Not authoritative.
- **`rec_applications` screening block** (all nullable, additive):
  `screening_outcome` (suitable/maybe/not_suitable), `screening_completed_at`,
  `screening_completed_by`, `assigned_recruiter_id`, `recruiter_summary`,
  `candidate_motivation`, `internal_notes`, `motivation_categories` (JSON list),
  `expected_compensation` / `current_compensation` (structured JSON),
  `notice_period` (structured JSON), `availability_date`,
  `availability_immediate`, `preferred_work_model`, `preferred_location`,
  `relocation`, `relocation_notes`, `client_visibility` (JSON map).

Migration: `alembic/versions/0031_candidate_role_screening.py`.

### MarketConfig

`app/recruiter/services/market_config.py` — code-level registry (no DB table)
of country presentation rules. Supported: GB, IN, AE, US, CA, AU, SG, DE.
Each entry defines default currency + salary period, salary display format
(`annual` / `lpa` / `monthly`), notice-period presets, compensation schema
(the fields the UI renders), and market terminology (e.g. India uses "CTC").
Format helper `format_compensation(comp, country_code)` renders the value the
way the market expects it.

### Visibility

`services/visibility.py::to_client_view(app_row)` is the **only** serializer
allowed on client-facing endpoints. It strips fields in `NEVER_CLIENT_VISIBLE`
unconditionally (`internal_notes`, `screening_outcome`, `current_compensation`,
`motivation_categories`, `assigned_recruiter_id`, `screening_completed_by`)
regardless of `client_visibility` overrides — the map only opts *out* of
client-visible fields; it can't opt *in* to internal ones. Backend
enforcement, never frontend hiding.

### AI screening copilot

`services/screening.py` gains two additional entry points:

- `improve_summary(rough_notes, cand_name?, role_title?)` — cleans rough
  notes into a client-safe summary. Returns `{summary, unsupported_gaps,
  used_llm, generated_at}`. Never persists; recruiter reviews then PATCHes.
- `extract_structured_notes(rough_notes, country_code?)` — parses rough
  notes into a proposed structured payload (comp, notice, availability,
  motivation, work model). Returns `{...fields, used_llm, generated_at}`
  with each field null when not mentioned. Never invents values.

`draft_screening_questions` (already existed) is the third arm — used for the
"AI Screening Assistant" side panel.

### New endpoints (all `/api/v1/recruiter`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/agencies/{id}/applications/{app_id}/screening` | Full screening block for a candidate on a role. |
| PATCH | `/agencies/{id}/applications/{app_id}/screening` | Partial update; `mark_completed:true` stamps completion + system activity note. Requires the application to be linked to a role. |
| GET | `/agencies/{id}/applications/{app_id}/client-view` | Server-side client-visible serialisation preview. |
| POST | `/agencies/{id}/applications/{app_id}/screening/ai-improve-summary` | AI rewrites rough notes → client-safe summary draft. |
| POST | `/agencies/{id}/applications/{app_id}/screening/ai-extract-notes` | AI extracts structured fields from rough notes. |
| GET | `/market-configs` | All supported country configs. |
| GET | `/market-configs/{country_code}` | One country config. |

### AI grounding rule

Every new AI endpoint returns drafts only. Nothing is persisted until the
recruiter issues a PATCH. Prompts include "return null / UNKNOWN if not
present in evidence" and heuristic fallbacks are always available so the
UI never sees a blank state.

### Tests

`tests/test_recruiter_screening.py` covers screening CRUD, agency isolation,
client-view visibility enforcement (including the "force internal_notes on
via map" attack), market config schema, and the no-role → 400 guard.

---

## Recruiter OS — Phases 2-7 (2026-09)

Migration: `alembic/versions/0032_recruiter_os_phases_2_7.py`
(chains from `0031`, additive, no existing table touched).

### New tables (all `rec_`-prefixed, agency-scoped, no consumer FKs)

**Phase 2**
- `rec_candidate_consents` — per-role consent (`pending|confirmed|declined|expired`) with method/evidence/expires_at, `captured_by` (recruiter id, no FK).
- `rec_client_submissions` — client-facing candidate submission, **snapshotted at submit** (compensation/notice/availability/motivation frozen on the row). Feeds Phase 3-5 downstream.

**Phase 3**
- `rec_submission_feedback` — structured decision + reasons taxonomy on a submission.
- `rec_client_sla_configs` — per-client feedback SLA (hours) with notify_recruiter flag; default 48h in code.

**Phase 4**
- `rec_interviews` — interview slot bound to `application_id` (survives submission re-issue). Fields cover stage/type/interviewers/proposed_times/confirmed_time/status.
- `rec_interview_feedback` — 1-5 rubric across technical/communication/role_understanding/domain_knowledge/leadership/culture_alignment + decision + comment.

**Phase 5**
- `rec_offers` — draft/sent/negotiating/accepted/declined/withdrawn; base_compensation/bonus/equity/allowances as JSON.
- `rec_offer_negotiations` — append-only history rows.
- `rec_placements` — closed-hire record with `fee_percent`, computed `fee_amount`, `guarantee_ends_at`. Auto-seeds 4 check-ins on create.
- `rec_post_placement_checkins` — day-offset 7/30/60/90 with outcome + completed_at.

**Phase 6**
- `rec_recruiter_tasks` — next-action items with priority + optional AI-generated flag; loose parent references (no FK) so tasks survive parent deletion.
- `rec_notifications` — in-app persistent notification store, grouped by kind + parent.

**Phase 7**
- `rec_ai_insight_cache` — cache for role_health / client_intel / feedback_pattern insights.

### New services

- `services/submissions.py` — readiness checklist, screening-into-submission snapshot, AI submission draft (grounded, refuses to invent facts).
- `services/collaboration.py` — SLA helpers, candidate comparison, feedback pattern rollups.
- `services/interviews.py` — AI interview brief (heuristic fallback + LLM polish).
- `services/offers.py` — placement fee calc, guarantee end-date, offer talking-point AI helper.
- `services/productivity.py` — daily brief (offers expiring / feedback overdue / interviews soon / consent missing / guarantee ending).
- `services/intelligence.py` — Ask-the-pool with LLM query parsing + deterministic scoring, role quality check, role health, client intelligence.

### New endpoints (agency-scoped, `require_unlocked_agency` for writes)

Phase 2:
- `POST/GET/PATCH /agencies/{id}/consents[/{cid}]`
- `GET /agencies/{id}/applications/{app_id}/readiness`
- `POST/GET/GET one/PATCH /agencies/{id}/submissions[/{sub_id}]`
- `POST /agencies/{id}/submissions/{sub_id}/ai-draft`

Phase 3:
- `POST/GET /agencies/{id}/submissions/{sub_id}/feedback`
- `GET/PUT /agencies/{id}/clients/{cid}/sla`
- `GET /agencies/{id}/clients/{cid}/sla-summary`
- `POST /agencies/{id}/candidate-comparison`

Phase 4:
- `POST/GET/GET one/PATCH /agencies/{id}/interviews[/{iid}]`
- `POST/GET /agencies/{id}/interviews/{iid}/feedback`
- `GET /agencies/{id}/interviews/{iid}/brief`

Phase 5:
- `POST/GET/PATCH /agencies/{id}/offers[/{oid}]`
- `POST/GET /agencies/{id}/offers/{oid}/negotiations`
- `POST/GET/PATCH /agencies/{id}/placements[/{pid}]`
- `GET/PATCH /agencies/{id}/placements/{pid}/checkins[/{cid}]`

Phase 6:
- `POST/GET/PATCH /agencies/{id}/tasks[/{tid}]`
- `GET /agencies/{id}/notifications` (+ `POST .../mark`)
- `GET /agencies/{id}/daily-brief`

Phase 7:
- `POST /agencies/{id}/intelligence/ask-pool`
- `GET /agencies/{id}/intelligence/roles/{rid}/health`
- `GET /agencies/{id}/intelligence/roles/{rid}/quality-check`
- `GET /agencies/{id}/intelligence/clients/{cid}/intelligence`

### AI grounding rules (unchanged across phases)

Every AI endpoint returns drafts only — nothing writes back to the domain
without an explicit recruiter action. Every prompt includes
"return UNKNOWN / null if not present in evidence"; heuristic fallbacks are
always available; agency-scoped data only. `intelligence.py` never calls
any candidate "best" — comparisons and health checks are described as
observations, not decisions.

### Frontend screens (recruiter-frontend)

- `/roles/[id]/candidates/[appId]` — Phase 1 Candidate Role Detail with tabs, market-driven forms, AI copilot.
- `/submissions/[id]` — Phase 2 submission detail with AI draft, feedback capture.
- `/roles/[id]/compare` — Phase 3 candidate comparison.
- `/clients/[id]/sla` — Phase 3 SLA dashboard.
- `/interviews/[id]` — Phase 4 interview with AI brief + feedback.
- `/offers/[id]` — Phase 5 offer with negotiation history.
- `/placements/[id]` — Phase 5 placement with check-ins.
- `/daily-brief` — Phase 6 daily brief (Today).
- `/tasks` — Phase 6 tasks board.
- `/intelligence` — Phase 7 Ask-the-Pool + role/client intelligence.

Sidebar now includes Daily brief / Tasks / Intelligence entries.

### Tests

- `tests/test_recruiter_screening.py` — Phase 1 (Phase 1's dedicated suite).
- `tests/test_recruiter_os_all_phases.py` — smoke tests per phase + cross-phase agency isolation.

Run inside the backend container:
```
pytest tests/test_recruiter_screening.py tests/test_recruiter_os_all_phases.py -q
```

## Public ids (no DB ints over the wire)

Every recruiter router uses `route_class=PublicIdRoute` (`app/recruiter/ids.py`).
At the HTTP boundary it translates ids so handlers/services/schemas stay int-based:

- Path + query params named `id`, `*_id`, `*_ids` (plus `ids`, `skipped_existing`,
  `*_by` actor fields) must be GUIDs. A raw int in the URL returns 404.
- JSON request bodies: GUID strings under those keys are decoded to ints (ints are
  still tolerated in bodies for older clients).
- JSON responses: every int under those keys, at any depth, is encoded to a GUID.

GUIDs are a keyed 128-bit Feistel permutation of the int (key: `RECRUITER_ID_KEY`,
else derived from `SECRET_KEY`). They're stable, non-sequential and tamper-checked,
and need no DB column. **Never rotate the key**: it changes every recruiter URL.
New id-bearing fields must follow the naming rule, or be added to `EXTRA_ID_KEYS`.

## Screening: lock, gap-filling, auto-fill

- `PATCH .../applications/{id}/screening` on a completed screening only accepts
  values for fields that are still empty (409 otherwise). `reopen: true` unlocks.
  Both paths write a system note to the activity log.
- When `recruiter_summary`/`internal_notes` are saved, facts stated in the text
  (salary, "competitive" asks, notice, last working day / early release, work
  model, relocation) are lifted into **empty** fields
  (`services/screening_extract.py` deterministic parser, merged with the LLM
  extractor). It never overwrites recruiter-entered values and never invents numbers.
- `POST .../screening/autofill` previews (`apply=false`) or applies proposals.
  Conflicting values are only written when listed in `overwrite`.
- Expected comp supports `basis: "competitive"` with an estimate
  (`minimum`/`maximum`/`target`, `estimate_source` market|recruiter|uplift,
  `uplift_pct`). Readiness accepts a competitive ask once it has an estimate.

## Fit score

`Application.fit_score` is a cache of the latest ranking. `generate_shortlist`
rewrites it for every application on the role, and the pipeline/screening reads
reconcile it with the latest shortlist (`refresh_role_fit_scores`).
