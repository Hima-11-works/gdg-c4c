# Neon Object Storage + Vercel + Gemini Implementation Plan

**Goal:** finish a deployable, three-hour integration for Gemini-assisted review of citizen photos and satellite context per H3 cell while preserving the existing India pollution map, API contracts, alert workflow, and fire-department simulator.

**Selected stack**

| Need | Service |
|---|---|
| Relational database and spatial data | Neon PostgreSQL + PostGIS |
| Private photo bucket | Neon Object Storage through its S3-compatible API |
| Frontend hosting | Existing Vercel Vite project |
| API runtime | Existing FastAPI project on Vercel |
| AI photo assessment | Gemini Developer API, key managed through Google AI Studio |

**Important:** Neon provides PostgreSQL and Object Storage, not a host for the existing FastAPI application. Keep that API on Vercel. The frontend calls it; the API connects to Neon PostgreSQL, the private Neon Object Storage bucket, and Gemini. Keep database, object-storage, and Gemini credentials on the backend.

**Three-hour assumption:** Neon and Vercel already work as documented in [GO_LIVE.md](GO_LIVE.md). This schedule covers configuration, the Gemini features, deployment, and a focused verification pass. It does not include replacing the map, migrating databases, training a pollution-estimation model, or building new notification infrastructure.

## Cost and privacy gates

- Gemini: use an image-capable model currently listed on the Gemini API free tier. Do not link billing, choose a paid tier, or enable paid tools. Free-tier requests may be used to improve Google products, so disclose Gemini processing and obtain consent before sending citizen photos. Otherwise leave AI analysis off and keep manual review.
- Neon Object Storage: Neon currently includes up to 5 GB per project on its Free plan. Keep this project within the included quota and check current Neon pricing and usage before scaling; do not assume usage above the allowance is free. Preserve the no-billing-details constraint: use the existing Neon account/plan, and if enabling Object Storage requires an upgrade or payment details, leave photo uploads disabled until that constraint is resolved. [Neon backend GA and plan limits](https://neon.com/blog/neon-backend-is-ga)
- Keep the Neon bucket private. Neon controls bucket visibility through its Console, API, or configuration; S3 ACL and bucket-policy calls do not set Neon access. Do not enable `public_read` or expose object URLs. Photos remain available through the existing reviewer-authorized API. [Neon Object Storage details](https://neon.com/blog/building-neon-object-storage)
- Keep the Gemini key, Neon Object Storage credentials, database URLs, and reviewer key out of browser bundles, Flutter builds, logs, and source control.
- Gemini is advisory only: it cannot confirm the pollution source, infer AQI or pollutant concentration from an image, approve reports, or dispatch responders.

## Step-by-step plan (180 minutes)

### 1. Confirm access and current deployment — 0–10 minutes

1. Confirm the existing Vercel frontend and FastAPI projects are deployed.
2. Confirm Neon is reachable and PostGIS is enabled.
3. Confirm Neon Object Storage is available on the existing Neon project and production branch. Check the Free plan allowance and current usage; do not upgrade or enter billing details for this setup.
4. Confirm the backend reviewer key is configured and the private-photo review endpoints work.

**Gate:** do not start AI work until the API, database, reviewer flow, and private object storage are healthy. If Neon requires an upgrade or payment details to enable storage, leave production photo uploads disabled and resolve that account requirement separately.

### 2. Provision the private Neon bucket and backend credentials — 10–25 minutes

1. Link the repository to the existing Neon project and its production branch. Do not create a duplicate project or point production at a development branch.
2. Declare a private bucket in the project's `neon.ts` configuration, for example `buckets: { "citizen-evidence": {} }` (private by default), then run `neon deploy` against the linked production branch. Neon provisions the bucket with that branch.
3. Keep bucket access private. Neon manages visibility in its Console/configuration; do not use S3 ACL or bucket-policy calls to change access, and do not set `public_read`. [Neon Object Storage setup](https://neon.com/blog/building-neon-object-storage)
4. Use `neon env pull --service object-storage` (or the generated environment output from `neon deploy`) to retrieve the branch-specific S3 endpoint and AWS-compatible credentials into an ignored local `.env.local`. Map those generated values into the backend settings:

   | Variable | Value |
   |---|---|
   | `CITIZEN_MEDIA_STORAGE` | `s3` |
   | `CITIZEN_MEDIA_S3_BUCKET` | `citizen-evidence` (or the exact declared bucket name) |
   | `CITIZEN_MEDIA_S3_REGION` | Neon’s documented signing region; use `us-east-1` only if Neon’s generated configuration does not specify one |
   | `CITIZEN_MEDIA_S3_ENDPOINT_URL` | The S3 endpoint generated for the Neon branch |
   | `CITIZEN_MEDIA_S3_ACCESS_KEY_ID` | The Neon-generated S3 access key |
   | `CITIZEN_MEDIA_S3_SECRET_ACCESS_KEY` | The matching Neon-generated secret key |
   | `CITIZEN_MEDIA_MAX_BYTES` | `4194304` |

5. Put production-branch values in the backend Vercel project's Production environment and development-branch values in the ignored local environment file. Never add Neon storage credentials to the frontend or Flutter projects.
6. Redeploy the backend, then run the existing `python -m app.cli verify-media-storage` check to upload, read, and delete a test object. Confirm unauthenticated/public access is unavailable.

The existing `MediaStore` already supports S3-compatible endpoints, so Neon should use the current adapter and configuration rather than a new storage layer. Keep the 72-hour evidence-retention job enabled and verify that it can delete an expired object. Use separate Neon branches for development and production so test uploads and deletions cannot affect production files. [Neon CLI environment workflow](https://neon.com/blog/just-landed-in-the-neon-cli)

### 3. Verify Neon and set up Gemini — 25–40 minutes

1. Use the existing Neon PostgreSQL project with PostGIS. For the Vercel API runtime, use Neon's pooled connection string as `DATABASE_URL`; use the direct connection string only for Alembic migrations, as documented in [GO_LIVE.md](GO_LIVE.md).
2. Check `/health/ready`, `/api/v2/meta`, and `/api/v2/reports` against the deployed API.
3. In [Google AI Studio](https://aistudio.google.com/), create a current Gemini API key and select an image-capable model available on the free tier. Store its model ID in `GEMINI_MODEL`.
4. Use a current Auth key. Google's September 2026 key migration rejects legacy Standard keys; replace any old Standard key and test the new key. Restrict the key to Gemini API usage where the AI Studio controls allow it. [Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key)
5. Add `GEMINI_API_KEY` and `GEMINI_MODEL` to the backend Vercel environment only. Add blank examples to the repository's `.env.example`; never commit the actual key.
6. Do not put a Gemini key in a Vite environment variable, React, or either Flutter app. Gemini free-tier requests may be used to improve Google products; keep analysis disabled until the photo consent/privacy copy discloses that processing. [Gemini pricing and data use](https://ai.google.dev/gemini-api/docs/pricing)

**Gate:** a test call from the backend can reach the selected free-tier model. If the model requires paid billing, choose another free-tier model or leave Gemini disabled.

### 4. Implement backend Gemini assessment and persistence — 40–80 minutes

1. Add Google's supported `google-genai` Python SDK to `backend/pyproject.toml` and update the backend lock file.
2. Add optional backend settings for `GEMINI_API_KEY`, `GEMINI_MODEL`, and a request timeout/output limit below the configured Vercel function maximum duration. Missing key means AI analysis is disabled, not that the API fails to start.
3. Add an analysis operation under the existing citizen-evidence API. Require the same reviewer authorization as existing photo reads and review actions.
4. Load the evidence through the existing service and send only its sanitized derivative from private Neon Object Storage to Gemini. Do not accept a caller-supplied URL, bucket key, or arbitrary image bytes.
5. Ask for schema-constrained JSON containing:
   - `visible_observations`
   - `possible_event_type` (`smoke`, `fire`, `industrial plume`, `other`, or `unclear`)
   - `visual_support`
   - `missing_information`
   - `uncertainty`
   - `reviewer_summary`
6. Instruct Gemini to describe visible evidence only, return uncertainty for ambiguous photos, and never invent source, location, event time, AQI, pollutant, or concentration.
7. Validate model output with a strict Pydantic schema. Add an Alembic migration to save the validated advisory, evidence/report IDs, model ID, prompt/schema version, and generated time. Do not store a second copy of the photo or any API credentials.
8. Reuse a saved assessment when a reviewer reopens it. Only call Gemini again after an explicit reviewer action; this limits duplicate calls and quota usage.
9. Map timeout, provider quota, invalid-key, and Neon Object Storage/database errors to safe API responses. Preserve manual review on every failure.
10. Add mocked tests for valid/invalid output, missing key, timeout, quota failure, reviewer authorization, missing/deleted evidence, and saved-result reuse. No test should depend on a live Gemini call.

Use the official Python SDK's image and structured-output support. [Google GenAI SDKs](https://ai.google.dev/gemini-api/docs/libraries) · [Image input](https://ai.google.dev/gemini-api/docs/image-understanding) · [Structured output](https://ai.google.dev/gemini-api/docs/structured-output)

### 5. Add the reviewer UI and consent disclosure — 80–100 minutes

1. Add **Analyze with Gemini** to the existing citizen-photo review dialog.
2. Update the photo consent/privacy wording to disclose that an authorized reviewer may send the sanitized image to Gemini. Require the needed consent before enabling analysis for that photo.
3. Show a loading state, prevent duplicate requests, and display the structured advisory, uncertainty, model ID, and analysis time.
4. Keep human approval/rejection separate from the model output. The model cannot change evidence/report state.
5. Show clear disabled, no-consent, timeout, quota, and provider-error states. Keep manual review available.

### 6. Add Gemini-assisted satellite context per H3 cell — 100–155 minutes

Treat the satellite pipeline as the source of measurements and Gemini as a visual-pattern interpreter and plain-language explainer. A satellite image or a Gemini vision response cannot by itself provide ground-level pollutant concentration or CPCB AQI.

1. Reuse the existing Sentinel-5P UV Aerosol Index (UVAI), FIRMS fire detections, weather, and ground-monitor data. Add quality-controlled numeric ingestion for Sentinel-5P tropospheric NO₂ vertical column density, retaining its native `mol/m²` unit. Aggregate source pixels that actually overlap each H3 cell; record valid-pixel/area coverage, acquisition time, QA flags, and processing time. Do not turn coarse satellite pixels into falsely precise fine-resolution cell readings.
2. If a quality-controlled source and time permit, add NASA MAIAC aerosol optical depth (AOD) as a separate, unitless column indicator. Keep it optional for this release. Neither AOD nor NO₂ column density is surface PM₂.₅. Do not calculate CPCB AQI from either. Satellite-derived surface PM₂.₅ requires a separately calibrated and monitor-validated model; if none exists, return “no reliable surface estimate.” [Sentinel-5P NO₂ product](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_OFFL_L3_NO2) · [NASA MAIAC product guide](https://www.earthdata.nasa.gov/s3fs-public/2025-04/MCD19_User_Guide_V6.pdf)
3. For each cell and observation window, assemble a backend-owned evidence bundle: satellite indicators with native units and QA/coverage, time-matched ground PM₂.₅ (clearly marked observed or validated estimate, with monitor distance), local weather (wind direction/speed and humidity; boundary-layer height only when a source supplies it), and nearby FIRMS detection count, distance, confidence, and fire radiative power when available. Do not present a nearby monitor's reading as a measurement taken inside the H3 cell. Missing, cloudy, stale, or low-coverage values are `unavailable`, never zero. Compare with a cell's historical baseline only when enough comparable, quality-controlled observations exist; label the result as an anomaly/screening signal, not AQI. Do not combine unlike satellite products into one pollution score without calibration and validation.
4. Generate an optional cell-clipped thumbnail from the timestamped, backend-proxied satellite layer with its product name and visualization legend. Send that image and the compact evidence bundle to Gemini from the backend using `GEMINI_API_KEY`. Derive and validate the H3 geometry on the backend; do not trust client-supplied imagery, URLs, or measurements. Keep the existing secret-handling and reviewer/rate-limit controls.
5. Use structured output for a short **Satellite interpretation** with fields such as `visual_pattern` (`plume_like`, `smoke_or_dust_like`, `no_clear_pattern`, `unclear`), `possible_event_type`, `supporting_evidence`, `limitations`, and `summary`. Prompt Gemini to describe visible patterns and evidence only; it must not invent pollutant values, AQI, source attribution, exact fire confirmation, health diagnosis, or emergency severity. Numeric measurements, units, times, coverage, and data provenance in the response come directly from validated backend records and cannot be changed by Gemini.
6. Show the user, per H3 cell:
   - Surface PM₂.₅ in `µg/m³` from a time-stamped monitor observation or a separately validated estimate; identify which it is and show source, age, and uncertainty. Show CPCB AQI only when the required pollutant data and averaging windows pass the official CPCB method; otherwise leave AQI unavailable.
   - Separate satellite rows for NO₂ column density, UVAI, and (if enabled) AOD, with units, observation time, quality/coverage, and the explicit label **satellite indicator — not ground-level concentration**.
   - A local anomaly/trend only when the baseline gate passes; nearby fire detections as corroborating thermal-anomaly evidence, never as proof of pollution or a confirmed fire; available wind/weather context; and an evidence coverage/quality indicator.
   - Gemini's concise interpretation clearly marked **AI-assisted, uncertain, and advisory**. Do not let it trigger an authority alert or change report status automatically.
7. Add an explicit user-triggered analysis action or cached cell-detail request. Cache by H3 cell, observation window, source-product versions, Gemini model, and prompt/schema version; do not call Gemini on every map pan, hover, or render. Return a clear unavailable state when imagery or valid numeric inputs are missing. Add a retention/expiry policy for generated interpretations.
8. Test aggregation and H3 spatial coverage, QA/cloud/stale/no-data cases, native units and provenance, baseline eligibility, Gemini schema/timeout/quota failures, cache reuse, and the invariant that model output never replaces backend measurements. Use mocked Gemini responses; no live model call in automated tests.

**Acceptance gate:** the cell view distinguishes measured surface air quality from satellite indicators; every number carries its source, unit, timestamp, and coverage/quality; Gemini cannot generate or overwrite numbers; and sparse/cloudy evidence stays unavailable instead of appearing clean. CPCB AQI remains based on its prescribed pollutant inputs and averaging periods: at least three pollutant measurements, including PM₂.₅ or PM₁₀, are needed, and the highest valid sub-index determines the AQI. [CPCB National AQI](https://cpcb.nic.in/displaypdf.php?id=bmF0aW9uYWwtYWlyLXF1YWxpdHktaW5kZXgvRklOQUwtUkVQT1JUX0FRSV8ucGRm) · [Gemini image understanding](https://ai.google.dev/gemini-api/docs/image-understanding) · [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output)

### 7. Apply migrations and deploy through Vercel — 155–165 minutes

1. Set production `DATABASE_URL` to Neon's pooled connection string and retain the direct URL for the documented migration procedure.
2. Apply the new Alembic migration once using the Neon direct connection.
3. Verify the backend Vercel Production environment has Neon PostgreSQL, Neon Object Storage, and Gemini variables. Secrets belong only in the backend project.
4. Redeploy the backend. Deploy the frontend Vercel project after its reviewer dialog changes.
5. Keep `VITE_API_BASE_URL` pointed to the backend origin and preserve the existing exact-origin CORS allowlist. Vercel's current two-project deployment steps are in [GO_LIVE.md](GO_LIVE.md).

### 8. Run a focused end-to-end check — 165–180 minutes

- The Vercel-hosted map, search, cells, alerts, reports, and timeline still load from the production API.
- A citizen photo uploads to private Neon Object Storage, is retrievable through the reviewer API, and is not publicly accessible.
- An authorized reviewer can request an advisory for a consented smoke/fire test image.
- An unrelated or ambiguous image produces an uncertain response, not a fabricated pollution claim.
- Missing consent, reviewer key, Gemini key, Neon Object Storage object, or database access leaves manual review usable and reports a clear error.
- A saved advisory reloads without another model call.
- A cell detail shows sourced satellite indicators separately from observed or validated surface PM₂.₅/AQI; each shows units, acquisition time, and valid coverage.
- An H3 cell with cloudy, stale, or insufficient satellite coverage shows unavailable data and does not receive a fabricated zero, AQI, or Gemini-generated concentration.
- Gemini's visual interpretation is advisory only and cannot alter server-returned numeric measurements, approve a report, or dispatch responders.
- Browser requests, Vite bundles, Flutter apps, and logs contain no Gemini or Neon Object Storage secret.
- The Flutter simulator still reaches the existing API and can acknowledge/resolve incidents.

If any integration gate fails, preserve the existing deployment and leave the affected optional feature clearly unavailable. Do not weaken the private-bucket or reviewer-authorization requirements to meet the time limit.

## Explicitly excluded

- Firebase services, Cloud Functions, Cloud Run, or moving the API to a new host.
- Google Maps migration; keep MapLibre.
- Public Neon Object Storage buckets or direct browser access to the bucket.
- Gemini calls from the frontend or Flutter app.
- Automatic report approvals, source attribution, AQI inference from photos, or real emergency dispatch.
- Paid Gemini models/features or unapproved billing changes.

## Official references

- [Current deployment and environment-variable map](GO_LIVE.md)
- [Neon connection setup](https://neon.tech/docs/connect/connect-from-any-app)
- [Neon Object Storage setup and S3 compatibility](https://neon.com/blog/building-neon-object-storage)
- [Neon backend GA and Object Storage Free plan allowance](https://neon.com/blog/neon-backend-is-ga)
- [Neon CLI branch and environment workflow](https://neon.com/blog/just-landed-in-the-neon-cli)
- [Vercel environment variables](https://vercel.com/docs/environment-variables)
- [Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Google GenAI Python SDK](https://ai.google.dev/gemini-api/docs/libraries)
- [Gemini image input and structured output](https://ai.google.dev/gemini-api/docs/image-understanding)
- [Sentinel-5P tropospheric NO₂ product fields and units](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_OFFL_L3_NO2)
- [NASA MAIAC aerosol optical depth product guide](https://www.earthdata.nasa.gov/s3fs-public/2025-04/MCD19_User_Guide_V6.pdf)
- [NASA FIRMS VIIRS fire-detection description and caveats](https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_VIIRS_Firehotspots.html)
- [ESA Sentinel-5P coverage and instrument facts](https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-5P/Facts_and_figures)
- [CPCB National Air Quality Index method](https://cpcb.nic.in/displaypdf.php?id=bmF0aW9uYWwtYWlyLXF1YWxpdHktaW5kZXgvRklOQUwtUkVQT1JUX0FRSV8ucGRm)
