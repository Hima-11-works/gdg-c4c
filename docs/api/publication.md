# v2 publication contract and demo runbook

This document covers two things:

1. **The v2 response contract** — what the `/api/v2/*` routes return and, in
   particular, how population and exposure reach the response.
2. **An empty-database demo runbook** — the exact command sequence that turns
   a freshly migrated database into one published, self-consistent India demo
   run with non-null population and exposure.

It also documents how the scheduled pipeline publishes that run and how a
failed live source is surfaced instead of being silently replaced.

---

## 1. Why there is a publication step

The `/api/v2/*` routes do **not** read the v1 grid/forecast tables directly.
They read *one published run*: the newest row of the `prediction_run` table
for the matching region, resolved by
`app.services.prediction_queries.PredictionQueryService.run()` →
`SqlPredictionPublicationRepository.latest_run()` (`ORDER BY published_at DESC,
id ASC LIMIT 1`, filtered by `region`).

A run is written by `PredictionPublicationService.publish()`, which turns a set
of `FeatureSnapshot`s into immutable `prediction_run` + `prediction_result`
rows. Historically that write only happened via the manual
`prediction-publish` CLI step, so a scheduled pipeline run updated v1 tables
and left the web app reading a stale or absent v2 run.

The pipeline now closes that gap: `app.pipeline.run` ends with a
`publish_v2` stage (`app/services/publication_pipeline.py`) that builds feature
snapshots from the state the run just persisted and publishes them through the
same service the CLI uses.

---

## 2. The v2 response contract

Every route returns a `V2Envelope` (`app/api/schemas_v2.py`), which makes five
facts visible together:

| Field | Meaning |
| --- | --- |
| `run_id` | The published run all values in this response came from. **Every v2 route for a given run returns the same `run_id`.** |
| `mode` | `live` \| `demo` \| `mixed` — the provenance label. |
| `is_demo` | Backwards-compatible boolean; `true` when mode is demo or any contributing row is synthetic. |
| `attribution` | `DatasetRefOut[]` — dataset id, source, product, version, kind, region, attribution, license. |
| `coverage` | Spatial coverage for the requested view (native vs display resolution, supported cells). |
| `data` | The payload for that route. |

Routes (`app/api/routes/predictions_v2.py`, prefix `/api/v2`):

| Route | Returns |
| --- | --- |
| `GET /meta` | `MetaV2Out`: `latest_run_id`, region, generated_at, native resolution, supported display resolutions, supported horizons, feature schema version, data mode. |
| `GET /grid/current` | `GridCurrentV2Out[]`: current PM2.5, PDI, confidence, wind, metadata, per-cell exposure. |
| `GET /grid/forecast` | `ForecastV2Out[]`: baseline/predicted/lower/upper PM2.5; 15-minute interpolation between published anchors. |
| `GET /cells/{h3_cell}` | `CellDetailV2Out`: current, forecasts, weather, static features, exposure, PDI factors. |
| `GET /weather` | `WeatherV2Out[]` from the feature vector. |
| `GET /alerts` | `AlertV2Out[]` derived from published results (critical ≥ 250, warning ≥ 91 µg/m³). |
| `GET /exposure` | `ExposureOut`: population-weighted PM2.5, residents above threshold, covered/unknown population, threshold, scope. |

### 2.1 How population and exposure reach the response

There is **no population table** read at request time. Population flows through
the *feature vector*:

1. `FeatureBuilder.build(...)` writes `population_count` and
   `population_density_per_km2` into each snapshot's `CellFeatureVector`,
   from the `CellStaticFeatures` supplied to it.
2. `PredictionPublicationService.publish()` copies the full vector into each
   `prediction_result.feature_vector` JSONB column.
3. `PredictionQueryService._aggregate_cell()` reads
   `feature_vector["population_count"]` back and computes exposure:
   - `covered_population` = Σ population over rows with a non-null prediction;
   - `population_weighted_pm25` = population-weighted mean prediction;
   - `residents_above_threshold` = Σ population where prediction > threshold;
   - `unknown_population` = population of cells with population but no
     prediction.

**Population is null in a published run only when** the snapshots were built
without static features, or a static feature's `available_at`/`valid_from`
window excludes the snapshot's times. The deterministic demo always attaches
population on every cell, so a demo run's population and exposure are non-null.
`/meta` and `/exposure` also expose `population_dataset_version`; for the demo
it is null because the synthetic dataset ref carries no "population" product
string.

---

## 3. Empty-database demo runbook

Starting from an empty (but migrated) database, the following produces **one
published v2 run with non-null population and exposure**, with every v2 route
agreeing on the same `run_id`.

### Prerequisites

Set the database connection (a direct, non-pooler Postgres/PostGIS URL):

```bash
export DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/DBNAME"
# or set POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / POSTGRES_HOST / POSTGRES_PORT
```

### Commands

```bash
cd backend

# 1. Create every table.
alembic upgrade head

# 2. Publish one deterministic India (Delhi-NCR) demo run in a single command.
#    This runs the full pipeline; in demo mode the final stage publishes a
#    complete run built from the committed `regional-demo` scenario.
DEMO_MODE=true python -m app.pipeline.run

# 3. Verify the web-facing result: the newest run resolves and its exposure
#    coverage is readable.
python -m app.cli verify-publication --region india
```

The same publication can also be driven explicitly, without the ingestion
stages, via the offline feature-export path:

```bash
# Build the India feature export (population included on every cell)...
python -m app.cli demo-features \
  --profile regional-demo --replay-hour 12 --history-hours 24 --out work/features.json

# ...then publish it as one immutable run. `--region india` must match the
# query service's default region or /api/v2/* will not find the run.
python -m app.cli prediction-publish \
  --input work/features.json \
  --feature-run-id demo-regional-2025-01-15T12Z \
  --mode demo --region india
```

### Expected output

```text
[OK  ] sensor_ingestion: fetched=... saved=... skipped_duplicates=...
[OK  ] weather_ingestion: fetched=... saved=... skipped_duplicates=...
[OK  ] fire_reports: seeded=... (deterministic demo sightings)
[OK  ] grid_computation: cells=256 sensors_used=32 saved=...
[OK  ] forecasting: cells=256 generated=... saved=...
[OK  ] alert_generation: cells_evaluated=256 alerts_created=...
[OK  ] publish_v2: published demo run demo-winter_stagnation-<UTC-hour> (mode=demo, cells=256, results=1792, profile=regional-demo)
```

And from `verify-publication`:

```text
Published run resolved: run_id=demo-winter_stagnation-20250115T1200Z region=india mode=demo cells=256 horizons=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
  generated_at=2025-01-15T12:00:00+00:00 feature_run_id=features-demo-winter_stagnation-20250115T1200Z scenario_id=winter_stagnation
  exposure: covered_population=196941.94 cells_with_residents_above_threshold=32
```

### Example `/api/v2/exposure` response

```json
{
  "generated_at": "2025-01-15T12:00:00+00:00",
  "run_id": "demo-winter_stagnation-20250115T1200Z",
  "mode": "demo",
  "is_demo": true,
  "data": {
    "population_weighted_pm25": 180.62,
    "residents_above_threshold": 196941.94,
    "threshold_pm25": 60.0,
    "covered_population": 196941.94,
    "unknown_population": 1537334.54,
    "population_dataset_version": null,
    "scope": "india; H3 resolution 8; 256 non-overlapping cells; +0h"
  }
}
```

### Notes on the numbers

- The demo places **32 stations** across **256 res-8 cells**. Only cells with
  ≥ 2 stations within the IDW radius get an observed PM2.5; the rest keep a
  population estimate but a null concentration. That is intentional — the
  country-level views the web requests (res 3/4/5) aggregate the covered cells,
  and "unknown population is not zero" is a property the demo deliberately
  exercises (here `unknown_population` ≈ 1.54M, the residents in cells with no
  concentration estimate).
- `population_weighted_pm25`, `residents_above_threshold`, and
  `covered_population` are **non-null** in demo mode. For the exact figures on
  your data, run `verify-publication`.

---

## 4. Scheduled publication

`.github/workflows/pipeline.yml` runs
`python -m app.pipeline.run`, which now publishes the v2 run as its final
stage, then `python -m app.cli verify-publication` to confirm the run is
readable. A non-zero exit from either step turns the workflow red.

Configuration:

| Name | Kind | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | secret | Direct (non-pooler) Postgres connection string. |
| `PIPELINE_DEMO_MODE` | variable | `true` (default) publishes the deterministic demo run; `false` runs live. |
| `OPENAQ_API_KEY` | secret | Only used when `PIPELINE_DEMO_MODE=false`. |
| `FIRMS_MAP_KEY` | secret | Optional. Without it the fire stage is skipped and fire features stay null. |
| `STATIC_FEATURES_URL` | variable | Optional. A URL for the versioned static-cell artifact; the workflow downloads it and sets `STATIC_FEATURES_PATH`. |
| `TRAFFIC_FEED_URL` | variable | Optional. A URL for the licensed traffic JSONL feed; same treatment. |
| `TRAFFIC_FEED_SOURCE` / `_PRODUCT` / `_VERSION` / `_ATTRIBUTION` / `_LICENSE` | variable | Provenance recorded with a licensed traffic import. Defaults say "unknown" on purpose. |

### 4.1 Live feature inputs

The synthetic scenario has always passed population, land use, fires, and
traffic to `FeatureBuilder`; the live path used to pass grid, sensors, and
weather only, so every published live vector had null static and environmental
fields. The live path now collects, in this order, and reports each one:

| Source | Store | Needs | Freshness |
| --- | --- | --- | --- |
| **Static cells** (population, road lengths, land cover) | `static_cell_feature`, keyed by `dataset_id` | `STATIC_FEATURES_PATH` (a versioned JSON artifact) | `STATIC_FEATURES_MAX_AGE_DAYS` (default 400) |
| **Sensor observations** | `sensor_reading` | `OPENAQ_API_KEY` | `INGEST_MAX_READING_AGE_HOURS` |
| **Weather observations** | `weather_reading` | none (Open-Meteo free tier) | as above |
| **Forecast weather** | `weather_forecast` | none | `WEATHER_FORECAST_HOURS` ahead (default 6) |
| **FIRMS detections** | `fire_hotspot` | `FIRMS_MAP_KEY` | `FIRMS_STALE_AFTER_HOURS` (default 6) |
| **Licensed traffic** | `traffic_observation` | `TRAFFIC_FEED_PATH` | `TRAFFIC_STALE_AFTER_HOURS` (default 2) |

**Forecast weather for future horizons.** `weather_forecast` is a separate table
from `weather_reading` because a forecast's *issue* time and *valid* time
differ. The publication query filters `issued_at <= prediction time`, so a
horizon can only be described by a forecast that already existed when the
prediction was made — no lookahead, and no observation quietly standing in for a
+6h forecast. When the newest usable weather for a horizon is materially older
than the target hour, the snapshot carries the `weather_forecast_gap` warning
instead of pretending the gap is not there.

### 4.2 Missing, stale, failed, and valid zero are different things

Every source is classified before use, and the classification is printed in the
`publish_v2` stage summary and carried on the published run:

| State | Meaning | Feature effect |
| --- | --- | --- |
| `present` | The source answered and rows are inside the freshness window | values used |
| `empty` | The source answered and reported **nothing** | a **valid zero** (`fire_frp_upwind_mw = 0.0`), *not* a gap |
| `stale` | Rows exist but all are past the freshness window | features null, source reported |
| `missing` | Never configured, or no dataset for these cells | features null, source reported |
| `failed` | The last ingestion attempt errored | features null, source reported with its error |

A per-cell absence is still per-cell: a dataset with no row for one cell leaves
that cell's `population`/`land_cover` in `missing_fields` while other cells are
fully populated. A measured standstill (`congestion_ratio = 0.0`) is a real
value, not missing.

**No demo values in a live run.** Live mode refuses a synthetic static artifact
at import, refuses a synthetic FIRMS dataset when collecting, and — as a last
line of defence — `LiveFeatureInputs.assert_no_synthetic_refs` refuses to publish
a `live` run that carries any `kind: synthetic` dataset ref. A live run also
fails closed when there is no observed current PM2.5 (below).

### 4.3 Live mode fails closed

In live mode `publish_from_state` builds snapshots from the persisted state and
publishes them as a `live` run. If there is no observed current PM2.5 with
supporting stations, `PredictionPublicationService` (via
`assert_live_snapshots_available`) **refuses** the publication. The
`publish_v2` stage then reports a failure and the workflow exits non-zero.
A failed live source therefore always produces a visible failure or a degraded,
explicitly labelled run — never an unlabeled synthetic replacement.

### 4.4 Partial upstream failure, demonstrated

`backend/tests/test_live_publication_inputs.py` is the executable version of all
of this, driven by committed fixtures and no network:

| Test | Demonstrates |
| --- | --- |
| `test_offline_fixture_run_carries_every_live_input` | The full live feature set, with the vector, source timestamps, coverage, and quality flags printed |
| `test_valid_zero_is_not_missing` | A completed FIRMS pull with no detections → `empty`, `frp = 0.0` |
| `test_failed_fire_source_stays_null` | A failed pull → `failed`, fire features **null**, run still publishes |
| `test_unconfigured_traffic_is_missing_not_zero` | No feed → `missing`, ratio **null** |
| `test_stale_traffic_is_reported_and_unused` | Out-of-window rows → not used, feature null |
| `test_live_mode_refuses_synthetic_static_data` | A synthetic artifact → refused, population null |
| `test_live_mode_refuses_a_synthetic_dataset_ref` | Any synthetic ref → publication refused |
| `test_future_horizon_uses_a_forecast_issued_before_prediction_time` | +3h described by a pre-issued modeled forecast |
| `test_forecast_issued_after_prediction_time_is_not_used` | A later-issued forecast is invisible; the horizon falls back and is flagged |

Run them with the transcript:

```bash
cd backend
python -m pytest -s tests/test_live_publication_inputs.py
```

#### The offline fixture run, verbatim

```
--- offline fixture run: published live run -----------------------
run_id=prediction-fixture mode=live cells=3
summary=published live run prediction-fixture (cells=3, results=21, coverage=0.73) sources:
  (static=present, 3 rows, newest=2026-06-01T00:00:00+00:00,
   sources=worldpop-india-2020,osm-roads-india-2025,esa-worldcover-india-2024)
  | (fires=present, 1 rows, newest=2026-09-24T08:00:00+00:00)
  | (traffic=present, 3 rows, newest=2026-09-24T08:20:00+00:00)
  | (weather_forecast=present, 18 rows, newest=2026-09-24T08:40:00+00:00)
  +0h valid_at=2026-09-24T09:00:00+00:00 wind_u_ms=0.32 wind_v_ms=0.12 rain_1h_mm=0.0
       population=41200.0 built_up=0.62 fire_frp_mw=12.5 traffic_ratio=0.1625
       coverage=0.950 stations=1 max_age_h=1.0 missing=[] warnings=[]
  +1h valid_at=2026-09-24T10:00:00+00:00 wind_u_ms=4.2286 wind_v_ms=1.5391 rain_1h_mm=0.2
       population=41200.0 built_up=0.62 fire_frp_mw=12.5 traffic_ratio=0.1625
       coverage=0.950 stations=1 max_age_h=1.0 missing=[] warnings=[]
  ... +2h .. +6h use the modeled forecast (issued 08:40Z, i.e. before the 09:00Z run) ...
  +6h valid_at=2026-09-24T15:00:00+00:00 wind_u_ms=4.2286 wind_v_ms=1.5391 rain_1h_mm=0.2
       population=41200.0 built_up=0.62 fire_frp_mw=12.5 traffic_ratio=0.1625
       coverage=0.950 stations=1 max_age_h=1.0 missing=[] warnings=[]
```

The fixture inputs are committed at
`backend/tests/fixtures/live_inputs/static_cells.json` (three cells, including
one with a genuine **zero** population) and `traffic.jsonl` (a licensed
congestion sample, including a near-standstill).

### 4.5 A documented live-mode run

```bash
cd backend
export DATABASE_URL='postgresql://…'          # direct, non-pooler
export DEMO_MODE=false
export OPENAQ_API_KEY='…'                     # sensors
export FIRMS_MAP_KEY='…'                      # fires (optional)
export STATIC_FEATURES_PATH='var/inputs/static_cells.json'
export STATIC_FEATURES_MAX_AGE_DAYS=400
export TRAFFIC_FEED_PATH='var/inputs/traffic.jsonl'
export TRAFFIC_FEED_SOURCE='licensed-sample'
export TRAFFIC_FEED_PRODUCT='sampled road speeds'
export TRAFFIC_FEED_VERSION='2026-09'
export TRAFFIC_FEED_ATTRIBUTION='<operator>'
export TRAFFIC_FEED_LICENSE='<licence terms>'
export WEATHER_FORECAST_HOURS=6
export WEATHER_FORECAST_MAX_LOCATIONS=400

alembic upgrade head
python -m app.pipeline.run          # stages print what each source did
python -m app.cli verify-publication
```

A healthy live run prints, in order:

```
[OK  ] static_features: static dataset static:<hash> (…): rows=<n> available_at=…
[OK  ] sensor_ingestion: fetched=… saved=…
[OK  ] weather_ingestion: fetched=… saved=…
[OK  ] weather_forecast: rows=… cells=…/… +6h issued=…
[OK  ] fire_ingestion: fetched=… saved=… stale=… newest=…
[OK  ] traffic_ingestion: samples=… saved=… stale=…
[OK  ] grid_computation: cells=… sensors_used=…
[OK  ] forecasting: cells=… generated=…
[OK  ] alert_generation: cells_evaluated=… alerts_created=…
[OK  ] publish_v2: published live run prediction-features-… (cells=…, results=…, coverage=0.73) sources: (static=present, …) | (fires=present, …) | (traffic=present, …) | (weather_forecast=present, …)
```

With an optional source unconfigured, its stage says so and the run still
publishes:

```
[OK  ] static_features: STATIC_FEATURES_PATH is not set - population/land cover stay null
[OK  ] fire_ingestion: FIRMS_MAP_KEY is not set - fire features stay null (not zero)
…
[OK  ] publish_v2: … sources: (static=missing, 0 rows, no static-cell dataset is available for this run) | (fires=missing, 0 rows, no FIRMS ingestion has run for this region) | …
```

### 4.6 Which live sources still need credentials or datasets

| Source | Credential | Dataset | If absent |
| --- | --- | --- | --- |
| Sensors (OpenAQ) | `OPENAQ_API_KEY` | none | Stage fails; live publication fails closed (no observed PM2.5) |
| Weather observations | none | none | Stage fails if Open-Meteo is unreachable |
| **Forecast weather** | **none** | none | Runs everywhere; if it fails, horizons fall back to the latest observation and are flagged `weather_forecast_gap` |
| **FIRMS** | `FIRMS_MAP_KEY` (free NASA key) | none | Stage skipped; `fires=missing`; fire features null |
| **Static cells** | none | **required** — a preprocessed, licensed population/road/land-cover artifact per H3 cell | Stage skipped; `static=missing`; population, roads, land cover, and exposure are null |
| **Traffic** | none | **required** — a licensed sampled-speed feed in the JSONL contract | Stage skipped; `traffic=missing`; congestion ratio null |

So a live deployment can run today with only `OPENAQ_API_KEY`; adding
population/exposure and traffic features needs the two **datasets** above, not
another API key.

---

## 5. Remaining limitations

- **No scheduler in the app.** The GitHub Actions workflow is the scheduler;
  there is no in-process cron. Scheduled workflows only run on the default
  branch.
- **Demo run cadence.** In demo mode each pipeline run publishes a run keyed to
  the scenario's replay hour. Repeated runs within the same replay hour
  re-publish the same immutable run id (idempotent); a changing replay hour
  produces a new run, so a long-lived demo database accumulates one run per
  distinct replay hour.
- **Population provenance label.** Demo runs report
  `population_dataset_version: null` because the synthetic dataset ref does not
  name a population product. The values are present; only the version label is
  absent.
- **Station coverage.** Only station-covered cells carry an observed
  concentration; exposure counts the rest as `unknown_population` rather than
  inferring a value.
- **PDI is heuristic.** The published `pdi` is the existing heuristic score,
  not a calibrated model output.
- **No static-cell artifact ships with the repo.** A live deployment must supply
  its own licensed population/road/land-cover artifact; until it does, the live
  run reports `static=missing` and exposure is null. There is no bundled
  fallback, on purpose.
- **Traffic is file-based.** The licensed feed is imported from a JSONL file; no
  traffic vendor is contacted. A continuously updated licensed source would need
  its own provider implementation.
- **FIRMS detections are not confirmed fires.** They are satellite detections
  with a confidence class; the fire features are a signal, not a ground truth.
- **Forecast weather is a single provider.** Open-Meteo only, with no ensemble
  spread; `confidence` for a horizon therefore reflects data coverage, not
  forecast uncertainty.
