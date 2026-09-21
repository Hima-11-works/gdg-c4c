# Environmental inputs, trainable pollution models, and deterministic demo data

Status: implementation plan; application changes and datasets are not implemented by this document.
Prepared: 2026-09-22.
Source branch: origin/swastik, freshly fetched.
Source commit: ab303888e077b2a25c5e8df0f43e01dcf88c4d85 (Swastik #10).
Planning branch: plan/environmental-inputs.

## 1. Intended outcome and first release

Extend Air Health to estimate current PM2.5 and forecast future concentrations using recent station observations, rainfall, wind, atmospheric mixing, seasonality, roads, land cover, population, and fire signals. Compute residential exposure separately from concentration. Introduce a trainable model and reproducible dummy inputs that exercise the same feature-building and inference paths as live inputs.

First release:
- Delhi-NCR, using the existing configured bounding box plus a bounded upwind context buffer.
- Hourly inference; current conditions and hourly forecast anchors through six hours.
- Weather, calendar, population, roads, and land-cover inputs first.
- FIRMS and traffic interfaces with complete synthetic fixtures; live integrations in subsequent milestones.
- A residual LightGBM model, compared with persistence and the existing IDW/dispersion baseline.
- Explicit live, synthetic, stale, missing, and fallback provenance in web and Flutter.
- Country overview remains available, but regional real coverage must never become a nationwide live-data claim.

PM2.5 is the supervised target in ug/m3. AQI remains a derived presentation quantity using documented averaging semantics. Do not call an instantaneous PM2.5 conversion a complete official AQI. Additional pollutants require separate labels, models, and schema work later.

Do not implement arbitrary universal coefficients such as "rain removes 30%" or "twice the population means twice the pollution." Environmental effects and interactions must be validated against observations.

## 2. Branch findings that determine the implementation

| Existing code | Verified behavior | Required change |
|---|---|---|
| backend/app/ingestion/open_meteo.py | Fetches current wind, precipitation, temperature, humidity, and the current hour's boundary-layer height | Add archived observations and issue-specific hourly forecast sequences |
| backend/app/domain/estimation.py | Estimator accepts grid + sensor readings only | Introduce a typed feature-aware prediction interface; retain the old estimator as a baseline |
| backend/app/domain/dispersion.py | Forecast contract assumes source-free evolution and forbids overall increases | Keep that invariant specific to the deterministic baseline; learned prediction has a different contract |
| backend/app/services/grid_computation.py | IDW, then additive citizen-fire contribution, then PDI | Separate baseline from feature-driven prediction; avoid double counting fires |
| backend/app/models/tables.py | Raw observations and grid/forecast history exist; grid_state key is cell + timestamp | Extend existing history rather than assuming grid_state contains only one row per cell |
| backend/alembic/versions/0005_grid_pdi_factors.py | PDI factor persistence already exists | Reuse it; do not reimplement this migration |
| backend/app/services/grid.py | Reads exact requested H3 IDs; empty results synthesize data | Implement real LOD aggregation and explicit fallback policy |
| backend/app/core/config.py | Default model grid resolution 8, weather sampling resolution 5 | Preserve native grid initially; cache weather by sampling cell |
| frontend/src/lib/lod.ts | Web requests resolutions 3/4/5, weather also resolution 2 | Aggregate native results consistently; a res-8 model alone will not populate the web map |
| backend/tests/test_api_contract.py | Explicitly forbids provider/source fields and fixes envelope fields | Deliberately revise this contract and its tests to support provenance |
| backend/app/ingestion/demo.py | Five fixed stations; provider timestamps use wall clock | Replace with injected-clock, scenario-driven providers |
| backend/app/services/demo_data.py | Separate nationwide synthetic fallback | Preserve overview behavior but connect it to shared scenario metadata and regional snapshots |
| Flutter grid adapter | Joins weather coordinates to pollution on h3_cell; has dummy scenario mode | Preserve the join during migration; unify provenance and forecast horizons |
| .github/workflows/pipeline.yml | Hourly workflow already exists, demo defaults true | Extend inference workflow and create a distinct training workflow |

Some README/schema comments are stale: they say PDI factors are not persisted or that scheduling is absent. Treat executable code as the baseline and update those descriptions with the implementation.

## 3. Outputs and calculation contracts

### 3.1 Concentration

For cell c, issue time t, and horizon h:

    baseline(c,t,h) = IDW at h=0; deterministic forecast at h>0
    prediction(c,t,h) = max(0, baseline(c,t,h) + residual_model_h(features(c,t,h)))
    training_residual = observed_station_PM25(t+h) - baseline_at_station(t,h)

Train models for h = 0, 1, 2, 3, 4, 5, 6 hours so the Flutter hourly series is supported. Report +1/+3/+6 as the primary product checkpoints. The h=0 model must exclude the target station's simultaneous observation from its baseline and spatial features.

Retain the existing 15-minute web timeline by interpolating between current/hourly anchors. Mark intermediate values as interpolated, with anchor/model/run metadata. Evaluate accuracy at actual trained horizons. Do not fabricate calibrated intervals for interpolated frames; return null initially. Bound all requests to the advertised six-hour horizon.

The baseline and feature vector used during training must be reproducible at inference. Cells with insufficient evidence return no supported prediction or the explicitly identified baseline, never an automatically invented live value.

### 3.2 Outlook and explanations

Expose forecast change (ug/m3), supported interval, coverage quality, and the strongest model-associated features where available. Keep existing PDI labeled as a heuristic, version its definition, and do not silently substitute ML importance values into its [-100,100] scale.

Road density, industrial land fraction, and vegetation fraction may populate PDI's existing slots through documented normalization. Those PDI weights are a UI heuristic, separate from learned concentration coefficients. Defer automatic PDI-based alert-rule changes until independently evaluated.

A feature contribution means "associated with this model prediction," not a causal emission share. Population must never be directly added to PM2.5.

### 3.3 Exposure

    population_weighted_PM25 = sum(population_c * predicted_PM25_c) / sum(population_c)
    residents_above_threshold = sum(population_c for supported cells with PM25_c > threshold)

Use only cells with a supported concentration and a population estimate. Also return covered population, unknown population, dataset year, threshold/unit, forecast time, and geographic scope. If covered population is zero, the weighted result is null.

These are residential exposure estimates, not real-time occupancy, individual dose, or medical risk. Compare concentrations and thresholds with matching averaging periods. Do not sum population across overlapping map tiers.

## 4. Feature specification and source plan

| Group | Initial feature fields | Source / update policy | Missing-data behavior |
|---|---|---|---|
| Pollution | PM2.5 lags 1/3/6/24h; trailing means/slopes; station count, nearest distance and age | Existing OpenAQ adapter + historical backfill; hourly | Unknown stays null; no future filling |
| Rain | hourly_rain_mm, precip_mm, rain_6h_mm, rain_24h_mm, hours_since_rain | Open-Meteo history/current/forecasts | Distinguish zero rain from missing coverage |
| Wind | wind_u_ms, wind_v_ms, speed, upwind station summaries | Open-Meteo; hourly sequences | Flag missing wind; baseline may use its existing decay-only behavior |
| Mixing | boundary_layer_height_m, temperature_c, relative_humidity_pct, surface_pressure_hpa | Weather model, subject to availability | Missingness feature and validated fallback |
| Calendar | hour_sin/cos, day_of_year_sin/cos, weekday/weekend, region | Derived; local calendar in Asia/Kolkata, UTC storage | Always reproducible; seasonal label is explanatory only |
| Population | population_count, people_per_km2, reference_year | WorldPop raster; versioned offline import | Unknown population remains unknown, not zero |
| Roads | road_km_per_km2 by road class; major-road distance | OSM regional extract; versioned offline import | Coverage metadata; no roads != unobserved roads |
| Land cover | built_up_fraction, vegetation_fraction, bare_soil_fraction; industrial_area_fraction where mapped | ESA WorldCover + OSM industrial polygons | Fractions computed over known area; store coverage fraction |
| Fires | upwind FRP sums in selected radii, detection count, age, confidence; citizen-report features separate | FIRMS + existing reports | Zero only after a successful complete empty query |
| Traffic, later | observed/free-flow speed ratio, confidence, sampled road coverage | Contract-approved traffic feed for selected corridors | Never equate missing/closed roads with free flow |
| Quality | provider age, observed-vs-modeled kind, spatial support, missing mask | Every ingestion path | Propagate to prediction and API |

Engineering rules:
- Derive wind vectors consistently from meteorological "from" direction. Unit-test all cardinal directions.
- Rain accumulation windows are backward-looking at issue time; future rain uses a forecast issued no later than issue time.
- Start with a transparent upwind cone/distance weighting at several neighborhood scales. Preserve input age and station count.
- Zero wind has no meaningful upwind direction; use an explicit calm flag.
- Encode season cyclically and include measured weather. Avoid a universal fixed monsoon multiplier across India.
- Aggregate road geometries with geodesic or appropriate projected lengths; never treat degrees as kilometers.
- Aggregate population counts by fractional pixel overlap where needed; do not average population counts or double count boundary pixels.
- Population and land cover are dated estimates. Save product version, reference date, availability date and coverage.
- Live speed does not reveal vehicle count, fleet mix, or emission rates. Road/time proxies must be labeled as proxies.
- Postpone satellite column NO2 and full emissions inventories; they are not interchangeable with ground PM2.5 labels.
- Optional future CAMS background must carry native resolution and modeled provenance; it does not supply fine-scale ground truth.

Fetch weather at existing coarser sampling cells and share it across native prediction cells. Static rasters and OSM processing run offline, not per page load or inside Vercel request handlers. Exact source quotas/licenses should be checked at integration time; this plan does not depend on paid traffic history.

## 5. Time alignment, storage, and reproducibility

Use these clocks explicitly:
- observed_at / valid_at: when the observation or forecast is about.
- issued_at: upstream forecast/model issue time.
- available_at: earliest supported availability to the application.
- ingested_at: when this system fetched it.
- prediction_issued_at and feature_schema_version: reproducibility keys.

Archived measurements imported today do not prove historical real-time availability. Use archived release timestamps where available; otherwise document a conservative latency assumption and run delayed-observation sensitivity tests. Use issue-specific archived weather forecasts for forecast evaluation. Reanalysis can support research but must not be presented as a faithful operational replay.

Proposed migrations, each new and reversible; verify the next free revision before implementation:
1. 0006_env_inputs: ingestion dataset/run metadata; nullable provenance links on existing raw rows; weather forecast history and cell static features.
2. 0007_feature_snapshots: per-cell feature snapshots and optional fire/traffic observations.
3. 0008_model_predictions: model registry, prediction runs/results, and exposure fields/materialized summaries if needed.

Suggested entities:

| Entity | Key / important fields |
|---|---|
| dataset_version | id; source, kind (observed/modeled/synthetic), product version, region, attribution/license, coverage, availability |
| ingestion_run | id; dataset, start/end, status, fetched time, errors, simulation_id nullable |
| cell_static_features | cell + dataset bundle version; population, roads, land cover, valid/available dates, coverage |
| weather_forecast | provider/sample-cell + issued_at + valid_at; hourly variables, availability, ingestion reference |
| fire_detection | provider + deterministic detection identity; coordinates, acquisition time, available time, FRP/confidence, satellite |
| traffic_observation | provider + road segment + valid_at; speed/free-flow speed, confidence, coverage |
| cell_feature_snapshot | run_id + cell + horizon + feature_schema_version; typed schema serialized to JSONB; missing mask and dataset references |
| model_version | id; artifact path/hash, features, training dates, region, horizon, metrics, status, synthetic_only flag |
| prediction_run | id; issue time, feature/model versions, data mode, scenario, completeness and publish status |
| prediction_result | run_id + cell + horizon; baseline, corrected PM2.5, interval bounds, method, quality flags |

Do not replace sensor_reading or duplicate raw history needlessly. Add history query methods to its repository. Keep legacy grid_state/forecast output through a projection from the selected successful prediction run; store candidate model comparisons only in prediction_result.

Indexes should support region/cell + time and issue/valid-time selection. Batch upserts must be idempotent. Use UTC throughout. Keep typed schemas and feature versions even if feature payloads use JSONB.

Separate demo data using a dedicated demo database/configuration initially. simulation_id and provenance remain necessary, but no schema-wide multi-tenancy rewrite is needed. Reject synthetic rows when exporting real training datasets. Exclude generated rasters, parquet and model binaries from Git; commit small fixtures and manifests.

## 6. Model pipeline and architecture

Add domain types/protocols under existing layers:
- app/domain/features.py: CellStaticFeatures, WeatherForecastSample, FeatureVector, FeatureQuality.
- app/domain/prediction.py: FeatureAwarePredictor, PredictionContext, PredictionResult.
- app/domain/providers.py: forecast weather, static feature, fire and traffic ports.
- app/domain/repositories.py: history and new-entity repository methods.

Add adapters:
- app/ingestion/open_meteo.py: expanded forecast/history adapters or dedicated adjacent modules.
- app/ingestion/static_features.py: input manifest readers for preprocessed raster/road artifacts.
- app/ingestion/firms.py and traffic.py: later adapters.
- app/ingestion/factory.py: explicit live/demo/provider selection; no network fallback inside demo mode.

Add services:
- features.py: one deterministic transform for training and inference.
- feature_collection.py: repository/provider I/O; assemble as-of input bundles.
- prediction.py: baseline + residual composition and eligibility checks.
- model_artifacts.py: version/hash/schema-checked local artifact loading.
- exposure.py: population summaries.
- model_training.py / model_evaluation.py: offline orchestration.
- demo_scenarios.py: shared deterministic simulation.

Place heavy preprocessing/training commands under backend/scripts/ or existing app/cli.py entry points. Avoid a new app/ml layer unless test_architecture.py and the design are deliberately updated. Domain code remains free of I/O and LightGBM.

Revise grid_computation.py, forecasting.py and pipeline/run.py to:
1. Capture a single run clock and selected scenario/data mode.
2. Ingest available inputs independently and retain individual freshness/errors.
3. Build as-of features.
4. Compute the documented baseline.
5. Apply a compatible promoted model when eligibility permits.
6. Persist results and mark a complete prediction run ready.
7. Publish consistent current/forecast/metadata views; derive alerts from that run.
8. Preserve and label the last complete run on partial failure.

Keep deterministic dispersion tests, including its source-free invariant. Do not impose that invariant on ML outputs: emissions can raise concentrations. A sum of concentrations across unequal cells is not physical mass; avoid expanding that existing diagnostic into a new scientific claim.

Citizen fires: define the ML baseline as sensor-only IDW/dispersion; give fires to the feature model. Legacy baseline mode can retain the existing additive heuristic, explicitly tagged. Training and inference must use the same choice; no additive fire adjustment after an ML model already consumed that signal.

Dependency groups:
- Base API: remain lightweight and serve persisted results.
- Inference job: add pinned LightGBM/numpy requirements as needed.
- Training/preprocessing extras: pandas, pyarrow, rasterio, geometry tools and evaluation dependencies.
- Update Docker/lock-generation and CI deliberately; do not load large training artifacts per API request.

Proposed settings: PREDICTION_MODEL=baseline|residual, FEATURE_SCHEMA_VERSION, MODEL_ARTIFACT_URI, MODEL_VERSION, FEATURE_MAX_AGE_HOURS, LIVE_DEMO_FALLBACK=false, DATA_MODE=live|demo, DEMO_SCENARIO, DEMO_SEED, DEMO_ANCHOR_UTC. Keep DEMO_MODE as a documented compatibility alias during migration.

## 7. Evaluation and retraining

Dataset target: 12 months minimum for an initial seasonal assessment; prefer 24 when available. Preserve hourly station labels, missing episodes, reporting delays and changing station coverage. Estimate volume from actual station counts before backfill.

Validation:
- Rolling chronological folds, a final untouched test period, and spatially held-out stations.
- Purge training labels whose forecast target time crosses into validation. Fit preprocessing/imputation using training data only.
- For spatial generalization, exclude held-out stations from all spatial features for that evaluation.
- Also evaluate the operational setting where a station's previously available readings may be used for future prediction.
- Never train on IDW grids, synthetic labels or the model's own outputs as if they were observations.
- Compare persistence, existing baseline, lag-only learner, then weather/calendar, then static variables, then fire/traffic.
- Report station-balanced and overall MAE, RMSE, bias, high-pollution errors, alert precision/recall and interval coverage by horizon/season/coverage.
- Treat station predictions as point-supported estimates; station validation alone does not establish exact whole-cell averages.

Fit residual quantile models for a nominal 80% interval, calibrate on separate validation data, and measure held-out coverage. Quantile ordering and nonnegative bounds must hold. Where calibration is unsupported, return no interval plus the quality reason. Keep legacy confidence as a quality indicator, not a probability.

Initial candidate promotion targets (engineering choices to revisit before training):
- At least 5% lower held-out MAE against the stronger of persistence and baseline at the primary +1/+3/+6 horizons.
- No primary-horizon MAE regression greater than 2%.
- No high-pollution MAE regression greater than 5% and no alert-recall drop greater than 5 percentage points.
- Nominal 80% interval coverage approximately 75-85%, with width and subgroup coverage reported.
- Insufficient high-pollution examples or seasonal coverage blocks claims/promotion for that scope.

Train candidates weekly after new validated observations arrive. Inference stays hourly. Initially promote through a reviewed artifact/configuration update after the metrics pass; later automate if useful. Rollback selects the prior immutable artifact. Monitor errors as labels arrive, feature drift, missingness, and coverage. Drift triggers investigation/candidate training, not automatic replacement.

## 8. Dummy data: concrete deliverables

Dummy data is part of the implementation acceptance criteria, not an optional screenshot aid. Ship a deterministic generator, manifests, small committed golden fixtures, coherent API snapshots, and documentation.

### 8.1 Three dataset sizes

| Profile | Content | Intended use |
|---|---|---|
| tiny-ci | 12 adjacent valid res-8 cells, 6 stations, 48h warm-up + 24h replay + 6h future, 4 weather samples, 8 road segments | Fast tests, edge cases, provider fixtures |
| regional-demo | 256 adjacent valid res-8 cells in Delhi-NCR, 32 synthetic stations, 48h warm-up + 72h replay + 6h future, 64 road segments, mixed residential/industrial/green areas | Dashboard/mobile demo and pipeline integration |
| seasonal-training-smoke | Same regional geography/stations, 365 days of hourly records, leap-year variant, multiple weather regimes and event episodes | Exercise backfill, feature export, training, validation and artifact loading |

Generate res-8 cells using the existing H3 facade from a bounded center/ring selection; assert exact counts and adjacency where specified. Derive coarser parents; never invent H3-looking strings. Generate full native India only for the existing coarse overview fallback, not a nationwide res-8 annual dataset.

The yearly profile yields 280,320 station-hours before missingness and about 2.24 million native cell-hours if fully materialized. Default to storing station/coarse-weather history and static cells, building feature batches lazily. Avoid materializing all horizons for every yearly cell in CI. Keep annual output in compressed parquet outside Git.

### 8.2 Determinism and temporal behavior

Each manifest includes:
- schema_version, generator_version, scenario_id, seed=42.
- fixed anchor_utc (default 2025-01-15T00:00:00Z), region, timezone, native resolution.
- cell/station/road counts, warm-up, replay length, forecast horizons.
- data_mode=synthetic, source attribution "Air Health synthetic scenario".
- expected events, supported assertions, and generated artifact checksums.

Explicit CLI overrides support other season anchors. Use an injected clock and deterministic PRNG streams derived from stable seed/entity/time identifiers; do not use Python's randomized hash(). Advancing replay time selects a later state on the same timeline, rather than rebuilding the scenario at time zero.

Generate history, current data and future forecast runs coherently. Each weather forecast must have a deterministic issue-dependent error relative to the synthetic latent weather; it must not expose perfect future truth. Observations have noise, reporting delay and missingness. Hidden latent truth is only in test/generator outputs, never in production features.

A small simulation may use synthetic emissions + transport + removal to generate labels. Its parameters are explicitly fictional; fitting this generator is a software smoke test, never scientific validation or a production model-promotion signal.

### 8.3 Required scenarios and acceptance behavior

| Scenario | Synthetic setup | Must demonstrate |
|---|---|---|
| clean_breezy | Low activity, moderate wind, deep mixing | Stable low pollution, no alert |
| winter_stagnation | Shallow mixing, calm winds, recurring activity | Accumulation and warning/critical examples |
| monsoon_washout | Same starting conditions; rainfall arrives mid-replay | Declining synthetic truth after rain; rolling totals align |
| post_rain_rebound | Rain stops; activity continues | No permanent "clean" assumption |
| rush_hour | Commuter-road activity morning/evening | Time-varying pressure with identical static roads |
| weekend_contrast | Paired weekday/weekend activity profiles | Calendar features differ, static features unchanged |
| industrial_vs_green | Similar initial concentration; distinct land use | Different histories/outlooks in authored simulation |
| upwind_fire | Confidence-tagged FIRMS-like event upwind | Delayed downwind response, detection provenance |
| wind_shift | Wind rotates during the forecast | Forecast weather sequence matters; plume direction changes |
| population_pair | Identical concentration/weather, different population | Exposure differs; population alone does not change authored pollution |
| sparse_stations | Remove most stations in a subarea | Lower support, baseline fallback or unavailable results |
| stale_weather | Old observations, missing latest forecast run | Explicit degradation and last-good-run behavior |
| traffic_outage | Missing traffic; separate valid zero-traffic interval | Missing != zero; model feature eligibility tested |
| empty_fire_feed | Successful no detections vs provider failure | Zero vs unknown distinction |
| duplicate_reports | Repeated client_report_id and nearby satellite event | Idempotence and no double-counted source contribution |
| partial_region | Supported cells inside a coarse parent; others absent | Coverage fractions and exposure denominators remain honest |

For matched scenarios, re-use geography, seed and nuisance inputs so only the named variable changes. Directional assertions apply to authored synthetic truth and controlled baseline tests; do not force every trained model to obey a universal rain/vegetation rule.

### 8.4 Example synthetic ranges and faults

Generator ranges, not scientific calibration constants:
- PM2.5: typical authored episodes 5-300 ug/m3; separate valid extreme fixture above 500 without silently clipping observations.
- Temperature: 5-42 C; humidity 15-98%; wind 0-10 m/s; mixing height 80-2200 m.
- Hourly rain: 0-15 mm, with dry sequences and wet episodes.
- Population: zero-population industrial/green cells through dense residential cells; density derived from actual cell area.
- Land fractions: [0,1], consistent known-area totals; roads have class, geometry and length.
- Traffic: road-linked speeds, free-flow reference, confidence and unavailable samples.
- Fires: acquisition time, categorical VIIRS-like confidence and positive FRP; citizen reports retain their own schema.
- Controlled 5% observation missingness and noise for smoke training; targeted 30%/complete-outage scenarios separate.
- Bad-provider fixtures: negative rain, NaN PM2.5, wrong units, malformed date, duplicate record, timeout/429, and partial batch response. They must be rejected or flagged, not normalized into believable readings.

Check in tiny provider-response fixtures and expected outputs. Generate large samples on demand from the manifest. Do not require API keys or external requests for any demo profile.

### 8.5 One scenario across all surfaces

- Factory-selected demo providers emit normal domain objects through real ingestion/services.
- Regional fallback responses read the same scenario snapshot as persisted demo data, so current/weather/forecast/detail/exposure agree.
- Any retained India-wide fallback shares simulation_id/clock and remains visibly synthetic.
- Generate small JSON API golden snapshots for frontend and Flutter from the backend scenario runner.
- Update Flutter scenario_data.dart and mocks to consume equivalent fixture values/clock or generated shared snapshots, instead of independently authored conflicting forecasts.
- Scenario ID, version, issue time and simulation ID participate in frontend cache keys.
- Demo selection is a development/demo control. Live production does not silently switch to a dummy scenario when upstream data fails.

Proposed commands (to implement, not available yet):

    python -m app.cli demo-generate --profile regional-demo --scenario winter_stagnation --seed 42
    python -m app.cli demo-replay --scenario winter_stagnation --at 2025-01-15T08:00:00Z
    python -m app.cli demo-export-api --scenario monsoon_washout
    python -m app.cli training-export --mode live --start 2024-01-01 --end 2026-01-01
    python -m app.cli train --dataset <manifest> --candidate
    python -m app.cli evaluate --model <version> --split <manifest>

demo-generate is idempotent in a dedicated demo database; it must never truncate unrelated live records. --synthetic-only artifacts cannot be promoted to a live model.

## 9. H3 aggregation, API and clients

### 9.1 Resolution contract

Native predictions remain res 8 for compatibility with existing defaults and mobile queries. Compute static features at that grid. Aggregate results to res 3/4/5 for the web and weather res 2 where requested.

- Map concentration: area-weighted mean over covered child cells, labeled as covered-area mean.
- Exposure: population-weighted concentration and summed population, explicitly separate.
- Roads: sum lengths then divide by covered area; land fractions area-weighted.
- Wind: aggregate u/v vectors, then derive direction; never average 359 and 1 degrees arithmetically.
- Report coverage fraction. Do not upscale missing children into synthetic live coverage.
- Handle partial parent/bbox intersections and region clipping consistently.
- Intervals at coarse resolution need separate calibration/aggregation treatment; return null initially rather than average quantiles.
- Cell detail and grid overview must use the same aggregation function and published run.
- A fine display grid reflects model output locations, not a claim that weather or station evidence has that resolution.

### 9.2 Versioned response changes

Use /api/v2 for the new environmental/prediction contract; keep /api/v1 stable while migrating clients. Add v2 schemas/modules rather than weakening v1 contract tests. This is necessary because the existing tests intentionally prohibit provenance.

v2 envelope: generated_at, run_id, mode (live/demo/mixed), is_demo, data, attribution, coverage.
v2 per-cell metadata: input_kind, prediction_method, dataset/model/feature versions, observed/issued/valid times, synthetic flag, quality flags and optional interval.
is_demo is true when any relevant returned input/output is synthetic; mode explains mixed responses. Prefer homogeneous runs and layers.

Initial routes:
- GET /api/v2/grid/current and /grid/forecast: existing bbox/resolution semantics plus exposure/quality metadata.
- GET /api/v2/cells/{cell}: weather, static inputs, forecast, exposure, versioned PDI and explanations.
- GET /api/v2/weather: compatible cell coordinates for Flutter's join plus forecast time metadata.
- GET /api/v2/alerts: alerts for the published run with matching provenance.
- GET /api/v2/exposure: bounded region/time/threshold summaries.
- GET /api/v2/fires: once satellite observations are introduced.
- GET /api/v2/meta: region, resolutions, horizons, model and dataset versions.
- Keep citizen-report POST behavior; both clients can use the existing endpoint during migration.

API requests read persisted/aggregated results. No training, raster processing, per-cell upstream fetch loops or secret keys in clients.

Web changes: lib/types.ts/api.ts, forecastFrames.ts, MapPage.tsx, CellDetailPanel.tsx, StatusBanner.tsx, LayerToggle.tsx, Legend.tsx, MapView.tsx and UI state. Add environmental details, concentration/exposure layer choice, available horizons and unmistakable simulation/freshness labeling. Defer decorative extra overlays.

Flutter changes: grid/grid_api.dart DTOs/client, grid_api_pollution_data_provider.dart, domain data freshness/forecast models, home/nearby screens and scenario simulator. Advertise six-hour support through metadata, fixing the current 12-hour chart label when only six hours are returned. Missing weather must eventually stop preventing coordinate resolution; add cell centroids to v2 grid data and retire the weather join after migration.

## 10. Implementation milestones

| Milestone | Concrete deliverables | Acceptance gate |
|---|---|---|
| M0: contracts | Architecture decision, typed feature/provenance schemas, source inventory, dummy manifests and v2 contract examples | Review units, time semantics, scope and baseline eligibility |
| M1: storage + synthetic providers | New migrations/repositories, injected clock, tiny/regional scenario generator, snapshot export | Offline end-to-end demo; repeat run identical/idempotent |
| M2: environmental features | Weather sequences/history, calendar, WorldPop/OSM/land-cover import, shared FeatureBuilder | As-of and geographic aggregation tests; missing != zero |
| M3: model and evaluation | Historical station export, baseline reproduction, residual training, quantiles, registry | Time/spatial held-out report and synthetic/live separation |
| M4: publication and clients | Hourly prediction orchestration, real H3 aggregation, v2 API, web/Flutter updates, exposure | Same published run/scenario across all views; demo visible |
| M5: fires + traffic | FIRMS ingestion first; optional sampled traffic ingestion and retained history | Source quality/age checks and incremental evaluation before model use |
| M6: operations | Separate candidate-training workflow, artifact promotion/rollback, drift/error summaries, docs | Replay deployment with no keys; live deployment fails visibly on unavailable data |

Recommended hackathon delivery boundary: M0-M4 with synthetic fire/traffic, followed by live FIRMS if time allows. If sufficient real historical data or validation is unavailable, ship baseline inference plus the complete feature/exposure system and label ML as experimental. Do not replace real validation with a good synthetic training score.

Illustrative effort for one experienced developer: 1 day contracts, 2 days storage/dummy generation, 2-3 days feature ingestion, 2-3 days training/evaluation, 2-3 days integration, 1-2 days operations. Data access/backfill and actual findings may extend this; each milestone is independently reviewable.

## 11. Verification and completion checklist

Backend:
- Extend existing provider, ingestion, pipeline, repository/schema and migration tests.
- Add tests for as-of joins, forecast issue cutoff, target-station exclusion, train/serve feature parity, and partial upstream failures.
- Assert raster population conservation within tolerance, valid geometries, road units, wind direction and LOD consistency.
- Assert exact scenario replay checksums and seed differentiation.
- Assert source-free baseline invariants separately from learned-model constraints.
- Assert synthetic exclusion from live training/promotion and no cross-mode repository contamination.
- Assert one published run across current/forecast/alerts and meaningful stale timestamps.
- Assert exposure denominators, zero population, missing population, overlapping resolutions and partial regions.
- v1 compatibility remains covered; v2 tests require explicit provenance and intervals.

Frontend:
- Typecheck/build/lint plus focused tests for v2 parsing, scenario-aware cache keys, unsupported horizons and simulation/freshness labels.
- Add a focused test runner if needed; current package has no test command.
- Browser verification: rainfall replay, wind shift, sparse coverage, exposure pair, country-to-city zoom, timeline and selected-cell consistency.

Flutter:
- Update existing grid adapter, dummy scenario, freshness, notification and acceptance tests.
- Run flutter analyze and flutter test on a machine with Flutter installed.
- Verify simulation data cannot trigger misleading live health claims; verify notifications use displayed forecast times.

Documentation:
- Update README, docs/architecture.md, deployment/go-live instructions, Flutter README and stale schema descriptions.
- Document dataset attribution, dataset dates, commands, examples, model metrics, supported geography and demo limitations.
- Keep actual held-out results separate from this plan's proposed thresholds.

This planning change requires document/path/diff verification only. No application tests prove these future features until the implementation exists.

## 12. Source references

These references support source selection; recheck access/variable availability and terms when wiring providers:
- OpenAQ physical measurements and v3 API: https://docs.openaq.org/about/about
- OpenAQ attribution/data-use terms: https://docs.openaq.org/about/terms
- Open-Meteo forecast variables: https://open-meteo.com/en/docs
- Open-Meteo historical forecast/run guidance: https://open-meteo.com/en/docs/historical-forecast-api
- Open-Meteo hosting terms/pricing: https://open-meteo.com/en/pricing
- Optional CAMS background resolution: https://open-meteo.com/en/docs/air-quality-api
- WorldPop population products: https://hub.worldpop.org/project/categories?id=3
- OSM regional extracts: https://download.geofabrik.de/asia.html
- ESA WorldCover products: https://esa-worldcover.org/en/data-access
- FIRMS API/key/field guide: https://firms.modaps.eosdis.nasa.gov/content/academy/data_api/firms_api_use.html
- TomTom traffic product overview: https://developer.tomtom.com/traffic-api/documentation/product-information/introduction
- LightGBM objectives: https://lightgbm.readthedocs.io/en/latest/Parameters.html
