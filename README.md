# Pollution Intelligence Platform

Project scaffold for a pollution intelligence MVP (PM2.5, H3 grid, weather-driven
spread predictions). **PM2.5 estimation and PDI aren't wired into any pipeline
yet** — only the dispersion/forecast model is (`python -m app.cli forecast`
persists real `Forecast` rows). Any endpoint with nothing real behind it still
falls back to deterministic demo data. So far the project has:
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
  construction, behind a `PollutionForecastModel` interface — run and
  persisted via `python -m app.cli forecast`
- OpenAQ ingestion for PM2.5 (`python -m app.cli ingest`) and Open-Meteo
  ingestion for weather (`python -m app.cli ingest-weather`), each behind
  a provider interface (`PollutionDataProvider` / `WeatherProvider`) so
  other sources (CPCB, satellite, ECMWF, private sensors) can be added
  later without touching anything downstream
- a database layer: schema, migrations, and a repository per entity
  (`SensorReading`, `WeatherReading`, `GridState`, `Forecast`, `Alert`)
- a dedicated geospatial service (`GeospatialService`) wrapping every H3
  operation the app needs, and `python -m app.cli export-grid` to inspect
  the region's grid as GeoJSON
- a React + TypeScript frontend that displays backend health
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
caller changing. Not wired into a pipeline or the API yet; this is the
model layer only, exercised directly by
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
Not wired into a pipeline or the API's read path yet (`GridStateOut.pdi`
already exists and accepts the value once something populates it); this
is the model layer only, exercised directly by
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

Unlike `IDWPollutionEstimator`/`HeuristicPDIModel`, this one **is**
persisted: `app.services.forecasting.ForecastingService` reads the latest
`GridState`/`WeatherReading` rows, runs the model for `hours=(1, 3, 6)`,
and saves every resulting `Forecast`. Run it with:

```bash
cd backend
python -m app.cli forecast
```

Requires `GridState` rows to already exist (run ingestion, or seed some
manually — there's no PM2.5-estimation pipeline wired up yet either).
Once it's run at least once, `/api/v1/grid/forecast` and
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

## Frontend

```bash
cd frontend
npm ci             # exact versions from package-lock.json
npm run dev        # dev server
npm run build      # type-check + production build
npm run lint       # oxlint
npm run format     # prettier --write
```

## Notes

- Docker Compose runs only `db` and `api`. The frontend runs on the host with
  `npm run dev`, which hot-reloads faster than Vite inside Docker on Windows.
- The `api` container waits for the Postgres healthcheck. Its own healthcheck
  uses `/health/ready`, so `docker compose ps` shows it healthy only once
  PostGIS is reachable.
- `ingest` and `ingest-weather` write `sensor_reading` / `weather_reading`
  rows when triggered manually; nothing else writes yet — the pollution
  model isn't implemented. Every `/api/v1/*` endpoint falls back to
  deterministic demo data (`app/services/demo_data.py`) when its
  repository query is empty — see `is_demo` in every response.
