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
| CandidateProfile | `rec_candidate_profiles` | agency-owned CRM record, no login; `provisioned_user_id` once converted |
| CandidateSkill | `rec_candidate_skills` | normalised skill tokens |
| CandidateExperience | `rec_work_experiences` | class renamed to avoid clashing with the consumer `WorkExperience` on the shared Base |
| Shortlist / Entry | `rec_shortlists` / `rec_shortlist_entries` | a saved matching run + ranked entries |
| Application | `rec_applications` | tracking-only pipeline record (stage) |

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
- `agencies/{id}/candidates` (GET, GET one), `.../ingest`, `.../{cid}/convert`,
  `.../{cid}/role-matches`
- `agencies/{id}/applications` (GET, POST, PATCH stage)
- `agencies/{id}/market`

## Schema management

Production uses Alembic (`alembic/versions/0017_recruiter_platform.py` creates
the `rec_` schema). The startup `ensure_recruiter_tables()` is a **dev
convenience** (`checkfirst=True`) so the module runs on a fresh SQLite DB without
migrations; in production, migrations are the source of truth.

## Tests

`tests/test_recruiter_platform.py` is a pytest suite (SQLite + mock providers)
covering auth/isolation, ingestion + matching, listing, market, placement,
advisory, tracking, and the provisioning bridge. `tests/recruiter_smoke.py` is
the equivalent runnable script.
