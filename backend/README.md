# Air Health — Backend Service & Spatial Intelligence Pipeline

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL + PostGIS](https://img.shields.io/badge/PostgreSQL-16%20%2B%20PostGIS-4169E1?logo=postgresql&logoColor=white)](https://postgis.net/)
[![Uber H3](https://img.shields.io/badge/Uber%20H3-Hexagonal%20Grid-000000?logo=uber&logoColor=white)](https://h3geo.org/)

The backend is the core spatial intelligence, data fusion, and dispatch engine of the **Air Health** platform. It ingests multi-source satellite observations, official CAAQMS sensor data, weather parameters, and crowdsourced citizen evidence, producing high-resolution **Uber H3 hexagonal nowcasts and forecasts**, automated **hidden hotspot detection**, **economic corridor exposure assessments**, and **incident management**.

---

## 🏛️ Architecture & Core Components

```mermaid
flowchart TB
    subgraph INGESTION["1. Multi-Source Ingestion (`app.ingestion`)"]
        OP["OpenAQ CAAQMS<br/>Official Ground Monitors"]
        MET["Open-Meteo<br/>Wind, Boundary Layer, Temp, Rain"]
        FIR["NASA FIRMS<br/>VIIRS/MODIS Thermal Fires"]
        S5P["Copernicus CDSE<br/>Sentinel-5P UVAI & NO2"]
        CIT["Citizen Reports & Sensors<br/>Geotagged Reports & Readings"]
    end

    subgraph STORAGE["2. Spatial Storage (`app.models`)"]
        DB[("PostgreSQL 16 + PostGIS<br/>Spatial Indexing & Sensor Records")]
        GRID[("H3 Hexagonal State Store<br/>Multi-resolution L3–L8")]
    end

    subgraph PIPELINE["3. Processing Pipeline (`app.pipeline`)"]
        IDW["Inverse Distance Weighting (IDW)<br/>Spatial Nowcasting Engine"]
        PDI["Pollution Development Index (PDI)<br/>Meteorological Smog Trapping Index"]
        DISP["Advection-Dispersion Model<br/>1h, 3h, 6h Forward Forecasts"]
        HOT["Hidden Hotspot Corroborator<br/>Satellite + Ground Anomaly Matcher"]
        CORR["Corridor Exposure Evaluator<br/>Delhi-Kanpur, DMIC, EDFC"]
        ALRT["Alert Rule Engine<br/>CPCB NAQI Bands & Thresholds"]
    end

    subgraph SERVICES["4. Application Services (`app.services`)"]
        INC["Incident Lifecycle Engine<br/>State Transition Verification"]
        MED["Media Evidence Service<br/>EXIF Stripping & Raster Re-encoding"]
        FED["Federation Aggregator & Client<br/>Decentralized Model Synchronization"]
    end

    subgraph API["5. REST API Layer (`app.api`)"]
        V2["/api/v2/grid/*<br/>H3 Hexagonal Nowcasts & Forecasts"]
        V1["/api/v1/*<br/>Hotspots, Corridors, Incidents, Reports"]
        PROXY["/api/v1/tiles/*<br/>Satellite Raster Proxies (GIBS/CDSE)"]
    end

    INGESTION --> DB
    DB --> GRID
    GRID --> IDW
    GRID --> PDI
    IDW --> DISP
    PDI --> DISP
    DISP --> HOT
    DISP --> CORR
    DISP --> ALRT
    ALRT --> INC
    CIT --> MED
    MED --> DB
    PIPELINE --> V2
    SERVICES --> V1
    DB --> FED
```

---

## 🚀 Key Features

1. **Multi-Source Data Fusion**:
   - **Ground Monitoring**: Ingestion from OpenAQ CAAQMS stations across India.
   - **Meteorology**: High-resolution Open-Meteo weather parameters (boundary layer height, 10m wind $u/v$ components, temperature, precipitation).
   - **Spaceborne Thermal Anomalies**: NASA FIRMS VIIRS & MODIS active fire detections.
   - **Aerosol Screening**: Copernicus Sentinel-5P UV Aerosol Index (UVAI).
2. **Uber H3 Spatial Hexagonal Modeling**:
   - Dynamically indexes observations across H3 resolutions (Res 3 to Res 8).
   - Distance-decayed IDW interpolation with strict minimum-station safety guards.
   - Forward 1-hour, 3-hour, and 6-hour advection-dispersion forecast vectors.
3. **Automated Hidden Hotspot Detection**:
   - Identifies unmonitored emission sources by matching satellite aerosol spikes with citizen reports and local low-cost sensors.
4. **Economic Corridor Analysis**:
   - Spatio-temporal exposure routing along key freight arteries: **Delhi–Kanpur Interstate Corridor**, **DMIC (Delhi–Mumbai)**, and **EDFC (Eastern Dedicated Freight Corridor)**.
   - Honestly scored against withheld ground monitoring stations.
5. **Incident Management & Response Engine**:
   - Immutable audit history and strict state transitions: `REPORTED` → `ACKNOWLEDGED` → `EN ROUTE` → `ON SCENE` → `RESOLVED` (or `CANCELLED`).
   - Integrated with the companion [Fire Department Simulator](../partner_apps/fire_dept_simulator/).
6. **Privacy-Preserving Citizen Photo & Sensor Ingestion**:
   - Sniffs image bytes, strips sensitive EXIF metadata, and re-encodes rasters to secure non-identifiable derivatives before storage.
7. **Interoperable Federated Learning**:
   - Multi-party federated model aggregation allowing municipal and state pollution boards to train localized ridge-residual calibration models without sharing raw sensor data.

---

## 🛠️ Tech Stack

- **Runtime**: Python `3.11+`
- **Framework**: FastAPI (Asynchronous REST API)
- **Database**: PostgreSQL 16 with PostGIS extension
- **ORM / Migrations**: SQLAlchemy 2.0 + Alembic
- **Spatial / Math**: Uber `h3-py`, `numpy`, `scipy`, `shapely`, `pyproj`
- **Image Processing**: `Pillow` (EXIF stripping, re-encoding)
- **Testing**: `pytest`, `httpx`, `pytest-asyncio`

---

## ⚡ Setup & Local Development

### Option 1: Docker Compose (Recommended)

From the project root:
```bash
# Start PostgreSQL/PostGIS and FastAPI backend
docker compose up --build -d

# Check API health
curl http://localhost:8000/health/ready
```

Interactive API documentation will be available at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

### Option 2: Local Python Virtual Environment

1. **Navigate to the backend directory and set up a virtual environment:**
   ```bash
   cd backend
   python -m venv .venv
   ```

2. **Activate the virtual environment:**
   - **Windows (PowerShell)**:
     ```powershell
     .\.venv\Scripts\Activate.ps1
     ```
   - **Linux / macOS**:
     ```bash
     source .venv/bin/activate
     ```

3. **Install dependencies:**
   ```bash
   pip install -e ".[dev]"
   ```

4. **Configure environment variables:**
   ```powershell
   Copy-Item ../.env.example .env
   ```
   *Note: In `.env`, ensure `DATABASE_URL` points to your PostGIS database, e.g. `postgresql+asyncpg://postgres:postgres@localhost:5432/air_health`.*

5. **Run database migrations:**
   ```bash
   alembic upgrade head
   ```

6. **Start the development server:**
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

---

## 🔄 Running the Processing Pipeline

The platform includes a unified pipeline runner that executes all spatial processing stages end-to-end:

```bash
# In Docker:
docker compose exec api python -m app.pipeline.run

# Or locally:
python -m app.pipeline.run
```

### Operational CLI Commands

| Command | Description | Example |
|---|---|---|
| `python -m app.pipeline.run` | Run the complete pipeline end-to-end | `python -m app.pipeline.run` |
| `python -m app.cli ingest` | Ingest CAAQMS PM2.5 observations | `python -m app.cli ingest` |
| `python -m app.cli ingest-weather` | Ingest Open-Meteo weather parameters | `python -m app.cli ingest-weather` |
| `python -m app.cli ingest-fires` | Ingest NASA FIRMS VIIRS fire anomalies | `python -m app.cli ingest-fires --days 1` |
| `python -m app.cli forecast` | Run deterministic H3 advection-dispersion forecast | `python -m app.cli forecast` |
| `python -m app.cli corridor-evaluate` | Score corridor exposure against withheld stations | `python -m app.cli corridor-evaluate --corridor delhi-kanpur` |
| `python -m app.cli hotspot-scan` | Run hidden hotspot candidate screener | `python -m app.cli hotspot-scan` |
| `python -m app.cli federation-demo` | Run local multi-party federated model demo | `python -m app.cli federation-demo` |

---

## 🌐 Federated Learning Architecture

The backend includes a complete federated training and aggregation subsystem:

```bash
# 1. Run local dual-partition simulation:
python -m app.cli federation-demo

# 2. Start standalone central model aggregator:
python -m app.federation_aggregator --port 8010 --run-id fed-run-01 --client-keys var/federation/client_keys.json

# 3. Start regional participant client nodes:
python -m app.federation_client --participant region-a --aggregator-url http://localhost:8010
python -m app.federation_client --participant region-b --aggregator-url http://localhost:8010
```

Read the full protocol specification in [`docs/api/federation.md`](../docs/api/federation.md).

---

## 🧪 Testing & Code Quality

```bash
# Run complete test suite (unit, regression, and contracts)
pytest

# Run tests with coverage report
pytest --cov=app --cov-report=term-missing

# Lint and format checks
ruff check .
ruff format --check .
```
