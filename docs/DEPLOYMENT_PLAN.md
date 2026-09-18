# Deployment & app-integration plan

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

1. Create a Neon project and **confirm PostGIS is available on the plan**
   (`CREATE EXTENSION postgis`); Supabase is a drop-in fallback (PostGIS
   preinstalled).
2. Put the connection string in the Vercel project env as `DATABASE_URL`
   (Neon's pooled `-pooler` host is fine now that prepared statements are
   disabled).
3. Apply the schema once, from a machine or CI with `DATABASE_URL` set:
   `cd backend && alembic upgrade head`.
4. Leave it empty for the MVP — the country-wide demo fallback covers India
   (verified 802 cells / 0.11 s). Seed Delhi NCR later if desired.

## Phase 3 — Frontend on Vercel

1. New Vercel project, root `frontend/`, preset Vite, build `npm run build`,
   output `dist`.
2. Env `VITE_API_BASE_URL=https://<backend>.vercel.app` (inlined at build
   time — set it before deploying).
3. Single page, no router → no SPA fallback rewrite needed.

## Phase 4 — Flutter app → backend

The adapter already exists (`GridApiPollutionDataProvider`, app-side mapping
of `/api/v1/...`). Only configuration remains:

1. `flutter build apk --release --dart-define=POLLUTION_API_BASE_URL=https://<backend>.vercel.app`
2. HTTPS → no Android cleartext exception; `INTERNET` permission is already
   present. Debug builds still use the scenario simulator; unset → dummy data.
3. Re-check against live data: the adapter joins `/weather` + `/grid/current`
   on `h3_cell` to locate a point, requests forecasts hourly (6 h cap), and
   derives AQI from PM2.5.

## Phase 5 — (Optional) real data + scheduling

- GitHub Actions cron with the `DATABASE_URL` secret running
  `python -m app.pipeline.run` (DEMO_MODE=true for deterministic, or set
  `OPENAQ_API_KEY` for live data). Avoids serverless time limits.
- Only if Vercel Cron is required: add a secret-protected pipeline endpoint
  and a `crons` entry, accepting the duration risk.

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
