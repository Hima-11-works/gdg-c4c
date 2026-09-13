# Pollution Intelligence Platform

Project scaffold for a pollution intelligence MVP (PM2.5, H3 grid, weather-driven
spread predictions). The full vertical slice is wired end to end — one command
(`python -m app.pipeline.run`) takes it from OpenAQ/Open-Meteo ingestion through
PM2.5 interpolation, PDI, forecasting, and alerting, and the API serves real
persisted data once it's run. Any endpoint with nothing real behind it yet still
falls back to deterministic demo data (`is_demo: true`). So far the project has:
- a FastAPI backend: `/api/v1` (sensors, weather, grid, cells, alerts) plus
  health/readiness, with a consistent error shape and OpenAPI docs at `/docs`
- PM2.5 estimation v1 (`IDWPollutionEstimator`, inverse-distance-weighted)
  behind a `PollutionEstimator` interface, so Kriging, satellite fusion, or
  an ML model can replace it later without changing any caller
- PDI v0 (`HeuristicPDIModel`): a heuristic "pollution pressure index",
  **not** a scientific measurement, behind a `PDIModel` interface
- a deterministic H3 dispersion/forecast model v0
  (`DeterministicH3DispersionModel`): wind-driven advection, limited
  neighbor diffusion, decay/precipitation removal, mass-conserving by
  construction, behind a `PollutionForecastModel` interface
- a very small rule-based alert engine (`AlertGenerationService`, **no
  machine learning**) — threshold crossing (now or forecast), sharp
  current-to-forecast increase, and high PDI + worsening forecast; see
  "Alerts" below
- OpenAQ ingestion for PM2.5 and Open-Meteo ingestion for weather, each
  behind a provider interface (`PollutionDataProvider` / `WeatherProvider`)
  so other sources (CPCB, satellite, ECMWF, private sensors) can be added
  later without touching anything downstream
- **`python -m app.pipeline.run`**: the complete pipeline (ingest → grid
  state + PDI → forecasts → alerts) for the configured region in one
  command, with a clear per-stage pass/fail report — see "Full pipeline"
  below. Every stage is also runnable individually via `python -m app.cli
  <command>` for local development.
- a database layer: schema, migrations, and a repository per entity
  (`SensorReading`, `WeatherReading`, `GridState`, `Forecast`, `Alert`)
- a dedicated geospatial service (`GeospatialService`) wrapping every H3
  operation the app needs, and `python -m app.cli export-grid` to inspect
  the region's grid as GeoJSON
- a React + TypeScript + MapLibre map dashboard (current PM2.5/PDI layers,
  wind, the Now/+1h/+3h/+6h timeline, cell details, alerts)
- PostgreSQL/PostGIS via Docker Compose, with Alembic migrations

See `docs/architecture.md` for the full system design.

## Stack

- **Frontend:** React + TypeScript + Vite
- **Backend:** Python + FastAPI
- **Database:** PostgreSQL + PostGIS
- **Local infra:** Docker Compose

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose v2)
- [Node.js](https://nodejs.org/) 20.19+ or 22.12+ (required by Vite 8)
- Python 3.11+ (only needed to run the backend or its tests outside Docker)

## Quick start

```bash
# 1. Create .env and frontend/.env from the committed examples,
#    then change POSTGRES_PASSWORD in .env
./scripts/bootstrap.sh

# 2. Start PostgreSQL/PostGIS + the API
docker compose up --build

# 3. In a second terminal, start the frontend
cd frontend
npm ci
npm run dev
```

| URL | What it is |
|---|---|
| http://localhost:5173 | Frontend |
| http://localhost:8000/health | Liveness: the API process is up (never touches the DB) |
| http://localhost:8000/health/ready | Readiness: PostgreSQL reachable and PostGIS installed (200 or 503) |
| http://localhost:8000/docs | Swagger UI — every `/api/v1/*` endpoint |
| http://localhost:8000/api/v1/sensors | Try it: latest sensor readings (demo data until ingestion exists) |

## API

| Endpoint | Returns |
|---|---|
| `GET /api/v1/sensors` | Latest reading per sensor |
| `GET /api/v1/weather` | Latest weather per H3 cell |
| `GET /api/v1/grid/current` | Current PM2.5/PDI state per cell |
| `GET /api/v1/grid/forecast?hours=1\|3\|6` | Forecast per cell at that horizon |
| `GET /api/v1/cells/{h3_cell}` | Current state + forecasts + weather for one cell |
| `GET /api/v1/alerts` | Alerts from the last 24h |

Every response is `{"generated_at", "is_demo", "data"}`. Ingestion isn't
implemented, so `is_demo` is `true` until real rows exist — the frontend
should treat that as "illustrative, not measured" (e.g. a banner), never as
real air-quality data. Errors are always `{"error": {"code", "message", "details"?}}`.
See `docs/architecture.md` for the full contract and the demo-data fallback rule.

## Configuration

`.env` at the repo root is the **single configuration file** for Docker Compose and
the backend. The backend reads it by absolute path, so it is found whether you
start from the repo root or from `backend/`. Real environment variables always
override it.

| Variable | Used by | Notes |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | db container, backend | **Required.** No defaults in code; Compose refuses to start without them |
| `POSTGRES_HOST`, `POSTGRES_PORT` | backend | For host-side runs (`localhost:5432`). Compose overrides them to `db:5432` for the api container |
| `API_PORT` | Compose | Host port for the API |
| `ENVIRONMENT`, `LOG_LEVEL`, `CORS_ORIGINS` | backend | |
| `H3_RESOLUTION` | backend | H3 resolution (0-15, default 8) for every `h3_cell` column. Changing it on an existing database does not rewrite stored rows — treat it as a breaking change to stored data |
| `OPENAQ_API_KEY` | ingestion | Required to ingest PM2.5 ([get one free](https://explore.openaq.org/register)). Leave blank to run everything else without it |
| `OPENAQ_BASE_URL`, `OPENAQ_TIMEOUT_SECONDS`, `OPENAQ_MAX_RETRIES`, `OPENAQ_LOCATIONS_LIMIT` | ingestion | OpenAQ adapter tuning — see `.env.example` |
| `OPEN_METEO_BASE_URL`, `OPEN_METEO_TIMEOUT_SECONDS`, `OPEN_METEO_MAX_RETRIES`, `OPEN_METEO_MAX_LOCATIONS_PER_REQUEST` | ingestion | Open-Meteo adapter tuning; no API key needed |
| `WEATHER_MAX_CELLS` | ingestion | Safety ceiling (default 50000) on one weather run's fan-out; an oversized bbox is refused instead of building millions of rows |
| `WEATHER_H3_RESOLUTION` | ingestion | Coarser H3 resolution (default 5) weather is sampled at, fanned out to every `H3_RESOLUTION` cell inside each sampled cell. Must be <= `H3_RESOLUTION` |
| `INGEST_BBOX_MIN_LAT`/`MIN_LON`/`MAX_LAT`/`MAX_LON` | ingestion | Bounding box to ingest, shared by `ingest` and `ingest-weather` (default: San Francisco, matching the demo data) |
| `INGEST_MAX_READING_AGE_HOURS` | ingestion | A fetched PM2.5 reading older than this is dropped as stale |
| `IDW_MAX_DISTANCE_KM` | estimation | Max distance (default 15km) from a cell center a sensor may be to count as evidence for that cell |
| `IDW_MIN_SENSORS` | estimation | Min sensors (default 2) required within range before a cell gets an estimate at all |
| `PDI_PM25_REFERENCE_UGM3` | PDI | PM2.5 (default 250 ug/m3) treated as "maximum pressure" when normalizing to [0, 1] — a normalization scale, not a scientific threshold |
| `PDI_PM25_WEIGHT`, `PDI_ROAD_PRESSURE_WEIGHT`, `PDI_INDUSTRIAL_PRESSURE_WEIGHT` | PDI | Relative weights (default 0.7 / 0.2 / 0.1) of each factor in the PDI blend; renormalized over whichever factors are actually present for a cell |
| `DISPERSION_DECAY_RATE_PER_HOUR`, `DISPERSION_WET_REMOVAL_RATE_PER_HOUR`, `DISPERSION_PRECIPITATION_REFERENCE_MM` | dispersion | Baseline + precipitation-driven PM2.5 removal per hour (defaults 0.15, 0.25, 4mm) |
| `DISPERSION_MAX_TRANSPORT_FRACTION`, `DISPERSION_WIND_TRANSPORT_REFERENCE_MS`, `DISPERSION_CALM_WIND_THRESHOLD_MS` | dispersion | How much of a cell's PM2.5 wind can move per hour, and at what speeds (defaults 0.6, 8 m/s, 0.5 m/s) |
| `DISPERSION_WIND_CONE_HALF_ANGLE_DEG` | dispersion | Half-angle (default 50°) of the downwind neighbor-selection cone |
| `DISPERSION_CONFIDENCE_DECAY_PER_HOUR`, `DISPERSION_MISSING_WEATHER_CONFIDENCE_PENALTY` | dispersion | Per-hour forecast confidence discount, and an extra one for a cell with no weather reading (defaults 0.9, 0.5) |
| `ALERT_WARNING_THRESHOLD_UGM3`, `ALERT_CRITICAL_THRESHOLD_UGM3` | alerts | PM2.5 (µg/m3) at/above which a cell gets a WARNING/CRITICAL alert if happening now, or WATCH if only a forecast horizon reaches it (defaults 55, 150 — the AQI "Unhealthy" range) |
| `ALERT_SHARP_INCREASE_THRESHOLD_UGM3` | alerts | Current-to-forecast PM2.5 jump (µg/m3) that counts as a "sharp increase" alert on its own (default 25) |
| `ALERT_PDI_HIGH_THRESHOLD`, `ALERT_PDI_WORSENING_MIN_INCREASE_UGM3` | alerts | PDI considered "high pressure", and the (smaller) PM2.5 increase that counts as "worsening" when combined with it (defaults 60, 5) |
| `ALERT_ACTIVE_LOOKBACK_HOURS` | alerts | A cell with an alert created within this many hours is skipped on the next pipeline run, and is what `/api/v1/alerts` considers "active" (default 24) |
| `VITE_API_BASE_URL` (in `frontend/.env`) | Vite | Where the frontend calls the backend |

The backend builds the database URL from the `POSTGRES_*` parts, so credentials
are defined only once.

## Project layout

```
.
├─ docker-compose.yml       # db (PostGIS) + api
├─ .env.example             # copy to .env
├─ backend/
│  ├─ alembic.ini
│  ├─ alembic/
│  │  └─ versions/          # 0001_initial_schema.py
│  ├─ app/
│  │  ├─ api/               # routes (thin), schemas, error handling, DI wiring
│  │  │  └─ routes/         # sensors, weather, grid, cells, alerts, health
│  │  ├─ core/              # settings
│  │  ├─ db/                # SQLAlchemy engine/session
│  │  │  └─ repositories/   # concrete (SQLAlchemy) repository implementations
│  │  ├─ domain/            # pure types, Protocols, h3_grid.py (all raw H3 calls) — no I/O
│  │  ├─ ingestion/         # openaq.py, open_meteo.py, shared retry policy in http.py
│  │  ├─ models/            # database table definitions (the schema)
│  │  ├─ services/          # per-resource logic, demo-data fallback, ingestion orchestration,
│  │  │                     # geospatial.py (GeospatialService), estimation.py (IDWPollutionEstimator),
│  │  │                     # pdi.py (HeuristicPDIModel), dispersion.py (DeterministicH3DispersionModel),
│  │  │                     # forecasting.py (ForecastingService — runs+persists the dispersion model)
│  │  ├─ cli.py             # dev commands — `python -m app.cli ingest[-weather]|export-grid|forecast`
│  │  └─ main.py            # FastAPI app entrypoint
│  ├─ tests/
│  ├─ pyproject.toml        # dependency ranges
│  └─ requirements.lock     # exact versions installed in Docker
├─ frontend/
├─ scripts/
│  ├─ bootstrap.sh          # copies .env.example → .env
│  └─ lock-backend.sh       # regenerates backend/requirements.lock (needs uv)
└─ docs/
   └─ architecture.md
```

Layer boundaries (which `app/*` package may import which) are enforced by
`backend/tests/test_architecture.py`.

## Backend

Run outside Docker (with the database from `docker compose up -d db`):

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Tests and lint:

```bash
cd backend
pytest                                   # unit tests, no database needed
RUN_DB_TESTS=1 pytest                    # also checks real PostgreSQL/PostGIS (needs the db running)
ruff check . && ruff format --check .
```

Dependencies: edit ranges in `pyproject.toml`, then run
`./scripts/lock-backend.sh` to regenerate `requirements.lock`.

## Database

Docker Compose runs migrations automatically (`alembic upgrade head`) before
starting the API. Outside Docker:

```bash
cd backend
alembic upgrade head          # apply migrations
alembic downgrade -1          # roll back one revision
```

The schema is defined once in `app/models/tables.py` and mirrored by hand in
`alembic/versions/0001_initial_schema.py` — there was no live database
available while building this to autogenerate a migration against, so the
two are kept in sync manually (see that migration's docstring). Repositories
in `app/db/repositories/` are the only code that builds SQL against these
tables; everything above `app/db` depends on the `app.domain.repositories`
Protocols instead.

## Geospatial

All H3 logic goes through `app.services.geospatial.GeospatialService` —
nothing else calls the `h3` library or hardcodes a resolution. It converts
lat/lon to cells, cells to GeoJSON polygons, looks up neighbors, and
generates the region's grid coverage. See `docs/architecture.md` for the
full method list.

```bash
cd backend
python -m app.cli export-grid                                # region from .env -> grid.geojson
python -m app.cli export-grid --out sf.geojson \
    --min-lat 37.75 --min-lon -122.45 --max-lat 37.80 --max-lon -122.40
```

Writes the configured region's H3 coverage as a GeoJSON `FeatureCollection`
— open the file directly at [geojson.io](https://geojson.io) to inspect
the grid visually. No API key or database needed.

## PM2.5 estimation

`app.services.estimation.IDWPollutionEstimator` fills in PM2.5 for grid
cells that don't contain a sensor, via inverse-distance-weighted
interpolation — behind `app.domain.estimation.PollutionEstimator`, so
Kriging, satellite fusion, or an ML model can replace it later without any
caller changing. Run and persisted via `python -m app.pipeline.run` (or
`GridComputationService` directly — see "Full pipeline" below); exercised
directly, independent of any pipeline, by
`backend/tests/test_estimation.py`.

- A cell with no sensor within `IDW_MAX_DISTANCE_KM`, or fewer than
  `IDW_MIN_SENSORS` within that range, gets `pm25=None` and
  `confidence=0.0` — never a fabricated value.
- A sensor within 10m of a cell center is used directly rather than
  divided by a near-zero distance.
- `pdi`/`wind_speed`/`wind_direction` are always `None` from this
  estimator (it only ever sees PM2.5 readings) — see
  `docs/architecture.md` for why `GridState`'s fields are `Optional`.

## PDI (pollution pressure index)

`app.services.pdi.HeuristicPDIModel` computes PDI for one cell at a time
via `app.domain.pdi.PDIModel.calculate(cell_context) -> PDIResult`. **PDI
is a heuristic "pollution pressure index", not a scientifically exact
measurement of net emissions** — a configurable, weighted blend of
normalized signals, meant for ranking/triage, not as a physical quantity.
Run and persisted onto the same `GridState` row PM2.5 estimation just
wrote, via `python -m app.pipeline.run` (see `GridComputationService`
below); exercised directly, independent of any pipeline, by
`backend/tests/test_pdi.py`.

- v0 uses only the current PM2.5 estimate as input — the only factor with
  a real data source today.
- `CellContext.road_pressure` and `.industrial_pressure` are extension
  points for when road-density/industrial-proximity data exists: pass
  them in (pre-normalized to `[0, 1]`) and they're blended in
  automatically, with no change to `HeuristicPDIModel` or its caller.
- A cell with no available factor (or every available factor configured
  with zero weight) gets `pdi=None` — never a fabricated score.
- `PDIResult.factors` reports the *normalized* `[0, 1]` value of each
  factor that contributed (e.g. `{"pm25": 0.81}`), not its weighted
  share, so a caller/UI can show which signals drove the score.
- Output is `0` to `100` today (only non-negative weights are
  configured); a future negative-weighted "sink" factor (e.g.
  precipitation washout) could push it toward `-100` without any formula
  change.

## Dispersion / forecast model

`app.services.dispersion.DeterministicH3DispersionModel` forecasts PM2.5
forward hour by hour from `GridState`/`WeatherReading` data, behind
`app.domain.dispersion.PollutionForecastModel`. **Not an atmospheric
chemistry simulator** — a deliberately simple, explainable box model: each
H3 cell is well-mixed; every hour its PM2.5 is reduced by a removal
fraction (decay, boosted by precipitation) and split between what stays
and what's transported into immediate H3 neighbors, biased toward
whichever neighbor(s) are closest to the wind's downwind bearing. It
redistributes/removes pollution that already exists — no emissions term.

- **1h/3h/6h** are all produced from one run of the same hour-by-hour
  loop (the 3h result is the literal state after 3 of the same steps used
  to reach 6h) — never a shortcut, so the horizons are always mutually
  consistent.
- **Mass conservation:** every coefficient is clamped to `[0, 1]`, and
  `DISPERSION_MAX_TRANSPORT_FRACTION < 1` caps transport regardless of
  wind speed — so total mass across the modeled grid can only decrease
  or leave through an open domain boundary, never increase, however
  extreme the input. `predicted_pm25` is validated `>= 0` at construction
  (`Forecast`, matching `GridState.pm25`), with a matching DB `CHECK`.
- **Confidence** propagates through the same transport as a mass-weighted
  average, then decays once per hour (`DISPERSION_CONFIDENCE_DECAY_PER_HOUR`)
  and takes an extra penalty for any hour a cell had no weather reading.
- **Known simplification:** weather is held constant across the whole
  forecast horizon — there's no per-hour weather forecast feed yet.

`app.services.forecasting.ForecastingService` reads the latest
`GridState`/`WeatherReading` rows, runs the model for `hours=(1, 3, 6)`,
and saves every resulting `Forecast`. Run it standalone with:

```bash
cd backend
python -m app.cli forecast
```

or as one stage of `python -m app.pipeline.run` (the "Full pipeline"
section below), which runs PM2.5 estimation and PDI first so this stage
has fresh `GridState` rows to forecast from. Once it's run at least once,
`/api/v1/grid/forecast` and
`/api/v1/cells/{h3_cell}` automatically start serving the real, persisted
forecasts instead of demo data — no API or service code changes needed,
since they already read from `ForecastRepository`. See
`backend/tests/test_dispersion.py` (the model) and
`backend/tests/test_forecasting_service.py` (the pipeline).

## Ingestion

```bash
cd backend
python -m app.cli ingest                                    # PM2.5, bbox from .env
python -m app.cli ingest-weather                             # weather, bbox from .env
python -m app.cli ingest --min-lat 28.4 --min-lon 76.8 \
                          --max-lat 28.9 --max-lon 77.4      # override for one run
```

Under Docker Compose, run either inside the `api` container instead:
`docker compose exec api python -m app.cli ingest-weather`.

- **`ingest`** requires `OPENAQ_API_KEY` in `.env` and the database
  running. Fetches recent PM2.5 readings for the configured bounding box
  from OpenAQ, normalizes them into `SensorReading`, and saves them —
  skipping exact duplicates (same source/sensor/pollutant/timestamp) and
  stale readings (older than `INGEST_MAX_READING_AGE_HOURS`).
- **`ingest-weather`** needs no API key. Samples weather at a coarser H3
  resolution than the grid (`WEATHER_H3_RESOLUTION`) and fans each sample
  out to every fine `H3_RESOLUTION` cell it covers — one batched
  Open-Meteo request typically produces far more `WeatherReading` rows
  than points requested, which is deliberate (see `docs/architecture.md`).

Either command prints a message and exits 1 on a network, API, or
database failure — never an uncaught traceback. There is no scheduler
yet; both (and `forecast`) are manual triggers for local development. See
`docs/architecture.md` for the full fetch flow and how to add another
source (CPCB, satellite, ECMWF, private sensors) behind the same
interfaces.

## Full pipeline

```bash
cd backend
python -m app.pipeline.run
```

Runs the complete vertical slice for the configured region in one command:

```
OpenAQ -> sensor ingestion -> H3 grid + PM2.5 interpolation -> PDI
-> Open-Meteo -> weather ingestion -> dispersion model -> 1h/3h/6h
forecasts -> alerts
```

Prints one line per stage (`[OK  ] sensor_ingestion: fetched=12 saved=10 ...` /
`[FAIL] ...: <reason>`) and exits 1 if any stage failed. Every stage is a
small orchestration service with its own test file
(`SensorIngestionService`, `WeatherIngestionService`,
`GridComputationService`, `ForecastingService`, `AlertGenerationService`) —
this module only wires them together in order against one shared
session/timestamp/bounding box; none of the actual pollution/forecast/PDI
logic lives in it.

**A failed external data source doesn't corrupt or abort the run.** If
OpenAQ or Open-Meteo is unreachable, that stage is reported as a clear
failure (and the process exits 1), but every stage after it still runs —
`IDWPollutionEstimator` already returns `pm25=None`/`confidence=0.0`
rather than a fabricated estimate when there isn't enough sensor
evidence, and `DeterministicH3DispersionModel` already forecasts
decay-only for a cell with no weather reading rather than inventing
wind — so continuing with whatever is already persisted is correct, not
a silent corruption. Only the database itself being unreachable aborts
the whole run (nothing below can do anything meaningful without one).

`GridComputationService` is the one new orchestration piece this ties
together: PM2.5 (`IDWPollutionEstimator`) and PDI (`HeuristicPDIModel`)
land on the *same* `GridState` row, so it estimates, folds in a PDI
score per cell, and persists once — see
`backend/tests/test_grid_computation_service.py`.

See "Alerts" below for `AlertGenerationService`'s rules in detail.

There is no scheduler yet — this is a manual trigger, same as every
other `app.cli`/`app.pipeline` command; a cron job or worker calling it
periodically is the natural next step and wouldn't need any code here to
change.

## Alerts

`app.services.alert_generation.AlertGenerationService` is a very small
rule-based alert engine — **no machine learning**, every rule is a plain,
configurable comparison. Rules run in priority order per cell; the first
one that matches wins, so a cell gets at most one alert per pipeline run:

1. **Threshold crossed now** — current PM2.5 at/above
   `ALERT_WARNING_THRESHOLD_UGM3` / `ALERT_CRITICAL_THRESHOLD_UGM3` →
   `WARNING` / `CRITICAL`.
2. **Threshold crossed in the forecast** — no current exceedance, but
   some forecast horizon reaches a threshold → `WATCH` (advance warning,
   not an active condition — see below).
3. **Sharp increase** — current-to-forecast PM2.5 jump of at least
   `ALERT_SHARP_INCREASE_THRESHOLD_UGM3` at some horizon, independent of
   whether either value alone crosses a threshold → `WATCH`.
4. **High PDI + worsening forecast** — current PDI at/above
   `ALERT_PDI_HIGH_THRESHOLD` *and* a forecast horizon at least
   `ALERT_PDI_WORSENING_MIN_INCREASE_UGM3` above current PM2.5 (a milder
   bar than rule 3, since already-high pressure makes even a modest
   uptick worth flagging) → `WATCH`.

Severity encodes **when** a condition is or will be true, not just how
severe it is: only rule 1 can produce `WARNING`/`CRITICAL`; every other
rule is `WATCH`, regardless of which threshold a forecast value happens
to cross. A cell already exceeding a threshold *now* always takes
priority over anything a forecast says about it. A cell with an alert
already created within `ALERT_ACTIVE_LOOKBACK_HOURS` is skipped
entirely, so a persistent condition doesn't spawn a new alert every run.

Every `Alert` carries the context it was raised with — never fabricated,
so any of these can be null:

| Field | Meaning |
|---|---|
| `current_pm25` | The cell's current estimate, or null if it had none |
| `forecast_pm25` / `forecast_hours` | The forecast attached to this alert — the horizon that triggered it (rules 2-4), or the nearest available horizon as trend context (rule 1); null only if the cell had no forecast at all |
| `confidence` | Confidence in whichever value triggered the alert — the current estimate's confidence for rule 1, that forecast horizon's confidence otherwise |
| `forecast_time` | When the alerted condition itself occurs; null if it's already true now (rule 1) |

Rules are configurable (`ALERT_*` env vars, see Configuration above) and
isolated behind `AlertGenerationService` — nothing else in the codebase
knows how a threshold is evaluated. `app.services.alerts.AlertService`
(the `/alerts` read path) is a separate, unrelated class: it only reads
whatever `Alert` rows exist, from this engine or anywhere else, and is
exposed at `GET /api/v1/alerts` (see "API" above) and displayed in the
frontend's alerts panel (see "Frontend" below). See
`backend/tests/test_alert_generation_service.py` for every rule's tests.

## Frontend

```bash
cd frontend
npm ci             # exact versions from package-lock.json
npm run dev        # dev server
npm run build      # type-check + production build
npm run lint       # oxlint
npm run format     # prettier --write
```

A single full-screen MapLibre GL JS map (`src/components/MapPage.tsx`), one page, no router. H3 hex
boundaries are computed client-side with `h3-js` from the plain `h3_cell` strings the API returns
(there's no live GeoJSON endpoint — see `src/lib/h3Geometry.ts`) and colored by PM2.5 or the
heuristic PDI (`src/lib/colorScales.ts`), switchable per the Now/+1h/+3h/+6h timeline. Wind is drawn
as rotated arrow glyphs from `/api/v1/weather`. Clicking a hex opens a detail sidebar
(`/api/v1/cells/{h3_cell}`); a collapsible panel shows `/api/v1/alerts`.

`src/lib/api.ts` is the only module that calls `fetch` — every export in it maps to one documented
backend endpoint and does no computation beyond typing the JSON. All pollution/forecast/PDI math
stays server-side; the frontend only ever displays what the API returns. Shared UI state (selected
timeline horizon, PDI toggle, selected cell) lives in a small `useReducer` + Context
(`src/state/`) — deliberately not Redux, since it's three fields read by a handful of sibling
components. Server data is *not* kept there: each component fetches what it needs via
`src/hooks/useApiResource.ts`, a ~50-line hook covering loading/success/error/polling/retry (no
data-fetching library). PDI's contributing-factor breakdown isn't in the API yet
(`GridStateOut.pdi` is a bare number), so the cell detail panel says so rather than fabricating one.

## Notes

- Docker Compose runs only `db` and `api`. The frontend runs on the host with
  `npm run dev`, which hot-reloads faster than Vite inside Docker on Windows.
- The `api` container waits for the Postgres healthcheck. Its own healthcheck
  uses `/health/ready`, so `docker compose ps` shows it healthy only once
  PostGIS is reachable.
- Nothing writes automatically — `ingest`, `ingest-weather`, `forecast`,
  and the full `python -m app.pipeline.run` are all manually triggered
  (see "Full pipeline" above). Every `/api/v1/*` endpoint falls back to
  deterministic demo data (`app/services/demo_data.py`) when its
  repository query is empty — see `is_demo` in every response.
