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

```
  frontend (host, Vite)  ──HTTP /api/v1 (OpenAPI, JSON)──►  api container
                                                                   │ read
                                                             PostgreSQL+PostGIS
                                                                   ▲ write
  worker container ─ pipeline:                                     │
     sources ──► store(raw) ──► build inputs(QC) ──► Nowcaster ──► Forecaster
                                                       ──► PDI ──► alerts ──► store(run)
```

- Three containers eventually: `db`, `api`, `worker` (api and worker share one
  image). Only `db` and `api` exist so far — there is no pipeline to run yet.
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
| `app/domain` | Pure domain types (`SensorReading`, `WeatherReading`, `GridState`, `Forecast`, `Alert`), repository Protocols (ports), H3 validation. No I/O. | `app/core` |
| `app/models` | SQLAlchemy Core table definitions (the schema) | `app/core`, `app/domain` (only for the `AlertSeverity` column type) |
| `app/db` | Engine/session management, and `app/db/repositories/*` — concrete SQLAlchemy implementations of the domain repository Protocols | `app/core`, `app/domain`, `app/models` |
| `app/ingestion` | Source adapters implementing `app.domain.providers.PollutionDataProvider` or `WeatherProvider`: `OpenAQProvider` and `OpenMeteoProvider` (both implemented); a shared retry policy in `http.py` | `app/core`, `app/domain` |
| `app/services` | Business logic: per-resource read services (`SensorService`, `GridService`, `CellService`, `AlertService`, …) plus the demo-data fallback, and `SensorIngestionService` / `WeatherIngestionService` (fetch → persist, skip duplicates) | `app/core`, `app/domain`, `app/ingestion`, `app/models`, `app/db` |
| `app/api` | FastAPI routes (thin — call a service, shape the response), Pydantic schemas, error handling, dependency wiring | `app/core`, `app/domain`, `app/db` (dependency wiring only, see `app/api/deps.py`), `app/services` |

Domain code depends only on `app/core` and never on SQLAlchemy: repositories
are consumed through the `app.domain.repositories` Protocols, so a service
typed against those interfaces can be tested with an in-memory fake instead
of a database, and the storage backend could be replaced without touching
anything above `app/db`.

Import direction is enforced by `backend/tests/test_architecture.py` (relative
imports are banned by ruff so the check can't be bypassed). `app/main.py` is the
composition root and may import any layer.

## Data flow (once the pipeline exists)

1. `t0` = latest full hour (UTC). Create a `model_run` row, status `running`.
2. Fetch station readings and weather (coarse H3 cells, t0 → t0+max horizon).
   Store both raw.
3. Build model inputs: QC filter, max observation age, weather mapped to fine
   cells.
4. Per configured pollutant: nowcaster → h=0 field, forecaster → h=1/3/6,
   then PDI, then alerts.
5. Write everything in one transaction, mark the run `succeeded`. On failure,
   mark `failed` with the error; the API keeps serving the previous
   successful run.

## Database (implemented)

All tables live in `app/models/tables.py` (the schema's reference
definition) and `alembic/versions/0001_initial_schema.py` (hand-written to
match it — see that file's docstring for why there's no autogenerate here).
Timestamps are always `timestamptz`, written and read as UTC; the app layer
rejects naive or non-UTC datetimes before they ever reach SQL
(`app.domain.types._require_utc`).

| Table | Purpose | Key / notable indexes |
|---|---|---|
| `sensor_reading` | Raw pollutant readings from stations. `pollutant` is a plain string, not an enum, so a new pollutant is a data change | PK `id`; unique (`source`, `external_sensor_id`, `pollutant`, `measured_at`); GiST index on `geom`; btree on (`pollutant`, `measured_at`) |
| `weather_reading` | Weather sample for one H3 cell | PK `id`; unique (`h3_cell`, `measured_at`); btree on `h3_cell` |
| `grid_state` | Current pollution state of one cell at one time (PM2.5, PDI, confidence, wind) | PK (`h3_cell`, `timestamp`); upserted, not appended |
| `forecast` | Predicted PM2.5 for one cell at a future time, tagged with the horizon and the run that produced it | PK `id`; unique (`h3_cell`, `generated_at`, `forecast_hours`) |
| `alert` | A pollution alert for one cell | PK `id`; `severity` is a plain-string column whose CHECK constraint is generated from the `AlertSeverity` enum, not hand-duplicated |

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

| Endpoint | Returns |
|---|---|
| `GET /health` | API liveness — never touches the database |
| `GET /health/ready` | PostgreSQL + PostGIS readiness, 200/503 |
| `GET /api/v1/sensors` | Latest reading per sensor |
| `GET /api/v1/weather` | Latest weather per cell |
| `GET /api/v1/grid/current` | Current `GridState` per cell |
| `GET /api/v1/grid/forecast?hours=1\|3\|6` | Latest `Forecast` per cell at that horizon |
| `GET /api/v1/cells/{h3_cell}` | Current state + all forecasts + weather for one cell |
| `GET /api/v1/alerts` | Alerts created in the last 24h (see `app/services/alerts.py`) |

Every response (`/health` excepted) is wrapped the same way:
`{"generated_at": <UTC ISO-8601>, "is_demo": bool, "data": ...}`. `data` is
a list for collection endpoints, a single object for `/cells/{h3_cell}`.
Internal database ids are never exposed — `h3_cell` (+ `timestamp` /
`generated_at` where relevant) already identifies a resource, and dropping
ids keeps the contract independent of the storage backend.

**Demo-data fallback.** Ingestion doesn't exist yet, so every endpoint
falls back to small, deterministic seed data (`app/services/demo_data.py`)
whenever its repository query returns nothing — never unconditionally, so
real rows take over automatically once ingestion writes them. Every such
response sets `is_demo: true`; the frontend should treat that as "this is
illustrative, not measured" (e.g. a banner), not silently show fabricated
numbers as real air-quality data.

**Errors** are always `{"error": {"code": "...", "message": "...", "details": [...]?}}`
(`app/api/errors.py`), for both explicitly-raised and unhandled exceptions.
`code` is one of `bad_request`, `not_found`, `validation_error`,
`internal_error`, or a generic `http_error` fallback. `/cells/{h3_cell}`
returns 422 for a malformed or wrong-resolution H3 cell, 404 for a
well-formed cell with no data at all (real or demo).

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

## Configuration

- **Env vars** (`.env`): infrastructure — DB connection, ports, CORS, log
  level, H3 resolution (grid and weather-sampling), and OpenAQ/Open-Meteo
  ingestion settings (API key where needed, base URL, timeout/retries, the
  shared ingestion bounding box). See `.env.example`. A `model_validator`
  on `Settings` rejects `WEATHER_H3_RESOLUTION` finer than `H3_RESOLUTION`
  at startup, rather than letting it fail confusingly inside H3 library
  calls the first time ingestion runs.
- **Region config** (future `config/region.yaml`): domain tuning — QC
  thresholds, model parameters, PDI weights, alert thresholds. Not yet
  added; there's no model to configure. The ingestion bounding box lives in
  `.env` for now (`INGEST_BBOX_*`, shared by both `ingest` and
  `ingest-weather`), not this future file — it's config for the adapters,
  not the shared region concept `/meta` will eventually expose.

## Extension points

| Future change | What changes |
|---|---|
| New forecast model | New module under `app/domain`, selected by config |
| New pollutant | New config entry + source field mapping (schema is already long-format) |
| New pollution-data source (CPCB, satellite, private sensors) | New class implementing `PollutionDataProvider` in `app/ingestion/`; `SensorIngestionService` and the CLI are unchanged — only the wiring (which provider gets constructed) picks it |
| New weather source (ECMWF, another provider) | New class implementing `WeatherProvider` in `app/ingestion/`; `WeatherIngestionService`, the representative-sampling logic, and the CLI are all unchanged |
| Satellite data specifically | Also likely a new `gridded_observation`-shaped table + a fusion nowcaster, since it isn't point-station data; forecaster/PDI/alerts/API/frontend untouched |
| New region | New config file |
| Scheduled ingestion | A worker process calling `SensorIngestionService.run()` on a timer, replacing the manual `python -m app.cli ingest` trigger — the service itself doesn't change |
