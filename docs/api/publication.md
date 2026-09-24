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

### Live mode fails closed

In live mode `publish_from_state` builds snapshots from the persisted state and
publishes them as a `live` run. If there is no observed current PM2.5 with
supporting stations, `PredictionPublicationService` (via
`assert_live_snapshots_available`) **refuses** the publication. The
`publish_v2` stage then reports a failure and the workflow exits non-zero.
A failed live source therefore always produces a visible failure or a degraded,
explicitly labelled run — never an unlabeled synthetic replacement.

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
