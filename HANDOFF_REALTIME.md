# Realtime Data Implementation Handoff

## Current state

- Continue on branch `beta`, tracking `origin/beta`.
- RT10 is complete and tested (`feat(context): add optional context within measured free budgets`). RT01–RT09 are also present in the branch history.
- The detailed scope and decisions remain in `docs/REAL_TIME_DATA_IMPLEMENTATION_PLAN.md`.
- Before editing, inspect `git status`. Preserve the existing untracked `docs/BUG_AUDIT.md` and `docs/REAL_TIME_DATA_IMPLEMENTATION_PLAN.md`; do not stage them as part of later steps.
- Complete one RT step at a time. Quickly verify the step, make one commit for it, push to `origin/beta`, and only then start the next step. Do not combine speculative work from future steps into a commit.

## RT10 delivered

RT10 integrates optional environmental and spatial context strictly bounded within measured free provider budgets:

- **Versioned OSM/WorldPop/WorldCover Features:** Preprocessed real pilot features stored in `backend/app/domain/data/pilot_static_features_delhi_ncr.json` with explicit source release provenance (WorldPop 2025, OSM 2025-Q1, ESA WorldCover 2021-v200). `FeatureBuilder` integrates versioned static features into pipeline cell contexts without blocking core runs when absent.
- **Unconfigured Traffic:** Strictly unavailable (`predicted_traffic = None` / `missing_fields` contains traffic); synthetic or default speeds are forbidden when unconfigured.
- **CAMS Distinct Regional Background:** Dedicated `CamsBackgroundService` operating under `"cams"` shared PostgreSQL provider budget (10/min, 100/hr, 1000/day, 50MB/day). Observations model atmospheric background at native 0.4° resolution with `is_station_observation = False` and strict runtime assertion (`assert_never_station_readings`) preventing CAMS observations from masquerading as ground stations or bypassing OpenAQ support requirements.
- **Citizen Sensor Review & Calibration:** Enforced explicit human review (`status == "verified"`), India geofencing, freshness window, physical bounds ([0, 1000] µg/m³), and slope/intercept calibration formulas in `qualify_citizen_submission`. Added `assert_retains_independent_station_validation` preventing citizen submissions from entering independent reference holdout sets.
- **Gemini Advisory Photo Review Optimization:** Added thread-safe per-evidence in-flight lock (`_IN_FLIGHT_LOCKS`) to deduplicate concurrent analysis requests. Reuses saved assessments; reanalysis within cooldown period (`gemini_reanalysis_cooldown_seconds`, default 300s) returns existing assessments without consuming provider quota. Provider rate limits and budget exhaustion leave reviews pending for manual inspection without blocking environmental ingestion.
- **Provider Budgets:** Extended `SqlProviderRequestBudget` with policies for `"cams"` and `"gemini"`.
- **Alembic Revisions & Schemas:** Shortened Alembic revision strings to <= 32 chars across `0031_shared_provider_budgets` and `0036_satellite_observations` to satisfy `alembic_version.version_num` column constraints.

RT10 verification completed:
- New dedicated RT10 test suite: 10/10 passed (`backend/tests/test_rt10_optional_context.py`).
- Focused related test suites: 69/69 passed (`test_static_features_import.py`, `test_feature_builder.py`, `test_pipeline_run.py`, `test_gemini_assessment.py`, `test_shared_request_budget.py`, `test_migrations_offline.py`).
- Frontend production build (`npm run build`) passed cleanly.
- Ruff checks on all modified backend files passed with 0 errors.

Operational limits to preserve:
- Static features are preprocessed at source release cadence; pipeline operation must never depend on live external GIS downloads.
- CAMS background must never be stored in `sensor_reading` or used to fulfill OpenAQ sensor requirements.
- Unverified citizen sensor submissions must never enter interpolation.
- Gemini analysis failures or quota limits must never abort environmental ingestion runs.

## RT09 delivered

RT09 applies consistent live-data behavior, lightweight foreground pointer polling, conditional requests, run-pinned response caching, and stale view retention across the web dashboard, Flutter citizen app, and responder simulator:

- **Pointer Polling & Conditional Requests:** All clients read the publication pointer (`/api/v2/meta`) every 30 seconds while foregrounded using HTTP `If-None-Match` conditional requests, receiving HTTP 304 Not Modified when the publication run has not changed.
- **Run-Pinned Response Caching:** Clients fetch run-pinned endpoints (`/grid/current`, `/grid/forecast`, `/weather`, `/alerts`, `/exposure`, `/cells/{cell}`) only when run ID, resolution, bounding box, or forecast horizon changes. In-memory response caches in `frontend/src/lib/api.ts`, `air_health_flutter/lib/data/grid/grid_api.dart`, and `fire_dept_simulator/lib/src/api.dart` prevent redundant network downloads for identical parameters.
- **Shared Source-Age & Quality Flags:** Common source-age, data mode (`live`/`demo`), and quality flags are surfaced across current conditions, nearby areas, forecasts, satellite context, and alerts.
- **Stale Retention & Alert Suppression:** On network or refresh failures, clients retain dated stale views and display clear stale indicators. New automatic alerts from expired or unsupported evidence are suppressed across `AlertEngine` (`DataFreshness.isStaleAt`) and simulator dispatch guards.
- **Backend Cache-Control & Private Evidence Protection:** Added `_handle_conditional_etag` helper generating deterministic SHA-256 ETags and returning HTTP 304 on match. Pointer TTL is set to `public, max-age=30, must-revalidate`, run-pinned endpoints to `public, max-age=300, stale-while-revalidate=600`, and cell satellite context to `public, max-age=300`. Enforced `Cache-Control: private, no-store` on all report evidence endpoints (`/reports/{id}/evidence`) to prevent intermediaries from publicly caching private reviewer evidence.
- **Local GeoNames & Boundaries:** Preserved local place name resolution and boundaries.

RT09 verification completed:
- Backend test suite: 64 passed (including 5 new tests in `test_api_v2_caching.py`).
- Frontend production build (`npm run build`) passed cleanly.
- `air_health_flutter` test suite: 12/12 passed (`test/data/grid_api_provider_test.dart`).
- `fire_dept_simulator` test suite: 28/28 passed (`api_test.dart`, `models_test.dart`, `incidents_screen_test.dart`).
- Ruff checks on modified backend files passed; `git diff --check` passed cleanly.

Operational limits to preserve:
- Pointer polling remains foreground-only with 30s TTL; clients do not loop in the background.
- Pinned datasets are immutable per `run_id`; caching keys must include run ID and query parameters.
- Private reviewer evidence must always carry `Cache-Control: private, no-store`.

## RT08 delivered

RT08 bounds prediction publication volume, decouples unchanged source checks from dense publication snapshots, caps active grid cell and horizon computation, and introduces automated retention and compaction for predictions while safeguarding evaluation holdout evidence and active alert pins:

- **Source Check Decoupling:** Added deterministic source input fingerprinting (`_compute_input_fingerprint` across sensors, weather, and qualified fire detections). Unchanged inputs skip dense publication snapshots, preserving the existing active published run and alerts.
- **Computation Limits:** Capped active grid computation states via `pipeline_max_active_cells` (default 500) and forecast horizons via `pipeline_max_forecast_horizons` (default 24).
- **Retention & Compaction Service:** Added `PredictionRetentionService` with `sweep()`, a periodic background scheduler job (`prediction-retention`), and CLI command `sweep-predictions`. Compaction preserves key issue-time/evaluation horizons while trimming intermediate granular horizons older than `prediction_compact_after_hours` (default 24h); sweeping prunes unpinned runs older than `prediction_retention_hours` (default 72h) while protecting the latest run per (region, mode), active alert runs, and runs with scenario/evaluation evidence.
- **Evaluation Evidence & Spatial Holdouts:** Added `contributing_station_ids` to `FeatureQuality` and database storage. Enhanced corridor evaluation (`require_unused_stations`) to verify independent holdout stations against contributing station IDs.
- **Live Cell & Alert Guardrails:** Enforced rejection of unsupported/unavailable live cells (`predicted_pm25 = None`, `confidence = 0.0`, method `"unavailable-unsupported"`). In alert generation, excluded unsupported cells and expired forecasts (`forecast_time <= generated_at` or `forecast_hours <= 0`) to prevent future-spike alerts from expired runs.

RT08 verification completed:
- Focused backend suite: 109 passed across `test_prediction_retention.py`, `test_prediction_publication.py`, `test_alert_generation_service.py`, `test_pipeline_run.py`, `test_corridor_evaluation.py`, and `test_feature_builder.py`.
- Backend `compileall` and focused Ruff checks passed.
- Frontend production build (`npm run build`) passed.
- `git diff --check` passed cleanly.

Operational limits to preserve:
- Unchanged check skipping applies only when input fingerprints match; `force_publish=True` forces dense snapshot creation when required.
- Compaction drops intermediate granular horizons (`h > 0 and h not in keep_horizons`), keeping 0h, 1h, 6h, 12h, and 24h for audit and evaluation holdout scoring.
- Active alert runs remain pinned and protected from retention sweeps until expired/closed.

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

### RT11 — Shadow-run, measure, and cut over

After deployment credentials and limits are known, run a bounded 48–72-hour live shadow collection. Measure provider-to-app and observation-to-provider lag where publication timestamps exist, successful collection intervals, fresh station coverage, request weights, bytes, database growth, and failed jobs. Exercise empty/stale data, invalid units, missing weather, corrections, clock skew, expired CDSE tokens, 429 resets, multiple workers, delayed scheduling, cloudy swaths, and schema failures. Validate web, citizen, and responder flows against the shadow API, including report review, alert freshness, incident provenance, and offline recovery. Cut over only after mode isolation, budgets, retention, and client freshness pass; retain dated last-good live data and a code rollback path.

**Acceptance:** live runs contain no synthetic contributions; controlled tests show no avoidable quota overshoot; measured freshness targets and infrastructure headroom are met; source outages are visible.

## Order and decisions

Implement RT08 and RT09 before increasing optional feeds or expanding coverage. RT10 is budget-gated. Run RT11 only after the live deployment has valid provider credentials and measured resource limits. The initial pilot assumptions are Delhi NCR, one quota owner, separate demo/live deployments, and existing infrastructure. Confirm source licenses/attribution, scheduler ownership, CDSE account support, and database/hosting allowances before production cutover.
