# Go-live checklist (detailed)

Deploy the web MVP (frontend + backend) on Vercel and point the Flutter app
at it. Everything is already implemented in the repo — this is all
configuration. Background: [`DEPLOYMENT_PLAN.md`](DEPLOYMENT_PLAN.md).

Read a whole step before doing it. Lines in `<>` are placeholders you fill
in. **Never paste a real password or connection string into chat or commit
it** — it belongs only in Vercel/GitHub.

## What you'll end up with

| Thing | Where | Example |
|---|---|---|
| Database | Neon (Postgres + PostGIS) | — |
| API | Vercel project, root `backend/` | `https://air-health-api.vercel.app` |
| Web app | Vercel project, root `frontend/` | `https://air-health.vercel.app` |
| Phone app | built locally | points at the API URL |
| Scheduler (optional) | GitHub Actions | hourly pipeline run |

---

## Step 0 — Put the deployment code on `main`

All deployment work is on the branch `flutter-completion`. Vercel and
GitHub's scheduler use `main`, which is the default branch.

1. Open a browser and go to `https://github.com/Hima-11-works/gdg-c4c`.
2. Sign in if needed.
3. In the address bar, go directly to:
   `https://github.com/Hima-11-works/gdg-c4c/compare/main...flutter-completion`
4. You should see **"Comparing changes"** with `base: main` ←
   `compare: flutter-completion`, and **"Able to merge"** in green.
5. Click **Create pull request**.
6. Give it a title (e.g. `Deployment: Vercel + scheduled pipeline`) and click
   **Create pull request** again.
7. Click **Merge pull request** → **Confirm merge**.
8. Verify: open `https://github.com/Hima-11-works/gdg-c4c/blob/main/backend/vercel.json`
   — it should load (not 404).

> If you'd rather not merge now, skip this and instead set each Vercel
> project's **Production Branch** to `flutter-completion` in Step 3/4. The
> scheduled job (Step 8) only runs from `main`, so it needs the merge.

---

## Step 1 — Neon database

1. Go to `https://neon.tech` → click **Sign Up** → **Continue with GitHub**
   → **Authorize**.
2. On the project screen click **Create project** (or **New project**).
   - Name: `air-health`.
   - Region: the one closest to you.
   - Postgres version: leave default.
3. Click **Create project** and wait a few seconds.
4. On the project dashboard, find the **Connect** button (top-right of the
   project panel) and click it. A panel opens with a connection string.
5. In that panel there's a **Connection pooling** switch/toggle.
   - Turn it **ON** → copy the string. Host will contain `-pooler`.
     Save it as: **POOLED URL** = `postgresql://...-pooler...?sslmode=require`
   - Turn it **OFF** → copy the string again (host has no `-pooler`).
     Save it as: **DIRECT URL** = `postgresql://...?sslmode=require`
6. Keep both somewhere safe for the next steps (e.g. a scratch note you
   delete later). The password is the part after `neondb_owner:`.

**You should see** a string like
`postgresql://neondb_owner:AbC123xyz@ep-cool-name-123456.us-east-2.aws.neon.tech/neondb?sslmode=require`.

---

## Step 2 — Create the tables

You'll run this on your Windows PC with Docker, so no Python install is
needed.

1. Start **Docker Desktop** and wait until the whale icon in the taskbar
   stops animating (Docker is running).
2. Press `Windows`, type `PowerShell`, press Enter.
3. Change to the backend folder:
   ```powershell
   cd C:\Users\KIIT\Documents\GitHub\gdg-c4c\backend
   ```
4. Build the backend image (first time takes a few minutes):
   ```powershell
   docker build -t air-health-backend .
   ```
   **You should see** it end with something like `naming to ... air-health-backend`.
5. Run the migrations, pasting the **DIRECT URL** from Step 1 inside the
   single quotes:
   ```powershell
   docker run --rm -e 'DATABASE_URL=<DIRECT URL>' air-health-backend alembic upgrade head
   ```
6. **You should see** three lines beginning `Running upgrade`, the last being
   `... -> 0003_forecast_hours_float`.

Verify the tables:
7. Back in the Neon dashboard, open **SQL Editor** (left sidebar).
8. Paste and run:
   ```sql
   SELECT PostGIS_Lib_Version();
   SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'public' ORDER BY table_name;
   ```
9. **You should get** a PostGIS version (e.g. `3.4.3`) and the tables
   `alert`, `forecast`, `grid_state`, `sensor_reading`, `weather_reading`.

**If it fails:**
| Message | Cause / fix |
|---|---|
| `Database configuration is incomplete` | The `DATABASE_URL` didn't reach the container — check the single quotes and that you used the DIRECT URL. |
| `invalid interpolation syntax` | You're on a branch without the fix — do Step 0. |
| `password authentication failed` | You copied the URL wrong (missing/extra character). Re-copy from Neon. |
| `could not connect` / timeout | Wrong region host, or you used the pooled host — use the DIRECT one. |

Leave the database empty — the API serves a country-wide demo field until
Step 8.

---

## Step 3 — Backend on Vercel

1. Go to `https://vercel.com` → **Sign Up** → **Continue with GitHub** →
   **Authorize**.
2. Click **Add New…** → **Project**.
3. Under **Import Git Repository**, find `gdg-c4c` and click **Import**.
   - If it's not listed: click **Adjust GitHub App Permissions** (or
     **Add GitHub Account**), grant access to the repo, come back.
4. On the configure page:
   - **Project Name**: `air-health-api` (or anything; this becomes part of
     the URL).
   - **Framework Preset**: leave as detected.
   - **Root Directory**: click **Edit** next to it, choose/type **`backend`**,
     click **Continue**. ← **required**
   - **Build and Output Settings**: leave everything default.
5. Scroll to **Environment Variables**. For each row, type the Name, type
   the Value, then click **Add**:
   | Name | Value |
   |---|---|
   | `DATABASE_URL` | `<POOLED URL>` |
   | `ENVIRONMENT` | `production` |
   | `LOG_LEVEL` | `INFO` |
6. Click **Deploy**. Watch the log; it installs Python deps and finishes with
   **"Deployment ready"** / **"Congratulations"**.
7. Copy the production URL. Either the big **Visit** button, or
   **Domains** in the project sidebar — e.g.
   `https://air-health-api.vercel.app`. Save as **API URL**.

Test it (replace the host with your API URL):
8. Open these in the browser:
   - `https://<API URL>/health` → `{"status":"ok",...}`
   - `https://<API URL>/health/ready` → `{"status":"ok","database":"ok","postgis_version":"..."}`
   - `https://<API URL>/docs` → the Swagger API page
   - `https://<API URL>/api/v1/grid/current?resolution=3&min_lat=6.5&min_lon=68&max_lat=37.5&max_lon=97.5`
     → a big JSON with `"is_demo":true` and `"data":[...]`

> Note: `/health` is on the same host as the API (the project root), **not**
> under `/api/v1`.

**If `/health/ready` returns `503`:** the DB URL is wrong. Vercel →
project → **Settings → Environment Variables → `DATABASE_URL` → Edit** →
paste the POOLED URL again → **Save** → **Deployments → ⋯ → Redeploy**.

---

## Step 4 — Frontend on Vercel

The frontend is a second Vercel project from the **same** repo.

1. Vercel → **Add New… → Project** → **Import** `gdg-c4c` again.
2. Configure:
   - **Project Name**: `air-health` (this becomes the web URL).
   - **Root Directory**: click **Edit** → **`frontend`** → **Continue**.
     ← **required**
   - **Framework Preset**: Vite (auto-detected).
3. **Environment Variables**:
   | Name | Value |
   |---|---|
   | `VITE_API_BASE_URL` | `https://<API URL>` (origin only — no `/api/v1`, no trailing slash) |
4. Click **Deploy** and wait for **"Deployment ready"**.
5. Copy the URL (e.g. `https://air-health.vercel.app`). Save as **WEB URL**.

> `VITE_API_BASE_URL` is baked into the build. If you change it later you
> must **Redeploy** (Deployments → ⋯ → Redeploy).

---

## Step 5 — Let the web app call the API (CORS)

Right now the browser will block the API calls until the backend allows the
web origin.

1. Vercel → open the **backend** project (`air-health-api`).
2. **Settings → Environment Variables**.
3. Add a variable:
   - Name: `CORS_ORIGINS`
   - Value: `<WEB URL>` (e.g. `https://air-health.vercel.app`)
   - For extra origins later, comma-separate them.
4. **Save**, then **Deployments** tab → the top deployment → **⋯** →
   **Redeploy** → confirm. Wait for ready.

---

## Step 6 — Test the web app

1. Open your **WEB URL** in the browser.
2. You should see a map of India with a coloured hex grid and wind arrows.
3. Press `F12` → **Network** tab → reload the page.
   - Requests should go to `https://<API URL>/api/v1/...` and show `200`.
   - **Console** tab should show no red CORS errors.
4. Click a hex → a detail panel opens (PM2.5, PDI, wind, forecast).
5. Zoom in a couple of steps → the grid should get finer.

**If it fails:**
| Symptom | Fix |
|---|---|
| Blank map + CORS error in Console | Redo Step 5, then hard-refresh (`Ctrl+F5`). |
| Requests to `http://localhost:8000` | `VITE_API_BASE_URL` wasn't set before the build — Step 4, then redeploy. |
| `/health/ready` is 503 | Step 3 database URL. |
| Everything 500s | Backend logs: Vercel → backend project → **Deployments → the deployment → Functions/Logs**. |

---

## Step 7 — Point the Flutter app at the API

On the machine that has the Flutter SDK:

1. Open PowerShell and check Flutter is available:
   ```powershell
   flutter --version
   ```
2. Go to the app and fetch packages:
   ```powershell
   cd C:\Users\KIIT\Documents\GitHub\gdg-c4c\partner_apps\air_health_flutter
   flutter pub get
   ```
3. Run it (first run downloads Gradle deps, can take several minutes):
   ```powershell
   flutter run --dart-define=POLLUTION_API_BASE_URL=https://<API URL>
   ```
   - If asked to pick a device, choose your emulator or connected phone.
   - The app should show the current AQI from the backend.
4. Build a release APK:
   ```powershell
   flutter build apk --release --dart-define=POLLUTION_API_BASE_URL=https://<API URL>
   ```
   Output path is printed; typically
   `build\app\outputs\flutter-apk\app-release.apk`.

> If you omit `--dart-define`, the app falls back to bundled dummy data; a
> debug build without it uses the on-screen scenario simulator.

---

## Step 8 (optional) — Real Delhi-NCR data on a schedule

The web MVP works without this. It adds real persisted cells for Delhi NCR
on an hourly schedule (GitHub Actions). Requires Step 0 (so the workflow is
on `main`).

1. GitHub repo → **Settings** (top bar).
2. Left sidebar → **Secrets and variables → Actions**.
3. On the **Secrets** tab, click **New repository secret**:
   - Name: `DATABASE_URL`
   - Secret: `<DIRECT URL>` (direct, not pooled)
   - **Add secret**.
4. Switch to the **Variables** tab → **New repository variable**:
   - Name: `PIPELINE_DEMO_MODE`
   - Value: `true`  (use `false` only for live OpenAQ/Open-Meteo data)
   - **Add variable**.
5. Only if you set `PIPELINE_DEMO_MODE=false`: back on **Secrets**, add
   `OPENAQ_API_KEY` with a key from
   `https://explore.openaq.org/register`.
6. Go to the **Actions** tab. If prompted, click **"I understand my
   workflows, go ahead and enable them"**.
7. In the left list click **Pollution pipeline** → **Run workflow** →
   **Run workflow**. Watch it run (green check). Its log should show
   `[OK  ]` for all five stages.
8. After it succeeds, the map still shows the country-wide field, and Delhi
   NCR now has real cells (`"is_demo":false`).

> Scheduled runs happen hourly and can be delayed; GitHub pauses a schedule
> after ~60 days with no repo activity (just re-enable it).

---

## Environment-variable reference

| Where | Name | Value |
|---|---|---|
| Backend (Vercel) | `DATABASE_URL` | POOLED Neon string |
| Backend (Vercel) | `CORS_ORIGINS` | WEB URL (comma-separated for more) |
| Backend (Vercel) | `ENVIRONMENT`, `LOG_LEVEL` | `production`, `INFO` |
| Frontend (Vercel) | `VITE_API_BASE_URL` | API URL (origin) |
| GitHub Actions | `DATABASE_URL` | DIRECT Neon string |
| GitHub Actions | `PIPELINE_DEMO_MODE` | `true` / `false` |
| GitHub Actions | `OPENAQ_API_KEY` | only when `PIPELINE_DEMO_MODE=false` |
