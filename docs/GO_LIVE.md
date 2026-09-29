# Deploy the web app and API

This guide targets the current `main` branch. It deploys the Vite web app and
FastAPI backend as two Vercel projects, with Neon Postgres + PostGIS. GitHub
Actions can run the data pipeline and photo-retention sweep. Never paste real
connection strings or secret keys into chat, source control, or a public issue.

## What you need

- A GitHub account with access to this repository.
- Vercel and Neon accounts.
- Docker Desktop for the one-time database migration command.
- Optional: an AWS S3 or Cloudflare R2 bucket for citizen photos.

## 1. Create the database and apply migrations

1. In Neon, create a Postgres project in a region close to your users/API.
   Confirm the project supports PostGIS.
2. Open **Connect** and save both connection strings privately:
   - **Pooled URL** for the Vercel API.
   - **Direct URL** for migrations and GitHub Actions.
3. In PowerShell, build the backend image and apply every migration to `head`:

   ```powershell
   cd C:\Users\KIIT\Documents\GitHub\gdg-c4c\backend
   docker build -t air-health-backend .
   docker run --rm -e 'DATABASE_URL=<DIRECT URL>' air-health-backend alembic upgrade head
   ```

   Replace `<DIRECT URL>` with the direct connection string. Do not use a
   hard-coded migration number; `head` follows the current `main` schema.
4. In Neon’s SQL Editor, run `SELECT PostGIS_Lib_Version();` to confirm PostGIS
   is available.

## 2. Deploy the backend to Vercel

1. In Vercel, choose **Add New → Project** and import this repository.
2. Set **Root Directory** to `backend`. Keep the detected FastAPI settings.
3. Add these Production environment variables:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Neon **Pooled URL** |
   | `ENVIRONMENT` | `production` |
   | `LOG_LEVEL` | `INFO` |
   | `HOTSPOT_SCAN_BACKEND` | `database` |

   Set `CORS_ORIGINS` after you create the web project in step 4. The database
   setting is required for the map, alerts, reports, and photo metadata.
4. Deploy, then copy the API origin, for example
   `https://air-health-api.vercel.app` (no path after the domain).

## 3. Deploy the frontend to Vercel

1. Create another Vercel project from the same repository.
2. Set **Root Directory** to `frontend` and use the Vite preset.
3. Add these Production environment variables before deploying:

   | Name | Value |
   |---|---|
   | `VITE_API_BASE_URL` | Backend origin from step 2, without `/api/v1` or a trailing slash |
   | `VITE_ENABLE_FIRE_REPORTING` | `true` to enable the citizen report button; otherwise leave unset/false |

   `VITE_API_BASE_URL` is baked into the frontend build. If you change it,
   redeploy the frontend. Without it, the browser falls back to localhost.
4. Deploy and copy the web app origin.

## 4. Allow the web app to call the API

1. In the backend Vercel project, add `CORS_ORIGINS` with the exact web origin,
   such as `https://air-health.vercel.app`.
2. Save and redeploy the backend. Add any other browser origins as a comma-
   separated list. Do not use `*` for a production site.

## 5. Optional: enable private citizen photo storage

Photo upload is off by default. Vercel’s local filesystem is temporary, so do
not set `CITIZEN_MEDIA_STORAGE=filesystem` there. Configure a **private** S3
bucket (AWS S3 or an S3-compatible service such as Cloudflare R2) and add these
Production variables to the backend project:

| Name | Value |
|---|---|
| `CITIZEN_MEDIA_STORAGE` | `s3` |
| `CITIZEN_MEDIA_S3_BUCKET` | Private bucket name |
| `CITIZEN_MEDIA_S3_REGION` | Provider region; use `auto` for Cloudflare R2 |
| `CITIZEN_MEDIA_S3_ENDPOINT_URL` | S3 API endpoint; blank for AWS S3 |
| `CITIZEN_MEDIA_S3_ACCESS_KEY_ID` | Restricted bucket access key |
| `CITIZEN_MEDIA_S3_SECRET_ACCESS_KEY` | Matching secret key |
| `CITIZEN_MEDIA_MAX_BYTES` | `4194304` (4 MiB) |

The frontend and backend cap each photo at 4 MiB to leave multipart overhead
under Vercel’s 4.5 MB request limit. After saving the variables, redeploy the
backend. Keep the bucket private; reviewers retrieve images through the API.

To run the automatic 72-hour retention sweep, add matching GitHub Actions
configuration under **Repository Settings → Secrets and variables → Actions**:

- Variables: `CITIZEN_MEDIA_STORAGE=s3`, `CITIZEN_MEDIA_S3_BUCKET`,
  `CITIZEN_MEDIA_S3_REGION`, and (for R2) `CITIZEN_MEDIA_S3_ENDPOINT_URL`.
- Secrets: `CITIZEN_MEDIA_S3_ACCESS_KEY_ID` and
  `CITIZEN_MEDIA_S3_SECRET_ACCESS_KEY`.

The hourly workflow verifies the bucket can write/read/delete, then removes
expired evidence and its bytes. The API’s `REPORTS_REVIEWER_KEY` is optional;
set a strong secret on Vercel to enable reviewer-only photo/report moderation.
The review screen asks the reviewer for that key at runtime.

## 6. Optional: run the data pipeline

The web app can show its clearly labelled demo field without live ingestion.
To populate the database, open **GitHub → Actions → Pollution pipeline → Run
workflow** after configuring:

| Kind | Name | Value |
|---|---|---|
| Secret | `DATABASE_URL` | Neon **Direct URL** |
| Variable | `HOTSPOT_SCAN_BACKEND` | `database` |
| Variable | `PIPELINE_DEMO_MODE` | `true` for demo/synthetic inputs; `false` for live ingestion |
| Secret | `OPENAQ_API_KEY` | Required when `PIPELINE_DEMO_MODE=false` |
| Secret | `FIRMS_MAP_KEY` | Optional NASA FIRMS corroboration |
| Secret | `CDSE_REFRESH_TOKEN` | Optional Sentinel-5P imagery access |

The default pipeline mode is demo. Keep the UI’s **DEMO SIM** label visible for
synthetic data. Live inputs are not a guarantee of complete coverage or an
official air-quality warning.

For the optional photo retention sweep, also set the S3 GitHub variables and
secrets listed in step 5. The schedule runs hourly; GitHub may delay scheduled
jobs, and disables schedules after long periods without repository activity.

## 7. Verify the deployment

Open these endpoints using the backend origin:

- `/health` — API process is running.
- `/health/ready` — database connection and PostGIS are ready.
- `/docs` — interactive API documentation.

Then open the web origin and check the browser’s Network and Console panels.
API requests should go to the backend domain without CORS errors. The map
should render and show whether its data is demo or live. Test an alert click,
cell details, and search. If photo storage is enabled, submit a small report
photo, then review it with the configured reviewer key.

The authority incident endpoints are for the fire-department **simulator**.
They do not dispatch real emergency services or send push notifications. Their
writes remain disabled unless `SIMULATOR_API_KEY` and `SIMULATOR_ACTORS` are
configured on the backend. Never bundle that shared server key in the public
frontend or a downloadable mobile app.

## 8. Optional: run the Flutter client

On a machine with Flutter installed:

```powershell
cd C:\Users\KIIT\Documents\GitHub\gdg-c4c\partner_apps\air_health_flutter
flutter pub get
flutter run --dart-define=POLLUTION_API_BASE_URL=https://<BACKEND ORIGIN>
```

For an APK, use `flutter build apk --release` with the same `--dart-define`.
The app needs only the API origin; it appends API paths itself. Do not put
server-only credentials in `--dart-define` values for a public build.
