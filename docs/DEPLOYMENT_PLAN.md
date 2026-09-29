# Deployment & app-integration plan

> **Historical implementation and rehearsal record (2026-09-18).** Some
> findings and setup notes below describe the code as it existed during that
> rehearsal; do not use them as current deployment steps. Follow
> [`GO_LIVE.md`](GO_LIVE.md), which targets the current `main` branch and
> includes the deployed database, CORS, photo-storage, and pipeline settings.

Goal: host the **web MVP** (frontend + backend) on Vercel and point the
**Flutter partner app** at the deployed backend, with the minimum necessary
code change.

## Verified findings (run locally, 2026-09-18)

Started the existing compose stack (`docker compose up -d --build`) and
probed it directly:

| Check | Result |
|---|---|
| `GET /health` | `200 {"status":"ok"}` |
| `GET /health/ready` | `200`, `database ok`, `postgis 3.4.3` |
| `GET /docs` | `200` (Swagger UI) |
| `GET /api/v1/grid/current` | `200`, 4954 persisted cells |
| `GET /api/v1/grid/forecast?minutes=60` | `200`, 2344 rows |
| `GET /api/v1/weather` | `200`, 4954 rows |
| `GET /api/v1/alerts` | `200` |
| `GET /api/v1/sensors` | `200`, 5 |
| `GET /api/v1/cells/{h3_cell}` | `200` (with the correct `resolution`) |
| CORS preflight from `http://localhost:5173` | `200` + `access-control-allow-origin` |
| `python -m app.pipeline.run` (DEMO_MODE=true) | all 5 stages `[OK]`: 4954 cells, 38374 forecasts, 666 alerts |
| Country-tier demo fallback (`res=3`, all-India bbox) | **802 cells in 0.11 s**, `is_demo=true` |

**The backend already serves API requests** — no serving functionality is
missing. It also works against an **empty database**: every endpoint falls
back to `app/services/demo_data.py` (`is_demo: true`), and that country-wide
synthetic field is what the web MVP renders. So the web MVP can ship with
**zero pipeline data**; the pipeline is only needed for the real Delhi-NCR
hotspot.

Constraints found in code/config:

- Schema requires **PostGIS**: `sensor_reading.geom` / `weather_reading.geom`
  are `Geography(POINT)`, and migration `0001` runs
  `CREATE EXTENSION IF NOT EXISTS postgis`.
- `Settings` builds the DB URL **only** from `POSTGRES_USER/PASSWORD/DB/HOST/PORT`
  and requires the first three (no default) — no single-URL support, and no
  SSL/pooler handling for a managed Postgres.
- Migrations are run by Alembic from `Settings.database_url`.
- The pipeline is **CLI-only** (`python -m app.pipeline.run`); there is no
  scheduler and no HTTP trigger.
- CORS defaults to `http://localhost:5173`.
- Frontend reads `VITE_API_BASE_URL` at build time (`frontend/src/lib/api.ts`).

## Target architecture

```
Vercel project A  (root: frontend/)  static Vite build, VITE_API_BASE_URL
Vercel project B  (root: backend/)   FastAPI "backend framework" preset
Neon / Vercel Postgres (PostGIS)     alembic schema; empty = country-wide demo fallback
Flutter app                          --dart-define=POLLUTION_API_BASE_URL=https://<api>
(optional) GitHub Actions cron       python -m app.pipeline.run against Neon
```

Vercel now has a first-class **FastAPI framework preset**: it looks for an
`app` instance at `app.py`/`index.py`/`server.py`/`main.py`/`wsgi.py`/`asgi.py`
(or the same names inside `src/` or `app/`) and routes **all** requests to it.
Our `backend/app/main.py` already matches (`app/main.py`, with the project
root set to `backend`), so **no `api/index.py` or catch-all rewrite is
needed** — and would in fact be ignored once the preset is detected. The
entrypoint is declared explicitly with `[tool.vercel]` to avoid relying on
detection.

## Phase 1 — Backend Vercel readiness (this change)

Files touched:

1. `backend/pyproject.toml` — declare the FastAPI entrypoint:
   ```toml
   [tool.vercel]
   entrypoint = "app.main:app"
   ```
   (Vercel installs Python deps from this file; `requirements.lock` is not
   auto-detected. Keep a `requirements.txt` as a documented fallback only if
   the pyproject install proves unreliable.)
2. `backend/vercel.json` — the only Vercel project config needed:
   ```json
   {
     "$schema": "https://openapi.vercel.sh/vercel.json",
     "functions": { "app/main.py": { "maxDuration": 60 } }
   }
   ```
3. `backend/app/core/config.py` — accept a single managed connection URL:
   - `DATABASE_URL` (`database_url_override`) takes precedence when set;
   - `POSTGRES_USER/PASSWORD/DB` become optional (so the app can boot
     without DB config and only fail when the DB is actually touched);
   - `database_url` returns `make_url(DATABASE_URL)` when set, else builds
     from the parts and raises a clear error if any are missing.
4. `backend/app/db/session.py` — managed-Postgres connect args:
   - when `DATABASE_URL` is used, default `sslmode` to `require` (unless the
     URL already specifies one);
   - pass `prepare_threshold=None` so a transaction-mode pooler (Neon's
     `-pooler` host / pgbouncer) doesn't break psycopg3 server-side prepared
     statements.
5. `backend/app/api/routes/health.py` — `/health/ready` returns **503** (not
   a 500) when database configuration is missing/incomplete, so an
   unconfigured deploy reports "not ready" cleanly.
6. CORS — no code change; set `CORS_ORIGINS` to the deployed frontend origin.

Deferred from Phase 1 (deliberately):

- An HTTP pipeline trigger for Vercel Cron — the run is ~30–60 s and would
  exceed Hobby function budgets; Phase 5 uses GitHub Actions instead.
- Auth/rate limiting — the API is intentionally open (`GET` only) for the MVP.

## Phase 2 — Database (Neon, or Vercel Postgres)

Requires your cloud account; the steps below were rehearsed locally against
a **fresh, empty** database with a managed-style `DATABASE_URL` (see
"Phase 2 rehearsal" below).

1. Create a Neon project and **confirm PostGIS is available on the plan**
   (Neon supports it; Supabase is a drop-in fallback with PostGIS
   preinstalled). Alembic's `0001` migration runs
   `CREATE EXTENSION IF NOT EXISTS postgis`, so the connecting role needs
   permission to create extensions.
2. Put the connection string in the Vercel project env as `DATABASE_URL`.
   Use Neon's **pooled** host for the serverless function (prepared
   statements are already disabled in Phase 1). Keep a direct (non-pooled)
   URL for running migrations. Example:
   `postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/db?sslmode=require`
3. Apply the schema once, from your machine or CI, with `DATABASE_URL` set
   (the app's own config is the single source of truth — `alembic.ini`'s URL
   is ignored):
   ```bash
   cd backend
   DATABASE_URL="postgresql://...direct..." alembic upgrade head
   ```
4. Leave the schema empty for the MVP — the country-wide demo fallback covers
   India (verified 802 cells / 0.11 s) and needs no data. Seed Delhi NCR later
   if desired (`python -m app.pipeline.run`, see Phase 5).

### Phase 2 rehearsal (run locally, 2026-09-18)

- Created a fresh `pollution_fresh` database and ran
  `alembic upgrade head` with `DATABASE_URL=postgresql://…?sslmode=disable`:
  all three migrations applied; 7 tables present
  (`alembic_version`, `sensor_reading`, `weather_reading`, `grid_state`,
  `forecast`, `alert`, plus PostGIS's `spatial_ref_sys`); PostGIS 3.4.3.
- API against that empty schema: `/health/ready` 200; `/api/v1/grid/current`
  (country tier) 802 cells `is_demo=true`; `/api/v1/alerts` 1 `is_demo=true`
  — i.e. the v1 deploy is fully functional with no data.
- **Found and fixed:** `alembic` passed the URL through a `ConfigParser`,
  so a percent-encoded password (`pa%40ss`) failed with
  `ValueError: invalid interpolation syntax` before connecting. `alembic/env.py`
  now escapes `%`; re-verified that `alembic upgrade head` succeeds with such
  a password, with a regression test in `tests/test_migrations_offline.py`.

## Phase 3 — Frontend on Vercel

1. New Vercel project, **root directory `frontend/`** (this is a monorepo —
   leaving the root at the repo root finds no `package.json` and fails).
   `frontend/vercel.json` pins the rest explicitly:
   framework `vite`, `npm ci`, `npm run build`, output `dist`.
2. Env `VITE_API_BASE_URL=https://<backend>.vercel.app` (Vite inlines it at
   **build** time — set it before deploying; changing it needs a redeploy).
3. Add the frontend origin to the backend's `CORS_ORIGINS`
   (comma-separated). No SPA fallback rewrite is needed — the app is a single
   page with no router.

### Phase 3 rehearsal (run locally, 2026-09-18)

- `npm ci` — clean lockfile install, 0 vulnerabilities.
- `npm run lint` (`oxlint`) — 0 errors, 1 pre-existing warning
  (spread in a hook's dependency array, `hooks/useApiResource.ts`).
- `VITE_API_BASE_URL=https://air-health-api.vercel.app npm run build` —
  `tsc -b` type-check + `vite build` succeed; the URL is present in the
  emitted `dist/assets/*.js`, confirming it is inlined.
- Output: `dist/` with `index.html`, hashed `assets/*.js|css`, the MapLibre
  worker, and `data/*.geojson`/`data/india_locations.json`. Main JS ~1.45 MB
  (~420 KB gzip); the >500 KB chunk-size message is advisory only.

## Phase 4 — Flutter app → backend

The adapter already exists (`GridApiPollutionDataProvider`, app-side mapping
of `/api/v1/...`) — no code change is needed, only configuration. The base
URL is the API **origin**; the client appends `/api/v1/...` itself.

1. Run against the deployed API (debug):
   ```bash
   flutter run --dart-define=POLLUTION_API_BASE_URL=https://<backend>.vercel.app
   ```
2. Release build:
   ```bash
   flutter build apk --release \
     --dart-define=POLLUTION_API_BASE_URL=https://<backend>.vercel.app
   ```
3. HTTPS → no Android cleartext exception; `INTERNET` permission is already
   present. Debug builds still use the scenario simulator; if the define is
   omitted the app falls back to the bundled dummy data. CORS is irrelevant
   to the native app (no browser origin).
4. Re-check against live data: the adapter joins `/weather` (coordinates)
   with `/grid/current` (PM2.5) on `h3_cell`, requests hourly
   `/grid/forecast` up to the API's **6 h cap** (the app's 12 h horizon
   yields ~6 points), derives AQI from PM2.5, and takes events from
   `/alerts` for the user's cell.

### Phase 4 contract check (run locally, 2026-09-18)

Replayed the adapter's exact requests against the real backend for
Bhubaneswar (20.2961, 85.8245) at `resolution=8`, a ±0.06° box:

- `grid/current`, `weather` and `grid/forecast?minutes=60` each returned the
  same **203 cells** (all `is_demo`, since no rows are persisted outside
  Delhi NCR).
- The `h3_cell` join produced 203 cells with both PM2.5 and coordinates; the
  nearest cell was **0.1 km** away (pm25 47.5) and had a +60 min forecast
  (47.1).

This confirms the adapter's data path against the real API, not just its
in-memory fake. The only unverified piece is the Dart build/run itself —
there is no Flutter/Dart SDK in this environment.

## Phase 5 — Scheduled real data (optional)

The web MVP works without this (country-wide demo fallback). This adds the
real, persisted Delhi-NCR pipeline data on a schedule.

Implemented: `.github/workflows/pipeline.yml` runs hourly (and on manual
dispatch):

1. `pip install -r backend/requirements.lock`
2. `alembic upgrade head`
3. `python -m app.pipeline.run`

GitHub Actions rather than Vercel Cron on purpose: a pipeline run is
~30–60 s and would exceed Hobby function budgets, and Vercel Cron only
invokes HTTP endpoints (the pipeline is CLI-only).

Configure in the GitHub repo (Settings → Secrets and variables → Actions):

| Kind | Name | Value |
|---|---|---|
| Secret | `DATABASE_URL` | the **direct** (non-pooler) Neon string |
| Variable | `PIPELINE_DEMO_MODE` | `true` (default) deterministic, or `false` for live |
| Secret | `OPENAQ_API_KEY` | only when `PIPELINE_DEMO_MODE=false` |

Notes:

- Use the **direct** URL: Alembic builds its own engine (not via
  `app/db/session.py`), so it doesn't get the pooler-safe
  `prepare_threshold=None` — the pooled `-pooler` host can break its
  prepared statements.
- Scheduled workflows only run on the default branch (`main` after the PR)
  and pause after ~60 days of repo inactivity.
- `workflow_dispatch` lets you run it on demand with the "Run workflow"
  button (and, with `DEMO_MODE=false`, produce a live OpenAQ/Open-Meteo run).
- Vercel Cron remains an option only if a secret-protected pipeline HTTP
  endpoint is added; not implemented.

## Risks / decisions

- **PostGIS availability** on the chosen Postgres plan — the one hard
  external dependency; verify first.
- **Pooler + psycopg3** — handled by `prepare_threshold=None` (Phase 1).
- **Cold starts** — FastAPI + h3/psycopg; expect ~1–3 s.
- **Open API** — consider rate limiting if abuse matters.
- **Fallback host** — if Vercel's Python runtime fights the app, the existing
  `backend/Dockerfile` deploys cleanly to Render/Railway/Fly; keep only the
  frontend on Vercel.

## Verification per phase

- Backend deployed: `/health/ready` 200; `/api/v1/grid/current?resolution=3&<India bbox>`
  returns 802 `is_demo:true` cells; `/docs` loads.
- Database: `SELECT PostGIS_Lib_Version();`; the five tables exist.
- Frontend: India renders with hexes; no CORS errors in the console.
- App: current AQI loads from the backend; the Delhi hotspot alerts after seeding.

## Local dev (unchanged)

`docker compose up --build`, then `docker compose exec api python -m app.pipeline.run`.
`DATABASE_URL`, when set, takes precedence over the `POSTGRES_*` parts.
