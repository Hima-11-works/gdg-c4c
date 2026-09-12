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
  frontend (host, Vite)  ──HTTP /api/v1 (OpenAPI, GeoJSON)──►  api container
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
| `app/ingestion` | Source adapters: OpenAQ, Open-Meteo, fixtures (not yet implemented) | `app/core`, `app/domain` |
| `app/services` | Orchestration: ingestion pipeline, alert rules — the only layer allowed to import ingestion + domain + models together | all of the above |
| `app/api` | FastAPI routes, request/response schemas, GeoJSON building | `app/core`, `app/domain`, `app/db` |

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

## API (target contract, not yet implemented beyond `/health`)

| Endpoint | Returns |
|---|---|
| `GET /health` | API liveness (implemented) |
| `GET /health/ready` | PostgreSQL + PostGIS readiness, 200/503 (implemented) |
| `GET /meta` | Region info, pollutants, horizons, latest run |
| `GET /grid?pollutant=&horizon=` | GeoJSON polygons of cell values |
| `GET /cells/{cell_id}` | Per-cell detail + PDI breakdown |
| `GET /weather?horizon=` | GeoJSON wind/precip points |
| `GET /stations` | GeoJSON station points |
| `GET /alerts` | Alerts from the latest run |

## Configuration

- **Env vars** (`.env`): infrastructure — DB connection, ports, CORS, log
  level. See `.env.example`.
- **Region config** (future `config/region.yaml`): domain tuning — bbox, H3
  resolution, QC thresholds, model parameters, PDI weights, alert
  thresholds. Not yet added; there's no model to configure.

## Extension points

| Future change | What changes |
|---|---|
| New forecast model | New module under `app/domain`, selected by config |
| New pollutant | New config entry + source field mapping (schema is already long-format) |
| Satellite data | New ingestion adapter producing gridded observations + a fusion nowcaster; forecaster/PDI/alerts/API/frontend untouched |
| New region | New config file |
