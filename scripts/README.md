# Backend scripts

Operational scripts for the ApplyForge backend. Run them from the `backend/`
directory (or from `/app` inside the deployed container). They read
`DATABASE_URL` from the environment, so they act on whatever database that points
at — a local SQLite/MySQL in dev, or prod MySQL inside the deployed container.

## Recruiter platform demo — `seed_recruiter_demo.py`

Seeds a self-contained demo tenant for the recruiter platform: an agency
(**Northwind Talent**, Pro plan, all features unlocked), three recruiter logins,
three clients, six open roles, ~20 candidates with skills and experience, a
14-item tracking pipeline (including two placements, so time-to-fill is
populated), and usage history. Everything is scoped to the `northwind-demo`
agency slug, so it is isolated from real data.

### Logins

All passwords: `DemoPass123!`

| Role      | Name          | Email                  |
|-----------|---------------|------------------------|
| owner     | Nadia Okafor  | `owner@northwind.demo` |
| recruiter | Alex Rivera   | `alex@northwind.demo`  |
| recruiter | Sam Chen      | `sam@northwind.demo`   |

### Run locally (dev)

```bash
cd backend
python scripts/seed_recruiter_demo.py                 # create (skips if it exists)
python scripts/seed_recruiter_demo.py --reset         # wipe the demo tenant + rebuild
python scripts/seed_recruiter_demo.py --create-tables # also create rec_ tables on a fresh DB
```

### Run on the hosted platform (Dokploy)

1. **Redeploy the backend first** so the image contains this script and the
   latest migrations. On boot the container runs `alembic upgrade head`, which
   creates any missing tables/columns — no manual migration step needed.
2. **Open a shell in the backend container** — Dokploy → backend app →
   *Terminal / Console*. (Or over SSH on the host: `docker ps` to find the
   container, then `docker exec -it <backend-container> sh`.)
3. **Run the seed** (working directory is already `/app`):

   ```bash
   python scripts/seed_recruiter_demo.py
   ```

   Use `--reset` to rebuild cleanly. You do **not** need `--create-tables` — the
   startup migrations already created the tables.

Notes:
- Safe and idempotent: scoped to the `northwind-demo` slug; re-running without
  `--reset` does nothing. It never touches real tenants or consumer data.
- Candidates/roles are embedded with whatever AI provider is configured. With an
  OpenAI key set, matching is fully semantic; without one, a deterministic
  offline fallback still ranks sensibly.
- After seeding, log in at `recruiter.applyforge.pro` as `owner@northwind.demo`.
- Cleanup when done: `--reset` removes the tenant, or suspend it from the admin
  console (Agencies → Suspend).

### Suggested demo flow

1. Log in as the owner → **Pool**: browse ~20 candidates with skills.
2. **Roles** → *Account Executive* → **Generate shortlist** (ranked fits + reasons).
3. **Market** → demand vs supply, skill shortages (`sem`, `valuation`), salary
   bands, pipeline funnel.
4. **Clients** → *Meridian SaaS* → **Next hire** (AI advisory).
5. **Team & plan** (owner) → seats, usage, invites, billing.
