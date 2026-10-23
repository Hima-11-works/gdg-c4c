# Realtime Data Implementation Handoff

## Current state

- Continue on branch `beta`, tracking `origin/beta`.
- RT07 is complete and pushed in commit `e519581` (`feat(satellite): persist NRT products and availability`). RT01–RT06 are also present earlier in the branch history.
- The detailed scope and decisions remain in `docs/REAL_TIME_DATA_IMPLEMENTATION_PLAN.md`.
- Before editing, inspect `git status`. Preserve the existing untracked `docs/BUG_AUDIT.md` and `docs/REAL_TIME_DATA_IMPLEMENTATION_PLAN.md`; do not stage them as part of later steps.
- Complete one RT step at a time. Quickly verify the step, make one commit for it, push to `origin/beta`, and only then start the next step. Do not combine speculative work from future steps into a commit.

## RT07 delivered

RT07 adds durable, versioned Sentinel-5P product and per-H3 observation storage for QA-qualified UVAI and NO2 values, acquisition/availability provenance, QA and sample support, encrypted persistence of rotating CDSE refresh tokens, and cell-level satellite context backed by those records. The GIBS imagery path now reads WMTS time domains and chooses the newest date with responding sample tiles for the requested map area; tile misses and availability checks are cached/coalesced.

RT07 verification completed:

- Focused backend suite: 62 passed across Sentinel-5P parsing, satellite repository, satellite context, GIBS tile API, and pipeline tests.
- Backend `compileall` and focused Ruff checks passed.
- Frontend production build passed. Vite reports the existing large `MapView` chunk (>500 kB); the build succeeds.
- `git diff --check` passed before commit.

Operational limits to preserve:

- Sentinel-5P and GIBS are near-real-time/daily sources. A five-minute job cadence does not make source publication five-minute or second-by-second.
- GIBS availability is based on a bounded sample of HTTP tile responses. Its fraction is not pixel QA or a measurement of geographic-area coverage.
- UVAI is an aerosol indicator, not PM2.5 or source attribution. Satellite NO2 is a column measure, not ground-level concentration or AQI.
- A live deployment must supply a valid initial `CDSE_REFRESH_TOKEN` and deployment-held `CDSE_SECRET_ENCRYPTION_KEY`. Verify unattended account/authentication behavior in the actual deployment before relying on token rotation.

## Remaining work

### RT08 — Bound publication volume and preserve live evaluation evidence

Review `pipeline/run.py`, publication and forecast services/repositories, model tables, retention tooling, and pinned-run handling. Separate source checks from new publications; cap active cells and horizons; add compatible retention/compaction before raising cadence. Preserve compact issue-time forecasts and later observed labels for evaluation, including independent station/time holdouts. Keep deterministic baselines clearly identified while real labels accumulate and retain existing model-promotion guards. Preserve last-good live data, reject unavailable/unsupported current cells, and prevent expired predictions from creating future-spike alerts.

**Acceptance:** unchanged checks do not create a dense publication snapshot; live storage and compute remain within measured limits; retention keeps active pins and audit/evaluation evidence.

### RT09 — Apply live-data behavior consistently to all clients

Update the dashboard, both Flutter clients, and responder simulator to read the publication pointer every 30–60 seconds while foregrounded, using ETags/conditional requests. Fetch run-pinned datasets only when run, location, or horizon changes. Apply shared source-age and quality flags across current conditions, nearby data, forecasts, satellite context, and alerts. On refresh failure, retain dated stale views and suppress new automatic alerts from expired/unsupported evidence. Add appropriate response caching keyed by run ID, resolution, bbox, and horizon; keep pointer TTL short and never publicly cache private reviewer evidence. Preserve local GeoNames and administrative boundaries, and confirm basemap provider terms before production reliance.

**Acceptance:** refreshes do not multiply upstream station/weather calls; all clients agree on mode, run, and source age; viewer count does not multiply external collection.

### RT10 — Add optional context only within measured free budgets

Import real, versioned OSM/WorldPop/WorldCover pilot features through existing contracts and preprocess them at source release cadence. Keep unconfigured traffic unavailable. Add CAMS only as a distinct background product if quota and storage permit; do not present it as station observations or bypass OpenAQ support requirements. Define explicit review/calibration requirements before citizen sensors enter interpolation, retaining independent station validation. Keep Gemini advisory photo review optional; reuse saved assessments, deduplicate concurrent work, budget reanalysis, and leave reviews pending on quota exhaustion without blocking environmental ingestion.

**Acceptance:** optional inputs have versioned provenance, consume only their reserved budget, and cannot prevent core station/weather operation.

### RT11 — Shadow-run, measure, and cut over

After deployment credentials and limits are known, run a bounded 48–72-hour live shadow collection. Measure provider-to-app and observation-to-provider lag where publication timestamps exist, successful collection intervals, fresh station coverage, request weights, bytes, database growth, and failed jobs. Exercise empty/stale data, invalid units, missing weather, corrections, clock skew, expired CDSE tokens, 429 resets, multiple workers, delayed scheduling, cloudy swaths, and schema failures. Validate web, citizen, and responder flows against the shadow API, including report review, alert freshness, incident provenance, and offline recovery. Cut over only after mode isolation, budgets, retention, and client freshness pass; retain dated last-good live data and a code rollback path.

**Acceptance:** live runs contain no synthetic contributions; controlled tests show no avoidable quota overshoot; measured freshness targets and infrastructure headroom are met; source outages are visible.

## Order and decisions

Implement RT08 and RT09 before increasing optional feeds or expanding coverage. RT10 is budget-gated. Run RT11 only after the live deployment has valid provider credentials and measured resource limits. The initial pilot assumptions are Delhi NCR, one quota owner, separate demo/live deployments, and existing infrastructure. Confirm source licenses/attribution, scheduler ownership, CDSE account support, and database/hosting allowances before production cutover.
