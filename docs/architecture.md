# Architecture

Approved design for the pollution intelligence MVP, after the simplification
review. This document describes the target architecture; only the base
scaffold (health endpoint, no pollution logic) has been implemented so far.

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
| `app/domain` | Pure domain types + the pollution/spread model (grid helpers, nowcaster, forecaster, PDI). No I/O. | `app/core` |
| `app/ingestion` | Source adapters: OpenAQ, Open-Meteo, fixtures | `app/core`, `app/domain` |
| `app/models` | SQLAlchemy table definitions | `app/core` |
| `app/db` | Engine/session management | `app/core` |
| `app/services` | Orchestration: ingestion pipeline, alert rules — the only layer allowed to import ingestion + domain + models together | all of the above |
| `app/api` | FastAPI routes, request/response schemas, GeoJSON building | `app/core`, `app/domain`, `app/db` |

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

## Database (target schema, not yet created)

| Table | Purpose |
|---|---|
| `region` | Name, bbox, H3 resolution — upserted from config |
| `station` | Sensor identity + location |
| `observation` | Raw readings, long format (`pollutant`, `value`) so new pollutants don't require schema changes |
| `model_run` | One row per pipeline execution, with the exact config used |
| `weather` | Wind speed/direction, precipitation per coarse cell per run |
| `cell_value` | Nowcast/forecast values per cell per horizon, plus PDI (only meaningful at horizon 0) |
| `alert` | Alerts produced by the latest run |

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
