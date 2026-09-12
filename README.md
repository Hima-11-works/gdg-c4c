# Pollution Intelligence Platform

Project scaffold for a pollution intelligence MVP (PM2.5, H3 grid, weather-driven
spread predictions). **Weather ingestion and the pollution model are not
implemented yet** — every endpoint falls back to deterministic demo data
until real rows exist. So far the project has:
- a FastAPI backend: `/api/v1` (sensors, weather, grid, cells, alerts) plus
  health/readiness, with a consistent error shape and OpenAPI docs at `/docs`
- OpenAQ ingestion for PM2.5 (`python -m app.cli ingest`), behind a
  `PollutionDataProvider` interface so other sources (CPCB, satellite,
  private sensors) can be added later without touching anything downstream
- a database layer: schema, migrations, and a repository per entity
  (`SensorReading`, `WeatherReading`, `GridState`, `Forecast`, `Alert`)
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
| `OPENAQ_API_KEY` | ingestion | Required to ingest ([get one free](https://explore.openaq.org/register)). Leave blank to run everything else without it |
| `OPENAQ_BASE_URL`, `OPENAQ_TIMEOUT_SECONDS`, `OPENAQ_MAX_RETRIES`, `OPENAQ_LOCATIONS_LIMIT` | ingestion | OpenAQ adapter tuning — see `.env.example` |
| `INGEST_BBOX_MIN_LAT`/`MIN_LON`/`MAX_LAT`/`MAX_LON` | ingestion | Bounding box to ingest (default: San Francisco, matching the demo data) |
| `INGEST_MAX_READING_AGE_HOURS` | ingestion | A fetched reading older than this is dropped as stale |
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
│  │  ├─ domain/            # pure types, repository/provider Protocols, H3 validation — no I/O
│  │  ├─ ingestion/         # openaq.py (implemented); Open-Meteo/fixtures (future)
│  │  ├─ models/            # database table definitions (the schema)
│  │  ├─ services/          # business logic per resource, demo-data fallback, ingestion orchestration
│  │  ├─ cli.py             # dev commands — `python -m app.cli ingest`
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

## Ingestion

```bash
cd backend
python -m app.cli ingest                                    # bbox from .env
python -m app.cli ingest --min-lat 28.4 --min-lon 76.8 \
                          --max-lat 28.9 --max-lon 77.4      # override for one run
```

Requires `OPENAQ_API_KEY` in `.env` and the database running. Under Docker
Compose, run it inside the `api` container instead: `docker compose exec
api python -m app.cli ingest`. Fetches
recent PM2.5 readings for the configured bounding box from OpenAQ,
normalizes them into `SensorReading`, and saves them — skipping exact
duplicates (same source/sensor/pollutant/timestamp) and stale readings
(older than `INGEST_MAX_READING_AGE_HOURS`). A network or API failure
prints a message and exits 1; it never crashes with a traceback. There is
no scheduler yet — this is a manual trigger for local development. See
`docs/architecture.md` for the full fetch flow and how to add another
source (CPCB, satellite, private sensors) behind the same interface.

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
- OpenAQ ingestion writes `sensor_reading` rows when triggered manually
  (`python -m app.cli ingest`); nothing else writes yet — weather ingestion
  and the pollution model aren't implemented. Every `/api/v1/*` endpoint
  falls back to deterministic demo data (`app/services/demo_data.py`) when
  its repository query is empty — see `is_demo` in every response.
