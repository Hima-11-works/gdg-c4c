# Go-live checklist

Deploy the web MVP (frontend + backend) on Vercel and point the Flutter app
at it. Assumes the deployment work is merged to `main` first (Step 0).

Everything here is already implemented in the repo — this is configuration,
not code. Background and rationale: [`DEPLOYMENT_PLAN.md`](DEPLOYMENT_PLAN.md).

## Prerequisites

- A GitHub account (the repo is already there).
- Docker Desktop installed and running (only for Step 2).
- Flutter SDK installed (only for Step 7).
- Accounts: [neon.tech](https://neon.tech) and
  [vercel.com](https://vercel.com) — both free, sign in with GitHub.

---

## Step 0 — Merge the deployment branch to `main`

Vercel and GitHub Actions use `main` (the default branch). The deployment
code (`backend/vercel.json`, `DATABASE_URL` support, the Alembic `%` fix,
`frontend/vercel.json`, `.github/workflows/pipeline.yml`) lives on
`flutter-completion`.

1. On github.com, open the repo → **Compare & pull request**:
   `flutter-completion` → `main`.
2. Merge it.
3. Optional: locally, `git checkout main && git pull` and confirm
   `backend/vercel.json` and `.github/workflows/pipeline.yml` exist on `main`.

> If you'd rather not merge, set each Vercel project's **Production Branch**
> to `flutter-completion` (Settings → Git) instead. Scheduled GitHub Actions
> only run on `main`, so the optional Step 8 needs the merge.

---

## Step 1 — Create the Neon database

1. Go to https://neon.tech → sign in with GitHub → **Create project**.
   Name it `air-health`, pick the nearest region; defaults are fine.
2. Click **Connect**. Copy the connection string twice (toggle the
   **Pooled connection** switch):
   - **Pooled ON** (host contains `-pooler`) → for Vercel.
   - **Pooled OFF** (direct host) → for migrations (Step 2) and the
     scheduler (Step 8).

   It looks like
   `postgresql://neondb_owner:AbC123@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`.
   Keep the `?sslmode=require`.

> Free tier sleeps when idle and wakes on the first request.

---

## Step 2 — Create the tables (migrations)

From a Windows PowerShell terminal, using Docker (no local Python needed):

```powershell
cd C:\Users\KIIT\Documents\GitHub\gdg-c4c\backend
docker build -t air-health-backend .
docker run --rm -e 'DATABASE_URL=<DIRECT neon url>' air-health-backend alembic upgrade head
```

- Use the **direct** (non-pooler) URL, in **single quotes**.
- Expect three `Running upgrade ...` lines, ending at
  `0003_forecast_hours_float`.

Verify in the Neon dashboard → **SQL Editor**:
```sql
SELECT PostGIS_Lib_Version();
SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';
```
You should see a PostGIS version and the tables `sensor_reading`,
`weather_reading`, `grid_state`, `forecast`, `alert`.

Leave the data empty — the API serves a country-wide demo field.

---

## Step 3 — Deploy the backend on Vercel

1. https://vercel.com → **Add New… → Project** → import the repo.
2. **Root Directory** → **Edit** → **`backend`**. (Required — the repo root
   has no `package.json`.)
3. **Framework Preset**: leave detected (the FastAPI entrypoint comes from
   `backend/pyproject.toml`).
4. **Environment Variables**:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | **pooled** Neon string |
   | `ENVIRONMENT` | `production` |
   | `LOG_LEVEL` | `INFO` |

5. **Deploy**. Copy the URL, e.g. `https://air-health-api.vercel.app`.
6. Verify in a browser:
   - `/health` → `{"status":"ok",...}`
   - `/health/ready` → `"database":"ok"` + a PostGIS version
   - `/docs` → Swagger UI
   - `/api/v1/grid/current?resolution=3&min_lat=6.5&min_lon=68&max_lat=37.5&max_lon=97.5`
     → large JSON with `"is_demo":true`, ~800 items

If `/health/ready` is `503`, the `DATABASE_URL` is wrong — fix it and
**Redeploy**.

---

## Step 4 — Deploy the frontend on Vercel

1. **Add New… → Project** → import the **same** repo again.
2. **Root Directory** → **`frontend`**.
3. **Environment Variables**:

   | Name | Value |
   |---|---|
   | `VITE_API_BASE_URL` | backend URL from Step 3 (origin only, no `/api/v1`, no trailing slash) |

4. **Deploy**. Copy the URL, e.g. `https://air-health.vercel.app`.

> `VITE_API_BASE_URL` is inlined at build time — changing it requires a
> **Redeploy**.

---

## Step 5 — Allow the browser to call the API (CORS)

1. Vercel → **backend** project → Settings → Environment Variables.
2. Add **`CORS_ORIGINS`** = the Step 4 frontend URL
   (comma-separate extra origins).
3. **Deployments → ⋯ → Redeploy** the backend.

---

## Step 6 — Smoke-test the web MVP

1. Open the frontend URL: India map with the coloured PM2.5 hex grid and
   wind arrows.
2. DevTools (F12) → **Network** → reload: requests go to the backend URL and
   return `200`; **Console** has no CORS errors.
3. Click a hex → the detail panel opens (PM2.5, PDI, wind, forecast).
4. Zoom in → the grid refines.

| Symptom | Fix |
|---|---|
| Blank map, CORS error | Redo Step 5 |
| Requests to `localhost:8000` | `VITE_API_BASE_URL` missing at build → set + redeploy (Step 4) |
| `503` from `/health/ready` | `DATABASE_URL` wrong/missing, or PostGIS not created (Step 2) |
| Backend build fails "no app found" | Root Directory must be `backend`; confirm `backend/vercel.json` is on the deployed branch (Step 0) |
| Migration `invalid interpolation syntax` | Deploying an old branch — the fix is in Step 0's merge |

---

## Step 7 — Point the Flutter app at the backend

```powershell
cd C:\Users\KIIT\Documents\GitHub\gdg-c4c\partner_apps\air_health_flutter
flutter pub get
flutter run --dart-define=POLLUTION_API_BASE_URL=https://air-health-api.vercel.app
```

Release build:

```powershell
flutter build apk --release --dart-define=POLLUTION_API_BASE_URL=https://air-health-api.vercel.app
```

The app loads its current AQI from the backend. Omit the define and it falls
back to bundled dummy data; debug builds use the built-in simulator.

---

## Step 8 (optional) — Real Delhi-NCR data on a schedule

`.github/workflows/pipeline.yml` runs hourly (and on demand) once
configured. In GitHub: **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Secret | `DATABASE_URL` | **direct** Neon string |
| Variable | `PIPELINE_DEMO_MODE` | `true` (default) or `false` for live OpenAQ/Open-Meteo |
| Secret | `OPENAQ_API_KEY` | only when `PIPELINE_DEMO_MODE=false` |

Run it now: **Actions → Pollution pipeline → Run workflow**. With
`PIPELINE_DEMO_MODE=false` and a key, it produces live data; otherwise it is
a deterministic demo run. After it succeeds, the map still shows the
country-wide field, but Delhi NCR now shows real persisted cells
(`is_demo:false`). Use the **direct** URL here — Alembic does not use the
app's pooler-safe connect settings.

---

## Environment-variable reference

| Where | Name | Purpose |
|---|---|---|
| Backend (Vercel) | `DATABASE_URL` | Neon pooled connection string |
| Backend (Vercel) | `CORS_ORIGINS` | Allowed frontend origin(s) |
| Backend (Vercel) | `ENVIRONMENT`, `LOG_LEVEL` | Optional app config |
| Frontend (Vercel) | `VITE_API_BASE_URL` | Backend origin baked into the build |
| GitHub Actions | `DATABASE_URL` | Direct Neon string for the scheduler |
| GitHub Actions | `PIPELINE_DEMO_MODE`, `OPENAQ_API_KEY` | Deterministic vs live pipeline |
