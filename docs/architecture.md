# Architecture

Approved design for the pollution intelligence MVP, after the simplification
review. This document describes the target architecture and is kept in sync
with what has actually been built as each layer lands.

**Database schema note:** the schema below (`sensor_reading` /
`weather_reading` / `grid_state` / `forecast` / `alert`) is the one actually
implemented, given directly as the entity list for the database layer. It
supersedes an earlier draft in this doc (`region` / `station` / `observation`
/ `model_run` / `cell_value`), which was never built. The differences are
mostly naming; the "long format, one row per pollutant" principle carries
over (`sensor_reading.pollutant` is a plain string, not an enum, precisely
so a new pollutant is a data change, not a migration).

## Shape

**Contract note:** the original plan below was a `worker` *container*
running the pipeline continuously/on a schedule inside Docker, alongside
`db` and `api`. What's actually built is `python -m app.pipeline.run` —
a manually-triggered batch script sharing the `api` image, not a
standing container of its own (see "Pipeline (implemented)" further
down). The data flow it runs is otherwise as originally shaped:

```
  frontend (host, Vite)  ──HTTP /api/v1 (OpenAPI, JSON)──►  api container
                                                                   │ read
                                                             PostgreSQL+PostGIS
                                                                   ▲ write
  python -m app.pipeline.run ─ pipeline:                            │
     sources ──► store(raw) ──► build inputs(QC) ──► Nowcaster ──► Forecaster
                                                       ──► PDI ──► alerts ──► store(run)
```

- Two containers (`db`, `api`) plus a manually-run pipeline script sharing
  the `api` image — not the originally-planned third `worker` container.
  Turning it into one (a long-running process on a timer, replacing the
  manual trigger) is a listed extension point, not a structural change.
- Replaceable interfaces: pollution source, weather source, nowcaster,
  forecaster. Implementations are selected by name in config.
- Fixed choices, not abstracted: PostgreSQL/PostGIS, H3, FastAPI.

## Module responsibilities → directory mapping

The approved design names modules `core / grid / sources / model / alerts /
store / pipeline / api`. This repo's directory structure (matching what was
requested for the scaffold) maps onto it as follows:

| Directory | Responsibility | May import |
|---|---|---|
| `app/core` | Settings, cross-cutting config | nothing internal |
| `app/domain` | Pure domain types (`SensorReading`, `WeatherReading`, `GridState`, `Forecast`, `Alert`, fire hotspots and traffic observations), repository/provider/estimator/PDI/dispersion Protocols (ports), H3 helpers. No I/O. | `app/core` |
| `app/models` | SQLAlchemy Core table definitions (the schema) | `app/core`, `app/domain` (only for the `AlertSeverity` column type) |
| `app/db` | Engine/session management, and `app/db/repositories/*` — concrete SQLAlchemy implementations of the domain repository Protocols | `app/core`, `app/domain`, `app/models` |
| `app/ingestion` | OpenAQ/Open-Meteo adapters, NASA FIRMS VIIRS NRT adapter, and a normalized, license-explicit traffic sample importer; shared retry policy in `http.py` | `app/core`, `app/domain` |
| `app/services` | Business logic: per-resource read services (`SensorService`, `GridService`, `CellService`, `AlertService`, …) plus demo fallback, sensor/weather ingestion, M5 environmental-source ingestion, `GeospatialService`, pollution estimators/models, and `ForecastingService` | `app/core`, `app/domain`, `app/ingestion`, `app/models`, `app/db` |
| `app/api` | FastAPI routes (thin — call a service, shape the response), Pydantic schemas, error handling, dependency wiring | `app/core`, `app/domain`, `app/db` (dependency wiring only, see `app/api/deps.py`), `app/services` |
| `app/pipeline` | `python -m app.pipeline.run`: the composition root for the full OpenAQ→...→alerts pipeline (see below). A second composition root alongside `app/main.py`/`app/cli.py`, but a real directory (unlike those two files), so it's a genuine layer here, not exempt | `app/core`, `app/domain`, `app/models`, `app/db`, `app/ingestion`, `app/services` |

Domain code depends only on `app/core` and never on SQLAlchemy: repositories
are consumed through the `app.domain.repositories` Protocols, so a service
typed against those interfaces can be tested with an in-memory fake instead
of a database, and the storage backend could be replaced without touching
anything above `app/db`.

Import direction is enforced by `backend/tests/test_architecture.py` (relative
imports are banned by ruff so the check can't be bypassed). `app/main.py` is the
composition root and may import any layer.

## Data flow (implemented)

**Contract note:** this originally described a hypothetical `model_run`
row with `running`/`succeeded`/`failed` status and one atomic
all-or-nothing transaction — never built that way, and superseded by
what's actually below (same situation as the API section's own contract
note). What was actually built persists incrementally, per stage, not
atomically: a stage's own persistence failure is that stage's reported
failure, not a rollback of the whole run, and there is no `model_run`
table.

1. `t0` = `datetime.now(UTC)` when `python -m app.pipeline.run` starts —
   one shared timestamp threaded through every stage of that run (never
   read from a clock again mid-run, so a run is reproducible given its
   inputs).
2. Ingest OpenAQ (PM2.5) and Open-Meteo (weather) for the configured
   bounding box, each independently — either can fail without affecting
   the other or aborting the run (see "Pipeline" below).
3. Estimate PM2.5 per H3 cell (`IDWPollutionEstimator`) from recent
   `SensorReading` rows, fold in a PDI score per cell (`HeuristicPDIModel`),
   persist the combined `GridState` rows.
4. Forecast 1h/3h/6h (`DeterministicH3DispersionModel`) from the just-
   written `GridState` + latest `WeatherReading` rows, persist `Forecast`
   rows.
5. Generate `Alert` rows from simple PM2.5 threshold rules
   (`AlertGenerationService`), deduplicated against still-active alerts.
6. Print a per-stage pass/fail report; exit non-zero if any stage failed.
   The API keeps serving whatever was persisted by the most recent
   successful run of each stage — there is no "roll back to the previous
   run" concept, since each stage's table already only ever holds the
   latest state per cell (`GridState`) or is naturally additive
   (`Forecast`, `Alert`).

## Database (implemented)

All tables live in `app/models/tables.py` (the schema's reference
definition) and `alembic/versions/*.py` — `0001_initial_schema.py`'s
`CREATE TABLE`s plus one `ALTER TABLE` migration per schema change since
(hand-written to match `tables.py` — see that file's docstring for why
there's no autogenerate here, and `0002_weather_temp_humidity.py`'s
docstring for why a schema change is always a *new* migration, never an
edit to an old one, once a real database might exist at that revision —
and for why every revision id must stay at or under 32 characters,
alembic's own `alembic_version.version_num` column width).
Timestamps are always `timestamptz`, written and read as UTC; the app layer
rejects naive or non-UTC datetimes before they ever reach SQL
(`app.domain.types._require_utc`).

| Table | Purpose | Key / notable indexes |
|---|---|---|
| `sensor_reading` | Raw pollutant readings from stations. `pollutant` is a plain string, not an enum, so a new pollutant is a data change | PK `id`; unique (`source`, `external_sensor_id`, `pollutant`, `measured_at`); GiST index on `geom`; btree on (`pollutant`, `measured_at`) |
| `weather_reading` | Weather sample for one H3 cell | PK `id`; unique (`h3_cell`, `measured_at`); btree on `h3_cell` |
| `grid_state` | Current pollution state of one cell at one time (PM2.5, PDI, confidence, wind) | PK (`h3_cell`, `timestamp`); upserted, not appended; `pm25`/`pdi`/`wind_speed`/`wind_direction` are nullable — `confidence` is the only pollution-related field that's always present (0.0 means "no evidence") |
| `forecast` | Predicted PM2.5 for one cell at a future time, tagged with the horizon and the run that produced it | PK `id`; unique (`h3_cell`, `generated_at`, `forecast_hours`) |
| `alert` | A pollution alert for one cell, plus the current_pm25/forecast_pm25/forecast_hours/confidence context it was raised with (all nullable — never fabricated) | PK `id`; `severity` is a plain-string column whose CHECK constraint is generated from the `AlertSeverity` enum, not hand-duplicated |

`sensor_reading` and `weather_reading` also store a derived `geom
geography(Point,4326)` column with a GiST index, for future spatial queries
(e.g. "stations within this bbox"). It is write-only from the app's side —
domain objects only ever carry plain `latitude`/`longitude` floats, never a
geometry — so PostGIS is used for indexing without leaking into the domain
layer.

H3 cell strings (`weather_reading`, `grid_state`, `forecast`, `alert`) are
validated against `H3_RESOLUTION` (env var, default 8) at the repository
layer (`app.domain.h3_grid.assert_valid_cell`), not in the dataclasses
themselves — the dataclasses are pure and have no notion of configuration,
so this check belongs at the persistence boundary.

## API (implemented)

**Contract note:** the endpoint list below (plain JSON under `/api/v1`) is
what was actually specified and built. It supersedes an earlier draft in
this doc (`/meta`, GeoJSON polygons from `/grid`, `/stations`) which was
never built. GeoJSON may still make sense once the frontend map is wired
up — nothing here forecloses adding it as an alternative representation
later, it's just not what exists today.

| Endpoint | Returns | Called by |
|---|---|---|
| `GET /health` | API liveness — never touches the database | infra only — `docker-compose.yml`'s healthcheck and the deploy smoke tests; no client calls it |
| `GET /health/ready` | PostgreSQL + PostGIS readiness, 200/503 | infra only — `docker-compose.yml` waits on this, and `docs/GO_LIVE.md` checks it after a deploy |
| `GET /api/v1/sensors` | Latest reading per sensor | **nothing** - raw ingestion audit trail, see below |
| `GET /api/v1/weather?resolution=&min_lat=&min_lon=&max_lat=&max_lon=` | Latest weather per cell | **nothing** - superseded by `GET /api/v2/weather` |
| `GET /api/v1/grid/current?resolution=&min_lat=&min_lon=&max_lat=&max_lon=` | Current `GridState` per cell | **nothing** - superseded by `GET /api/v2/grid/current` |
| `GET /api/v1/grid/forecast?hours=1\|3\|6&resolution=&min_lat=&min_lon=&max_lat=&max_lon=` | Latest `Forecast` per cell at that horizon | **nothing** - superseded by `GET /api/v2/grid/forecast` |
| `GET /api/v1/cells/{h3_cell}?resolution=` | Current state + all forecasts + weather for one cell | **nothing** - superseded by `GET /api/v2/cells/{h3_cell}` |
| `GET /api/v1/alerts` | Alerts created within `ALERT_ACTIVE_LOOKBACK_HOURS` (default 24h; see `app/services/alerts.py`) | **nothing** - superseded by `GET /api/v2/alerts` |
| `GET /api/v1/reports` | Fire/burning reports within `FIRE_REPORT_MAX_AGE_HOURS` | web + app |
| `POST /api/v1/reports` | Store a citizen report of an active fire/burning event | web + app |
| `GET /api/v1/fires?since_hours=&min_lat=&min_lon=&max_lat=&max_lon=` | Stored NASA FIRMS detections, worst FRP first, capped | web |
| `GET /api/v1/tiles/gibs/{layer}/{z}/{y}/{x}?date=` | One proxied NASA GIBS WMTS tile (image, not JSON) | web |
| `GET /api/v1/tiles/no2/{z}/{y}/{x}` | One proxied Sentinel-5P NO2 WMS GetMap tile (image) | web |

**Who calls what, and why the legacy rows are still here.** The `Called by`
column is the contract that keeps this from drifting again: it was written by
reading every path each client requests - `frontend/src/lib/api.ts` for the web
app and `partner_apps/air_health_flutter/lib/data/**` for the partner app -
not by assuming. The v1 grid/weather/cells/alerts routes are **kept but no
longer called by either client**: both moved to the versioned `/api/v2`
publication reads, and they stay in place because an already-released build of
the partner app may still request them. They are covered by
`tests/test_api_contract.py`, which pins the legacy v1 shape on purpose.
`GET /api/v1/sensors` is a different case: it is the only endpoint that names a
`source`/`external_sensor_id`, it exists as a raw ingestion audit trail rather
than as part of the map/grid contract, and neither client has a station UI to
read it with. The web's "Citizen Fire Reports" layer shows *human* reports
(`kind`, smoke slider, duration) and therefore reads `/api/v1/reports`, not
`/api/v1/sensors` - those are two different kinds of data, not two views of
one. Adding a station layer later is what would give `/api/v1/sensors` a
consumer; until then it is deliberate, documented dead weight.

Every response (`/health` excepted) is wrapped the same way:
`{"generated_at": <UTC ISO-8601>, "is_demo": bool, "data": ...}`. `data` is
a list for collection endpoints, a single object for `/cells/{h3_cell}`.
Internal database ids are never exposed — `h3_cell` (+ `timestamp` /
`generated_at` where relevant) already identifies a resource, and dropping
ids keeps the contract independent of the storage backend.

**Level of detail.** `resolution` and the four bbox params
(`min_lat`/`min_lon`/`max_lat`/`max_lon`) are optional on `/weather`,
`/grid/current`, and `/grid/forecast` — all four bbox params must be
given together or all omitted (`app.api.deps.get_bbox_query`), and
`resolution` alone (without a bbox) has no effect, matching the pre-LOD
"return whatever is persisted, unfiltered" behavior exactly. `/cells/{h3_cell}`'s
`resolution` isn't for scoping a read — it's required whenever `h3_cell`
came from a coarser (country/state-tier) LOD read, since a cell string
only means anything at the resolution it was minted at. This is what lets
a map frontend request only the resolution and area its current zoom
level can actually show — see `app.services.grid_query.resolve_cells` and
`frontend/src/lib/lod.ts`'s zoom→resolution mapping — rather than always
reading the whole configured region at full detail.

**Legacy v1 source-agnostic contract.** The v1 map/grid responses do not
name where a value came from — not "openaq", "open-meteo", "demo", nor
any future source (satellite retrievals, government sensor feeds, ...).
`is_demo` is the one exception, and it answers a different question
("is this illustrative or measured?"), not "which system produced this?".
`GET /api/v1/sensors`' `source`/`external_sensor_id` fields are the sole,
deliberate exception — that endpoint is a raw ingestion audit trail, not
part of the map/grid contract, and no client calls it (see the
route-to-consumer table above). Swapping
`app.ingestion.demo` for a real provider, or adding a new one, never
requires a frontend change: `app.services.demo_data` and every real
`*Provider` implementation both terminate in the exact same domain types
(`GridState`, `Forecast`, `WeatherReading`), which `app/api/schemas.py`
serializes identically regardless of which one produced them. V2 adds the
explicit provenance contract described below. See `tests/test_api_contract.py`
for tests pinning the legacy v1 contract.

**Environmental publication API (M4):** the versioned /api/v2 routes serve
immutable prediction runs alongside the unchanged legacy routes. Collection
and detail envelopes carry run_id, mode, is_demo, attribution, and coverage
where applicable. Clients resolve GET /api/v2/meta once, then pass
latest_run_id to current, forecast, weather, alert, and cell-detail reads so
those views cannot drift onto different hourly publications during refresh.

| Endpoint | Returns | Called by |
|---|---|---|
| GET /api/v2/meta?run_id= | Run identity, mode, resolution and forecast anchors | web + app (both resolve the pointer before their other reads) |
| GET /api/v2/grid/current?run_id=&resolution=&bbox | Concentration, centroid, provenance and population exposure | web + app |
| GET /api/v2/grid/forecast?hours=&run_id=&resolution=&bbox | Published anchors and 15-minute interpolations | web + app |
| GET /api/v2/cells/{h3_cell}?run_id=&resolution= | Current, anchor forecasts, weather, static features and exposure | web only (the app has no per-cell detail read) |
| GET /api/v2/weather?hours=&run_id=&resolution=&bbox | Run-pinned weather and source versions | web + app |
| GET /api/v2/alerts?run_id= | Alerts derived from that publication | web + app |
| GET /api/v2/exposure?hours=&threshold_pm25=&run_id=&resolution=&bbox | Population-weighted concentration and covered/unknown population | **nothing** - see below |

`/api/v2/exposure` is the aggregate roll-up (one summary for the requested
scope), and neither client calls it. That is not because exposure is unused -
both clients read the *per-cell* `exposure` field that rides along on
`/api/v2/grid/current`, `/api/v2/grid/forecast` and `/api/v2/cells/{h3_cell}`,
and the web renders it in the drawer and as its "Exposure" map metric. The
summary endpoint is for a caller that wants one number for a region rather
than a field of cells: a future summary card in the web UI, or an external
consumer. Documented rather than deleted, because it is the only route that
answers that question.

Forecast requests may use 15-minute steps. Values between stored anchors
are linearly interpolated and identify interpolated-between-published-anchors
in metadata; calibrated intervals are null for those values. Concentration
is area-weighted when aggregating native cells to parents; exposure is
population-weighted separately. The API rejects upscaling above native
resolution and does not invent missing native-cell coverage.

The command python -m app.cli demo-features --profile tiny-ci --out
feature-snapshot.json creates a deterministic fixture with current plus
hourly anchors through six hours. The command python -m app.cli
prediction-publish --input feature-snapshot.json --feature-run-id
hourly-2026-09-22T10Z --mode demo stores the feature run and atomically
publishes its immutable prediction run. Invoke this one-shot publisher from
an external hourly scheduler with a fresh feature export. Synthetic
provenance is rejected for live mode; production must supply real, as-of
feature exports rather than relabeling demo data. The command does not
install or manage a scheduler.

**Environmental source ingestion (M5):** `python -m app.cli ingest-fires`
fetches bounded NASA FIRMS VIIRS NRT detections, stores raw/versioned events,
and records per-run completeness, freshness, duplicate and invalid-row
metrics. A successful complete empty feed is distinct from a failed or
malformed request. `ingest-traffic` accepts normalized JSONL samples only
with explicit source, version, attribution and license metadata; no paid or
unlicensed traffic vendor is assumed. Both sources remain disabled as
prediction inputs. `evaluate-feature-group` compares fire or traffic
predictors against a control using the same observed-label temporal split and
never promotes automatically. See [M5 ingestion and evaluation](M5_FIRES_AND_TRAFFIC.md)
for feed fields and operator steps.

**Model operations (M6):** `model-validate` checks a live candidate's immutable
artifact digest and M3 held-out promotion gates; when fire/traffic features are
used, matching M5 incremental reports are mandatory. `model-promote` and
`model-rollback` explicitly switch the active region/horizon artifact while
retaining the previous version. `model-monitor` summarizes labelled prediction
errors, input coverage, feature missingness, and standardized feature drift.
Drift prompts investigation only and never triggers automatic replacement.
See [M6 operations](M6_OPERATIONS.md) for the offline workflow and monitoring
input contract.

**Demo-data fallback.** Every endpoint falls back to small, deterministic
seed data (`app/services/demo_data.py`) whenever its repository query
returns nothing — before the pipeline has ever run, or for a region/cell
it doesn't cover — never unconditionally, so real rows take over
automatically once `python -m app.pipeline.run` (see "Pipeline" below)
has written them. Every such response sets `is_demo: true`; the frontend
should treat that as "this is illustrative, not measured" (e.g. a
banner), not silently show fabricated numbers as real air-quality data.

**Errors** are always `{"error": {"code": "...", "message": "...", "details": [...]?}}`
(`app/api/errors.py`), for both explicitly-raised and unhandled exceptions.
`code` is one of `bad_request`, `not_found`, `validation_error`,
`internal_error`, or a generic `http_error` fallback. `/cells/{h3_cell}`
returns 422 for a malformed or wrong-resolution H3 cell, 404 for a
well-formed cell with no data at all (real or demo).

## Geospatial (implemented)

`app.domain.h3_grid` is the only module that imports the `h3` library
directly — every H3 primitive (point → cell, cell → boundary, neighbors,
region coverage, the two-resolution grouping weather sampling uses) lives
there as a plain function taking a resolution explicitly, so it stays
testable without settings.

`app.services.geospatial.GeospatialService` is the facade everything else
uses: constructed once with a resolution (normally `Settings.h3_resolution`,
read at the composition root — see `app/cli.py`), it wraps those functions
so callers never call `h3_grid` (or the `h3` library) directly and never
have to pass `resolution=` on every call. It provides:

| Method | Purpose |
|---|---|
| `cell_for_point(lat, lon)` | Lat/lon → H3 cell |
| `cell_for_reading(reading)` | Which cell a `SensorReading`'s coordinates fall into (computed on demand — `sensor_reading` has no `h3_cell` column; see below) |
| `cell_to_polygon(cell)` | Boundary ring as `list[Coordinate]`, open |
| `cell_to_geojson_polygon(cell)` | The same ring as a GeoJSON `Polygon` geometry: `[lon, lat]` order, closed |
| `cell_to_feature(cell)` | A GeoJSON `Feature` (`h3_cell`/`resolution` as properties) |
| `neighbors(cell, k=1)` | Cells within `k` grid steps, excluding the cell itself |
| `region_coverage(bbox)` | Every cell at this resolution covering bbox — the MVP region's grid |
| `region_geojson(bbox)` | `region_coverage` as a GeoJSON `FeatureCollection` |

**Why `cell_for_reading` doesn't persist anything:** a station's raw
coordinates are the source of truth; which H3 cell it falls into is
derived and would silently change meaning if `H3_RESOLUTION` were ever
reconfigured. This method exists for whatever builds `GridState` from
nearby stations later (the not-yet-implemented nowcaster) to bucket
readings into cells on demand, not to add a stored column.

**Inspecting the grid:** `python -m app.cli export-grid` writes the
configured region's H3 coverage as a GeoJSON `FeatureCollection` to a file
(default `grid.geojson`), viewable directly in geojson.io or any GIS tool.
Chosen over a dev API endpoint because there's no auth system yet, and an
unauthenticated introspection route would be a permanent addition to the
API surface for what is purely a local development need — the CLI already
has two ingestion commands following exactly this pattern.

## Ingestion (implemented: OpenAQ for PM2.5, Open-Meteo for weather)

Two ports in `app.domain.providers`, both raising `ProviderError` on
failure so `app.services.ingestion` handles either the same way:

- `PollutionDataProvider.fetch_readings(bbox, *, since) -> list[SensorReading]`
  — `app.ingestion.openaq.OpenAQProvider` is the only implementation, against
  OpenAQ v3 (`api.openaq.org/v3`, `X-API-Key` header).
- `WeatherProvider.fetch_weather(points: list[Coordinate]) -> list[WeatherSample | None]`
  — `app.ingestion.open_meteo.OpenMeteoProvider` is the only implementation,
  against Open-Meteo's free forecast API (no key required). Returns exactly
  one entry per input point, in order (`None` where unavailable) — providers
  commonly snap a requested point to their own model grid, so a caller can't
  reliably correlate results back to points by coordinate equality, only by
  position.

Adding CPCB, satellite pollution data, ECMWF, or a private sensor network
later means a new class implementing the relevant Protocol — nothing above
it (the ingestion service, the CLI) changes. Both adapters share one retry
policy (`app.ingestion.http.get_json`): exponential backoff on timeouts,
connection errors, and 429/5xx; never on 4xx, where retrying can't help.

**OpenAQ fetch flow**, chosen to avoid hardcoding OpenAQ's numeric parameter
id for PM2.5: `GET /v3/locations?bbox=...` returns locations in the box with
their sensors embedded (id + `parameter.name`), so which locations have a
PM2.5 sensor — and that sensor's id — is known from one call, matched by the
string `"pm25"`. Then one `GET /v3/locations/{id}/latest` per matching
location gets the actual value. A single flaky location (that call failing
after retries, or returning malformed data) is logged and skipped, not
fatal to the run; the initial `/locations` call failing is fatal (nothing
to iterate without it). A reading older than `INGEST_MAX_READING_AGE_HOURS`
is dropped as stale.

**Weather sampling**, the answer to "without making an unnecessarily large
number of API calls": weather varies far less over a city block than PM2.5
does, so it's fetched at `WEATHER_H3_RESOLUTION` (coarser than
`H3_RESOLUTION`, e.g. 5 vs. 8 by default — a difference of 2 levels cuts the
point count by roughly 49x) and fanned out to every fine `H3_RESOLUTION`
cell inside each sampled cell
(`app.domain.h3_grid.representative_sample_points`). Every representative
point for the whole configured bbox goes into **one** Open-Meteo request
(it accepts comma-separated multi-location lat/lon and answers with one
result per point, batched automatically past `OPEN_METEO_MAX_LOCATIONS_PER_REQUEST`
if needed) — that single fetched sample is then *reused* across every fine
`WeatherReading` row it represents, which is the caching/reuse mechanism
here rather than a separate response cache. `WeatherReadingRepository`'s
schema and validation are untouched by this: every stored row is still at
`H3_RESOLUTION`, just multiple rows sharing one set of values.
`boundary_layer_height` is only available as an hourly Open-Meteo variable,
not a "current" one, so it's matched to the current reading's hour and left
`None` if the model has no value for it — the "where available" case.
`temperature` and `humidity` (`temperature_2m`/`relative_humidity_2m`) are,
by contrast, in the same `current=` request as wind/precipitation, so they
come back with every sample; `WeatherSample`/`WeatherReading` still type
them as optional (matching `boundary_layer_height`'s "where available"
idiom) since not every provider/source will have them.

**Data hygiene and failure containment** (from the integration review):
OpenAQ timestamps are normalised to aware UTC in the adapter — a naive
value used to reach a comparison against an aware `since` and raise
TypeError, which is not a ValueError and so escaped every handler and
killed the whole run. Negative/sentinel (`-999`) and non-finite values are
dropped rather than stored as real concentrations, and the domain types
reject non-finite numbers outright (NaN passes every range check). A run
of consecutive per-location failures aborts the fetch instead of spending
`max_retries` requests on each of a hundred stations against a dead API,
and hitting the `OPENAQ_LOCATIONS_LIMIT` page size now logs a warning
rather than silently ingesting a truncated slice of the region. Weather
fan-out is capped by `WEATHER_MAX_CELLS`. Both services also contain a
provider that violates its Protocol (raises something other than
`ProviderError`, or returns the wrong number of samples), so one feed can
never take down the process that also ingests the other — see
`backend/tests/test_ingestion_resilience.py`.

**Duplicate prevention:** both `SensorReadingRepository.add()` and
`WeatherReadingRepository.add()` raise `DuplicateReadingError` — a
domain-level exception, not `sqlalchemy.exc.IntegrityError` — for an exact
repeat (sensor: source/external_sensor_id/pollutant/measured_at; weather:
h3_cell/measured_at), backed by each table's real unique constraint rather
than a separate check-then-insert (which would race under concurrent runs).
Each ingestion service catches it per-item and keeps going, returning
counts (`fetched`, `saved`, `skipped_duplicates`) plus `errors`. Any other
persistence failure (e.g. the database is unreachable) stops that run
immediately rather than retrying every remaining item against a connection
that's already failed, and becomes an `errors` entry rather than an
uncaught exception — verified live by running `ingest-weather` against the
real Open-Meteo API with no database reachable: it fetched real data, then
reported the connection failure cleanly instead of crashing.

**Manual trigger:** `python -m app.cli ingest` (PM2.5) and
`python -m app.cli ingest-weather` (weather) each run one pass against the
bbox from `.env` and print a summary. There is no scheduler yet — these are
development commands, not the production path (that's the `worker`
container in [Shape](#shape), not yet built).

**Demo Mode (implemented):** `DEMO_MODE=true` swaps which providers get
built, nothing else. `app.ingestion.factory.build_pollution_provider`/
`build_weather_provider` are the single decision point — every caller
(`app.cli`, `app.pipeline.run`) asks the factory instead of importing
`OpenAQProvider`/`OpenMeteoProvider` directly or branching on the flag
itself, which is what keeps `if settings.demo_mode` out of every layer
above ingestion. In demo mode the factory returns
`app.ingestion.demo.DemoPollutionDataProvider`/`DemoWeatherProvider`
instead: a fixed hotspot-plus-background PM2.5 dataset (Delhi, matching
`INGEST_BBOX_*`'s default region — the two modules don't depend on each
other in code) and a steady westerly wind,
both ignoring `bbox`/`since` — Demo Mode always shows the same scenario
regardless of the configured region. `measured_at` is still stamped with
the real current time on every call, not a fixed timestamp, so the data
never ages out of the freshness window `GridComputationService` already
enforces; "deterministic" here means deterministic in content (same
coordinates/values every call), not in timestamp.

Everything from `GridComputationService` onward is completely unaware
this happened: it estimates, interpolates, disperses, and alerts on
whatever ended up persisted, exactly as it would for a real OpenAQ/
Open-Meteo fetch. The scenario's hotspot (280 µg/m³) and wind (6 m/s) are
chosen so a real pipeline run against it reliably produces a visible
hotspot, forecasts that visibly carry it downwind over +1h/+3h/+6h, and
at least one CRITICAL alert — via the real, unmodified rule-1 alert
threshold, not a special case. `backend/tests/test_demo_mode.py` runs the
real `GridComputationService`/`DeterministicH3DispersionModel`/
`AlertGenerationService` against this dataset end to end and asserts all
three.

Not the same concept as a response's `is_demo` field (see
[Configuration](#configuration) and [API](#api-implemented)): that flags
`app.services.demo_data`'s static illustrative fallback, shown only when
a repository query finds nothing at all. A Demo Mode pipeline run
produces real, computed, persisted rows through the real pipeline, so
`is_demo` stays `false` for them — identical to live mode.

## PM2.5 estimation (implemented: v1, IDW)

`app.domain.estimation.PollutionEstimator` is the port:
`estimate(grid: list[str], sensor_readings, *, timestamp) -> list[GridState]`,
one result per cell in `grid`, in order. `timestamp` is supplied by the
caller rather than read from a clock, so any implementation is a pure
function of its inputs — the same call always produces the same result,
which is what makes it testable without mocking time. Kriging, satellite
fusion, or an ML model can implement this same Protocol later; nothing
that calls a `PollutionEstimator` — an API endpoint, a future pipeline —
needs to change to use a different one.

`app.services.estimation.IDWPollutionEstimator` is the first
implementation: classic inverse-distance-weighted interpolation. For each
target cell, only sensor readings within `IDW_MAX_DISTANCE_KM` of the
cell's center (via `Coordinate.distance_km`, haversine) are considered; if
fewer than `IDW_MIN_SENSORS` qualify, the cell gets `pm25=None` and
`confidence=0.0` — never a value extrapolated from evidence that's too
sparse or too far away. Otherwise `pm25` is the IDW-weighted average
(weight `1/distanceᵖ`, `power` configurable on the class, default 2), and
`confidence` is a deterministic function of the nearest sensor's distance
and how many sensors were used, bounded to [0, 1].

**Divide-by-zero:** a sensor within 10 m of a cell center is treated as
coincident with it — its value is used directly, which is standard IDW
practice for an exact match, not a workaround bolted on to dodge the
singularity.

**Scope:** `pdi`, `wind_speed`, and `wind_direction` are always `None` from
this estimator — it only ever sees PM2.5 sensor readings and has no
forecast or weather data to derive them from. This is why `GridState`'s
non-identity fields are all `Optional`: the type honestly reflects that a
cell can have a pollution estimate without wind (this estimator, today) or
wind without a pollution estimate (nothing currently produces that
combination, but a weather-only cell in a sparse sensor network is a real
future case), rather than forcing every producer to invent values for
fields it has no basis for.

## PDI: Pollution Development Index (implemented: v0, heuristic)

**PDI is a heuristic pollution-pressure score, not a scientifically
exact measurement of emissions, absorption, or a modeled pollutant
budget, and it is deliberately independent of PM2.5** — the two can and
do diverge for the same cell. Every place it's surfaced — API docs, UI
labels, code comments — must describe it that way; this is a
triage/ranking score, not a physical quantity.

`app.domain.pdi.PDIModel` is the port: `calculate(cell_context:
CellContext) -> PDIResult`, one cell at a time (unlike `PollutionEstimator`,
which is grid-wide — a cell's PDI only ever depends on that cell's own
context, so there's no batching concern to design around).
`CellContext` carries the inputs available for one cell: `pm25` (the
current estimate, e.g. from `GridState.pm25`) plus three extension
points — `road_pressure`, `industrial_pressure`, and `vegetation_sink`
(a *sink*, not a pressure) — pre-normalized to `[0, 1]` by whatever
eventually produces them. `PDIResult` carries `pdi` (`None` if no factor
was available or every available factor is zero-weighted — never a
fabricated score) and `factors`, the *normalized* `[0, 1]` value of each
factor that actually contributed, keyed by name — not each factor's
weighted contribution — so a caller/UI can show which signals drove the
score. Exposed via `GET /api/v1/cells/{h3_cell}`'s `pdi_factors` field.

`app.services.pdi.HeuristicPDIModel` is the first (and so far only)
implementation. It normalizes `pm25` to `[0, 1]` by dividing by
`PDI_PM25_REFERENCE_UGM3` and clamping, normalizes/clamps the other
three factors defensively (they're expected to already be in `[0, 1]`),
then combines whichever factors are present as a weight-normalized
average using the *absolute value* of each configured weight:

```
pdi = 100 * sum(normalized_i * weight_i) / sum(abs(weight_i))
```

over only the present factors — so with only a PM2.5 estimate available
(the realistic v0 case for *real* data), `pdi` is driven entirely by it
regardless of the configured weight split, rather than being capped
below 100 because the other three aren't available yet. `PDI_PM25_WEIGHT`,
`PDI_ROAD_PRESSURE_WEIGHT`, and `PDI_INDUSTRIAL_PRESSURE_WEIGHT` default
non-negative; `PDI_VEGETATION_SINK_WEIGHT` defaults *negative*
(vegetation is a sink, so more of it pulls the index down, not up) — the
result is bounded to `[-100, 100]` by the absolute-value denominator
regardless of sign.

`app.services.demo_data`'s illustrative PDI (the `is_demo: true`
fallback) reads these same four `Settings` weights and reuses the same
formula, but populates all four factors — `industrial_pressure` from
proximity to an industrial-flagged city, `road_pressure` from proximity
to any city, `vegetation_sink` from a regional "greenness" value
independently authored per climate anchor (not derived from that
region's own PM2.5 number) — specifically so demo PDI diverges from demo
PM2.5 rather than reading as a rescaled copy of it.

**Wired into the real pipeline:** `app.pipeline.run` constructs
`HeuristicPDIModel` from `Settings` and passes it to
`GridComputationService`, which builds a `CellContext` per cell (today
only `pm25` — see below) and persists the result on `GridState.pdi`.

**Not yet done:** there is no real data source for `road_pressure`,
`industrial_pressure`, or `vegetation_sink` yet; the fields exist so a
future source is a matter of populating `CellContext`, not changing
`HeuristicPDIModel` or its caller. The real pipeline also doesn't
persist a per-cell factor breakdown anywhere, so `pdi_factors` is
`null` for real data in the API today (only the demo fallback has one).

## Dispersion / forecast model (implemented: v0, deterministic H3 box model)

**Not an atmospheric chemistry simulator.** A deliberately simple,
explainable box model: each H3 cell is a well-mixed box; every simulated
hour, its PM2.5 is reduced by a removal fraction (decay, boosted by
precipitation if available), then split between what stays and what's
transported into its immediate H3 neighbors, biased toward whichever
neighbor(s) lie closest to the wind's downwind bearing. It redistributes
and removes pollution that's already estimated to exist — it has no
emissions term, so a cell sitting on a continuous real source will trend
lower over a horizon than reality (that's closer to what PDI signals, not
this model's job).

`app.domain.dispersion.PollutionForecastModel` is the port:
`forecast(current_state: list[GridState], weather: list[WeatherReading],
hours: Sequence[int] = (1, 3, 6), *, generated_at) -> ForecastResult`.
The modeled domain is exactly the cells present in `current_state` — no
separate grid parameter, since the cells with a current estimate *are*
the cells being forecast. `generated_at` is caller-supplied, matching
`PollutionEstimator`/`PDIModel`'s determinism precedent. Every requested
horizon is the literal simulated state at that many hourly steps (the
3-hour result is 3 iterations of the same per-hour update used to reach
6 hours) — never a closed-form shortcut — so requesting several horizons
from one call is guaranteed internally consistent with each other.
`ForecastResult.domain_outflow_by_hour` is a diagnostic (not a modeling
input): the PM2.5 that left the tracked grid each hour because it was
transported toward a neighbor cell outside `current_state` — an explicit,
observable open-boundary loss rather than a silently-dropped number.

`app.services.dispersion.DeterministicH3DispersionModel` is the first (and
so far only) implementation:

- **Removal:** `removal_fraction = clamp(DISPERSION_DECAY_RATE_PER_HOUR +
  DISPERSION_WET_REMOVAL_RATE_PER_HOUR * clamp01(precipitation /
  DISPERSION_PRECIPITATION_REFERENCE_MM), 0, 1)` — precipitation only
  changes this term, never the directional transport split.
- **Transport:** `transport_fraction = DISPERSION_MAX_TRANSPORT_FRACTION *
  clamp01(wind_speed / DISPERSION_WIND_TRANSPORT_REFERENCE_MS)`, zero
  below `DISPERSION_CALM_WIND_THRESHOLD_MS` or when a cell has no weather
  reading at all (decay-only, no fabricated wind).
- **Neighbor selection:** every H3 neighbor within
  `DISPERSION_WIND_CONE_HALF_ANGLE_DEG` of the downwind bearing
  (`wind_direction + 180`, since `wind_direction` is meteorological
  "blowing from") gets a share, weighted linearly by closeness to
  dead-on-downwind — a cone (usually 1-2 neighbors), not a single
  nearest-bearing pick, so a small wind-direction change shifts weights
  continuously instead of flipping 100% of transport from one hex to the
  next (see `Coordinate.bearing_to`, added alongside `distance_km`).
- **Mass conservation:** every coefficient (removal fraction, transport
  fraction, each neighbor weight) is clamped into `[0, 1]`, and
  `DISPERSION_MAX_TRANSPORT_FRACTION < 1` caps transport regardless of
  wind speed — so the per-hour update is a substochastic linear map:
  total mass across the modeled domain can only decrease (removal) or
  leave through an open boundary, never increase, however extreme the
  input (e.g. a corrupted 500 m/s wind reading). Concentrations are
  floored at 0 defensively.
- **Confidence:** propagates through the same transport as a
  mass-weighted average (so a cell that mostly receives well-observed
  inflow isn't penalized just for having a low confidence itself), then
  discounted once per hour by `DISPERSION_CONFIDENCE_DECAY_PER_HOUR` and,
  for an hour where a cell had no weather, by
  `DISPERSION_MISSING_WEATHER_CONFIDENCE_PENALTY`.
- **Known simplification:** weather is held constant across the whole
  simulated horizon — there is no per-hour weather forecast feed yet.
  This is the single biggest gap versus reality, documented rather than
  hidden; a future `WeatherForecastProvider` would remove it without
  changing this model's structure.

`Forecast.predicted_pm25` is validated `>= 0` at construction (matching
`GridState.pm25`/`SensorReading.value`), with a matching DB `CHECK`
constraint — "values cannot become negative" is enforced at the type,
not just trusted from the model's arithmetic.

`app.services.forecasting.ForecastingService` is the pipeline
orchestration (same relationship to the model as `SensorIngestionService`
has to `OpenAQProvider`): reads `GridStateRepository.latest()` and
`WeatherReadingRepository.list_latest()`, runs the model for
`hours=(1, 3, 6)`, and persists every resulting `Forecast` via
`ForecastRepository.add()`. `python -m app.cli forecast` is the manual
development trigger, matching `ingest`/`ingest-weather`/`export-grid`.
Once this has been run at least once, `/api/v1/grid/forecast` and
`/api/v1/cells/{h3_cell}` serve the real, persisted rows instead of demo
data automatically — no change to `GridService`/`CellService`/the API
contract, since they already read from `ForecastRepository`.

## Pipeline (implemented): the complete vertical slice

`app/pipeline/run.py` (`python -m app.pipeline.run`) is the composition
root that runs every stage above, in order, for the configured region,
against one shared session/timestamp/bounding box: OpenAQ ingestion →
Open-Meteo ingestion → grid computation (PM2.5 + PDI) → forecasting →
alert generation. It is deliberately thin — no pollution/forecast/PDI
logic lives in it, only wiring — and every stage it calls is a small
orchestration service with its own test file, constructible with fakes
independent of the others:

| Stage | Service | Test file |
|---|---|---|
| Sensor ingestion | `SensorIngestionService` | `test_ingestion_service.py` |
| Weather ingestion | `WeatherIngestionService` | `test_ingestion_service.py` |
| Grid computation (PM2.5 + PDI) | `GridComputationService` (new) | `test_grid_computation_service.py` |
| Forecasting | `ForecastingService` | `test_forecasting_service.py` |
| Alert generation | `AlertGenerationService` (new) | `test_alert_generation_service.py` |

**`GridComputationService`** is the piece that ties PM2.5 estimation to
PDI: since `pm25` and `pdi` land on the *same* `GridState` row (see
"Database" above), it reads recent `SensorReading` rows, covers the
region with H3 cells (`GeospatialService`), runs `PollutionEstimator`
(`IDWPollutionEstimator`), calls `PDIModel.calculate` (`HeuristicPDIModel`)
once per resulting cell with `CellContext(h3_cell, pm25=state.pm25)`,
and upserts the combined result once per cell — not two separate
round trips to the same row.

**`AlertGenerationService`** is a very small rule-based alert engine —
**no machine learning**. It's not a Protocol/interface like the
estimator/PDI/forecast models — every rule is a plain, configurable
comparison, not a model, so there's no separate swappable abstraction
for it (yet; if that changes, it would move to `app/domain/`). For each
cell it walks four rules in priority order and raises the *first* one
that matches — a cell gets at most one alert per run:

1. **Threshold crossed now**: current PM2.5 ≥ `ALERT_WARNING_THRESHOLD_UGM3`
   / `ALERT_CRITICAL_THRESHOLD_UGM3` → `WARNING` / `CRITICAL`.
2. **Threshold crossed in the forecast**: no current exceedance, but some
   forecast horizon (earliest first) reaches a threshold → `WATCH`.
3. **Sharp increase**: current-to-forecast PM2.5 jump ≥
   `ALERT_SHARP_INCREASE_THRESHOLD_UGM3` at some horizon, independent of
   whether either value alone crosses a threshold → `WATCH`.
4. **High PDI + worsening forecast**: current PDI ≥
   `ALERT_PDI_HIGH_THRESHOLD` *and* a forecast horizon at least
   `ALERT_PDI_WORSENING_MIN_INCREASE_UGM3` above current PM2.5 → `WATCH`.
   A deliberately smaller bar than rule 3: paired with already-high
   pressure, even a modest uptick is worth flagging.

Severity encodes *when* a condition is or will be true, not just how
severe it is: only rule 1 produces `WARNING`/`CRITICAL`; every other rule
is `WATCH` regardless of which threshold a forecast value crosses, and a
current condition (rule 1) always takes priority over what a forecast
says about the same cell (rules 2-4 are only reached if rule 1 doesn't
match). A cell with an alert already created within
`ALERT_ACTIVE_LOOKBACK_HOURS` is skipped, so a persistent condition
doesn't spawn a new `Alert` row every pipeline run — the same window
`app.services.alerts.AlertService` (the `/alerts` read path) uses to
decide what counts as "active".

Every `Alert` carries the context it was raised with —
`current_pm25`/`forecast_pm25`/`forecast_hours`/`confidence`, alongside
the pre-existing `h3_cell`/`severity`/`message`/`created_at`/
`forecast_time` — never fabricated, so any of them can be `None`:
`current_pm25` is `None` only if the cell had no current estimate;
`forecast_pm25`/`forecast_hours` are `None` only if the cell had no
forecast at all (a rule-1 alert still attaches the *nearest* available
forecast horizon as informational trend context even though it isn't
what triggered the alert). `confidence` is the current estimate's own
confidence for a rule-1 alert, or the triggering forecast horizon's
confidence for rules 2-4 — whichever value the alert is actually about.
`forecast_time` stays a separate concept from `forecast_pm25`/
`forecast_hours`: it means "when the alerted condition itself occurs"
(`None` for a condition already true now), not "which forecast is
attached".

**Failure handling** (the explicit requirement behind this design): a
failed external data source is a clear, reported per-stage failure, not
a crash and not silent corruption. OpenAQ or Open-Meteo failing is
reported and the run's exit code reflects it, but every stage after it
still runs against whatever is already persisted — this is safe only
because every downstream stage already has a well-defined, tested
behavior for missing/stale/absent upstream data from earlier turns:
`IDWPollutionEstimator` returns `pm25=None`/`confidence=0.0` rather than
fabricating an estimate below `IDW_MIN_SENSORS`, and
`DeterministicH3DispersionModel` forecasts decay-only rather than
inventing wind for a cell with no weather reading. Only the database
itself being unreachable is not caught — it raises and aborts the whole
run, since no stage can produce a meaningful result without one.

Every stage is also runnable individually for local development via
`python -m app.cli <ingest|ingest-weather|forecast>` (grid computation
and alert generation have no standalone CLI command yet — only via the
full pipeline, since neither has an independent development need for one
the way ingestion/forecasting do).

## Configuration

- **Env vars** (`.env`): infrastructure — DB connection, ports, CORS, log
  level, H3 resolution (grid and weather-sampling), `DEMO_MODE` (see
  "Demo Mode" under Ingestion above), OpenAQ/Open-Meteo
  ingestion settings (API key where needed, base URL, timeout/retries, the
  shared ingestion bounding box), the level-of-detail read cap
  (`GRID_QUERY_MAX_CELLS`), IDW estimation thresholds
  (`IDW_MAX_DISTANCE_KM`, `IDW_MIN_SENSORS`), PDI weights
  (`PDI_PM25_REFERENCE_UGM3`, `PDI_PM25_WEIGHT`, `PDI_ROAD_PRESSURE_WEIGHT`,
  `PDI_INDUSTRIAL_PRESSURE_WEIGHT`, `PDI_VEGETATION_SINK_WEIGHT` — the
  last negative by default — also read by `app.services.demo_data`'s
  illustrative PDI), and dispersion/forecast model
  parameters (`DISPERSION_DECAY_RATE_PER_HOUR`,
  `DISPERSION_WET_REMOVAL_RATE_PER_HOUR`,
  `DISPERSION_PRECIPITATION_REFERENCE_MM`,
  `DISPERSION_MAX_TRANSPORT_FRACTION`,
  `DISPERSION_WIND_TRANSPORT_REFERENCE_MS`,
  `DISPERSION_CALM_WIND_THRESHOLD_MS`, `DISPERSION_WIND_CONE_HALF_ANGLE_DEG`,
  `DISPERSION_CONFIDENCE_DECAY_PER_HOUR`,
  `DISPERSION_MISSING_WEATHER_CONFIDENCE_PENALTY`), and alert-rule
  thresholds (`ALERT_WARNING_THRESHOLD_UGM3`, `ALERT_CRITICAL_THRESHOLD_UGM3`,
  `ALERT_SHARP_INCREASE_THRESHOLD_UGM3`, `ALERT_PDI_HIGH_THRESHOLD`,
  `ALERT_PDI_WORSENING_MIN_INCREASE_UGM3`, `ALERT_ACTIVE_LOOKBACK_HOURS`).
  See `.env.example`. A `model_validator`
  on `Settings` rejects `WEATHER_H3_RESOLUTION` finer than `H3_RESOLUTION`
  at startup (and another rejects `ALERT_CRITICAL_THRESHOLD_UGM3` at or
  below `ALERT_WARNING_THRESHOLD_UGM3`), rather than letting either fail
  confusingly deep inside a pipeline run.
- **Region config** (future `config/region.yaml`): domain tuning — QC
  thresholds. PDI, dispersion-model, and alert-threshold parameters moved
  out of this "future" list: they live in `.env` alongside every other
  tunable this app currently has (IDW's included), the same place they'd
  need to move out of once a real region-config file exists. The
  ingestion bounding box also lives in `.env` for now (`INGEST_BBOX_*`,
  shared by both `ingest` and `ingest-weather`), not this future file —
  it's config for the adapters, not the shared region concept `/meta` will
  eventually expose.

## Extension points

| Future change | What changes |
|---|---|
| New PM2.5 estimation model (Kriging, satellite fusion, ML) | New class implementing `PollutionEstimator` in `app/services/`; whatever calls `estimate()` — an API endpoint, a future pipeline — is unchanged |
| Real road-density / industrial-proximity data | Populate `CellContext.road_pressure` / `.industrial_pressure` (normalized to `[0, 1]`) wherever a `CellContext` is built; `HeuristicPDIModel` and its caller are unchanged |
| New dispersion/forecast model (higher-fidelity plume, ML) | New class implementing `PollutionForecastModel` in `app/services/`; `ForecastingService`, `app.cli forecast`, and the API are unchanged |
| New/smarter alert rules (e.g. multi-cell trend, escalation tracking) | Change `AlertGenerationService` in `app/services/`; `app.pipeline.run` and the `/alerts` read path (`AlertService`) are unchanged |
| Scheduled runs | A worker calling `app.pipeline.run.run_pipeline()` (the whole slice) or an individual stage's service (`SensorIngestionService.run()`, `ForecastingService.run()`, …) on a timer, replacing the manual `python -m app.pipeline.run` / `app.cli <command>` triggers — none of the services themselves change |
| Per-hour forecast weather (instead of holding current weather constant) | A `WeatherForecastProvider`-shaped source feeding hour-indexed `WeatherReading`s into the model's per-hour loop; the model's coefficient/transport structure doesn't change |
| New PDI model (e.g. one that also weighs forecasted trend, not just current state) | New class implementing `PDIModel` in `app/services/`; whatever calls `calculate()` is unchanged |
| A "sink" factor that should lower PDI (e.g. precipitation washout) | Add the field to `CellContext`, populate it, give it a negative weight — `HeuristicPDIModel`'s formula already supports negative weights and stays bounded to `[-100, 100]` |
| New pollutant | New config entry + source field mapping (schema is already long-format) |
| New pollution-data source (CPCB, satellite, private sensors) | New class implementing `PollutionDataProvider` in `app/ingestion/`; `SensorIngestionService` and the CLI are unchanged — only the wiring (which provider gets constructed) picks it |
| New weather source (ECMWF, another provider) | New class implementing `WeatherProvider` in `app/ingestion/`; `WeatherIngestionService`, the representative-sampling logic, and the CLI are all unchanged |
| Satellite data specifically | Also likely a new `gridded_observation`-shaped table + a fusion nowcaster, since it isn't point-station data; forecaster/PDI/alerts/API/frontend untouched |
| New region | New config file |
