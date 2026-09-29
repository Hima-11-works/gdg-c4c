# Air Health — Federated Climate Action & Pollution Intelligence

[![Hourly pipeline](https://github.com/Hima-11-works/gdg-c4c/actions/workflows/pipeline.yml/badge.svg)](https://github.com/Hima-11-works/gdg-c4c/actions/workflows/pipeline.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![PostgreSQL + PostGIS](https://img.shields.io/badge/PostgreSQL-16%20%2B%20PostGIS-4169E1?logo=postgresql&logoColor=white)
![Uber H3](https://img.shields.io/badge/Uber%20H3-Hexagonal%20Grid-000000?logo=uber&logoColor=white)
![Flutter](https://img.shields.io/badge/Flutter-Partner%20Mobile%20Apps-02569B?logo=flutter&logoColor=white)

An AI-powered, federated climate action platform designed for Indian cities, states, and citizens. It bridges the critical divide between sparse macro-level monitoring and hyper-local pollution events by fusing **citizen science (photos, local sensor readings)** with **satellite observations (Copernicus Sentinel-5P, NASA FIRMS)** and **high-resolution meteorology**. Featuring multi-horizon air quality forecasting along key economic corridors, automated hidden hotspot detection, an operational incident dispatch workflow for rapid intervention, and a federated machine learning architecture for privacy-preserving inter-agency collaboration.

Built for Google's **Code for Communities (GDG C4C)** Hackathon.

---

## 🎯 The Hackathon Problem & Challenge

### The Problem
> **Macro vs Hyper-Local Blindspots in Indian Air Quality**  
> Major Indian cities monitor macro-level air quality through a limited network of continuous ambient air quality monitoring stations (CAAQMS). However, they consistently miss hyper-local, high-consequence pollution events — unmonitored industrial emissions, large-scale agricultural stubble burning across Punjab, Haryana, and Western UP, brick kiln clusters, and sudden seasonal smog episodes. The absence of real-time, granular spatial data prevents coordinated, cross-jurisdictional climate action and directly threatens public health.

### The Challenge
> **An AI-Powered, Federated Climate Action Platform**  
> Build an interoperable climate action platform that combines citizen-sourced data (geotagged photos, local low-cost sensor readings) with satellite imagery and meteorological data. It must detect hidden pollution hotspots, forecast air quality spikes across major economic corridors, and alert relevant authorities for rapid intervention — designed for interoperability so Indian cities and states can share predictive models and coordinate emergency resources.

### 💡 How This Platform Solves the Challenge

| Challenge Requirement | How Our Platform Implements It | Shipped Artifacts & Systems |
|---|---|---|
| **Citizen-Sourced Ground Truth** | Mobile-first citizen reporting with privacy-preserving EXIF-stripped photo evidence, real-time smoke/fire submissions, and local low-cost PM2.5 sensor integration. | Flutter Mobile App ([`air_health_flutter`](partner_apps/air_health_flutter/)), Photo Derivatives API, Community Sensor Endpoints |
| **Satellite & Meteorological Ingestion** | Automated ingestion of Copernicus Sentinel-5P UV Aerosol Index (UVAI), NASA FIRMS VIIRS active fire anomalies, OpenAQ CAAQMS stations, and Open-Meteo weather parameters (wind, boundary layer, precipitation). | Ingestion Pipeline (`app.ingestion`), Source Inventory ([`ENVIRONMENTAL_SOURCE_INVENTORY.md`](docs/ENVIRONMENTAL_SOURCE_INVENTORY.md)) |
| **Hidden Hotspot Detection** | Autonomous hotspot candidate generation flagging unmonitored emission surges by correlating satellite aerosol anomalies with citizen smoke reports and local sensor spikes. | Hotspot Service (`app.services.hotspot_service`), Hotspot Review Workflow ([`docs/api/hotspots.md`](docs/api/hotspots.md)) |
| **Economic Corridor Forecasting** | Spatio-temporal exposure modeling along major Indian freight and transit corridors to predict air quality spikes and identify lower-exposure transit windows. | Delhi–Kanpur Interstate Corridor, DMIC (Delhi-Mumbai), EDFC Freight Axis ([`docs/api/corridor-evaluation.md`](docs/api/corridor-evaluation.md)) |
| **Rapid Authority Intervention** | Automated alert generation with incident lifecycle management (`REPORTED` → `ACKNOWLEDGED` → `EN ROUTE` → `ON SCENE` → `RESOLVED`) connected to an incident response console. | Incident Engine, Fire & Authority Response Simulator ([`fire_dept_simulator`](partner_apps/fire_dept_simulator/)) |
| **Interoperable Federation** | Decentralized, multi-client/aggregator architecture allowing municipal corporations and state pollution control boards (SPCBs) to train local models and exchange signed parameter updates without sharing raw citizen or proprietary sensor data. | Federation Aggregator & Client CLI (`app.federation_*`), Federation Protocol ([`docs/api/federation.md`](docs/api/federation.md)) |

---

## 📑 Contents

- [System Architecture](#system-architecture)
- [Key Platform Pillars](#key-platform-pillars)
- [Granular Spatial Grid & Map](#granular-spatial-grid--map)
- [Environmental Data Sources](#environmental-data-sources)
- [Partner Mobile Apps & Ready-to-Install APKs](#partner-mobile-apps--ready-to-install-apks)
- [Quick Start](#quick-start)
- [Live Pipeline & Ingestion](#live-pipeline--ingestion)
- [Federation & Inter-Agency Workflows](#federation--inter-agency-workflows)
- [API Index & Contracts](#api-index--contracts)
- [Development & Testing](#development--testing)
- [Limits & Operational Roadmap](#limits--operational-roadmap)
- [Repository Guide](#repository-guide)

---

## 🏛️ System Architecture

```mermaid
flowchart TB
    subgraph SENSORS["1. Citizen & Ground Sensing"]
        CP["Citizen Photo Evidence<br/>EXIF-stripped & Verified"]
        CR["Citizen Smoke/Fire Reports"]
        CS["Local / Low-Cost Sensors<br/>Community PM2.5"]
        AQ["OpenAQ / CPCB CAAQMS<br/>Official Ground Monitors"]
    end

    subgraph SPACE["2. Spaceborne & Meteorological Inputs"]
        S5P["Copernicus Sentinel-5P<br/>UV Aerosol Index & NO2"]
        FIRMS["NASA FIRMS<br/>VIIRS/MODIS Thermal Fires"]
        METEO["Open-Meteo<br/>Wind, Boundary Layer, Temp"]
        GIBS["NASA GIBS<br/>TrueColor & AOD Imagery"]
    end

    subgraph CORE["3. Core Spatial Intelligence & Storage"]
        DB[("PostgreSQL 16 + PostGIS<br/>Sensor, Weather & Grid State")]
        H3["Uber H3 Hexagonal Grid<br/>Multi-resolution L3–L7"]
        IDW["IDW Nowcasting Engine"]
        PDI["Heuristic Pollution<br/>Development Index (PDI)"]
        DISP["Advection-Dispersion Model<br/>1h, 3h, 6h Forecasts"]
    end

    subgraph ENGINES["4. Action & Decision Engines"]
        HOT["Hidden Hotspot Engine<br/>Candidate Screening & Corroboration"]
        CORR["Economic Corridor Engine<br/>Delhi-Kanpur, DMIC, EDFC"]
        ALERTS["Rule-Based & Predictive Alerts<br/>Threshold & Rate-of-Change"]
        INC["Incident Lifecycle Manager<br/>Assignment & Dispatch"]
    end

    subgraph FED["5. Interoperable Federation Layer"]
        FED_CLI["Regional SPCB Client Nodes<br/>Local Training & Verification"]
        FED_AGG["Central Model Aggregator<br/>Signed Manifests & Parameter Fusion"]
    end

    subgraph APPS["6. Client & Authority Interfaces"]
        WEB["React 19 + MapLibre Web Map<br/>Interactive Multi-Layer Console"]
        CIT_APP["Air Health Flutter App<br/>Citizen Companion & Field Reporting"]
        AUTH_APP["Fire Dept Simulator<br/>Incident Dispatch & Unit Tracking"]
    end

    SENSORS --> DB
    SPACE --> DB
    DB --> H3
    H3 --> IDW
    H3 --> PDI
    IDW --> DISP
    PDI --> DISP
    DISP --> HOT
    DISP --> CORR
    DISP --> ALERTS
    ALERTS --> INC
    
    DB <--> FED_CLI
    FED_CLI <--> FED_AGG
    
    DB --> WEB
    INC --> AUTH_APP
    CR --> CIT_APP
    CP --> CIT_APP
    CS --> CIT_APP
    CIT_APP --> DB
    GIBS -.-> WEB
```

---

## 🚀 Key Platform Pillars

### 1. 🔍 Hidden Hotspot Detection (Satellite + Citizen Corroboration)
Traditional monitoring misses rural stubble burning and industrial bypass events between macro stations. The platform continuously ingests **Copernicus Sentinel-5P UV Aerosol Index (UVAI)** granules. When aerosol anomalies are detected, the system generates **Hotspot Candidates** and attempts cross-verification with:
- **NASA FIRMS** satellite thermal fire pixels.
- **Citizen-reported** smoke plumes and geotagged field photos.
- **Local sensor** reading spikes in downwind H3 cells.
Candidates are ranked with confidence metrics and routed to authorities for field verification rather than false alarms.

### 2. 🚚 Economic Corridor Forecasting & Exposure Routing
Major industrial and freight corridors in Northern and Western India (such as the Indo-Gangetic Plain and freight corridors) concentrate heavy vehicular emissions and experience severe winter smog trapping. The platform provides:
- **Delhi–Kanpur Interstate Corridor** (Indo-Gangetic Plain agricultural and industrial axis).
- **DMIC** (Delhi–Mumbai Industrial Corridor connecting NCR, Rajasthan, Gujarat, and Maharashtra).
- **EDFC** (Eastern Dedicated Freight Corridor).
Evaluates forward 1h, 3h, and 6h exposure profiles along corridor cells, enabling logistics coordinators to schedule transit windows during lower-exposure conditions.

### 3. 📱 Citizen Science & Ground Truth (Flutter Companion App)
Citizens are equipped with the **`air_health_flutter`** mobile app:
- **Local Air Quality & Exposure**: Real-time CPCB AQI scoring, personalized health advisories based on sensitivity profiles (asthma, elderly, children), and hourly forecasts.
- **Ground-Truth Field Reporting**: Citizens can capture smoke and fire incidents. The app automatically sanitizes uploads by **stripping sensitive EXIF metadata** before transmission.
- **Low-Cost Sensor Submissions**: Citizens and community organizations can pipe micro-sensor PM2.5 data directly into the regional grid.

### 4. 🚨 Rapid Authority Intervention (Incident Response Console)
When alert thresholds are breached or citizen reports are corroborated:
- Incidents are automatically instantiated with geographic coordinates, H3 cell indices, and priority levels.
- The **`fire_dept_simulator`** Flutter operational console allows municipal authorities and fire departments to receive dispatch requests, view attached evidence photos, and track real-time status transitions:
  `REPORTED` ➔ `ACKNOWLEDGED` ➔ `EN ROUTE` ➔ `ON SCENE` ➔ `RESOLVED`

### 5. 🤝 Federated Climate Intelligence Across States & Cities
Air pollution does not stop at administrative boundaries. The platform features an **interoperable federated learning architecture**:
- **Decentralized State/City Nodes**: Each regional authority (e.g., DPCC in Delhi, PPCB in Punjab, UPPCB in Uttar Pradesh) retains complete ownership of its local ground sensor data and citizen reports.
- **Signed Parameter Updates**: Regional client nodes train localized ridge-residual calibration models and exchange cryptographic model updates over HTTP with an aggregator node.
- **Privacy-Preserving**: Raw sensor measurements and citizen identities never leave the local node's perimeter.

---

## 🗺️ Granular Spatial Grid & Map

The web application uses the **Uber H3 Discrete Global Grid System** to discretize spatial pollution fields. As users explore, the viewport dynamically adapts its H3 resolution:

| Map Level | Viewport Zoom | H3 Resolution | Hex Cell Diameter | Requested Coverage |
|:---:|:---:|:---:|:---:|---|
| **Level 1** | `< 6` | **H3 Res 3** | ~110 km | National India overview (aggregated OpenAQ stations) |
| **Level 2** | `6 to < 7` | **H3 Res 4** | ~41 km | Dynamic viewport boundary |
| **Level 3** | `7 to < 9` | **H3 Res 5** | ~16 km | State-level focus & major industrial corridors |
| **Level 4** | `9 to < 10.5` | **H3 Res 6** | ~6 km | District / Municipal Corporation view |
| **Level 5** | `10.5 to 12` | **H3 Res 7** | ~2.2 km | Hyper-local ward & neighborhood analysis |

- **Layer Controls**: Toggle between discrete H3 hexagons and continuous IDW-interpolated heat surfaces.
- **Overlays**: Real-time wind vector particles, active NASA FIRMS thermal detections, citizen photo markers, active incidents, and NASA GIBS satellite raster overlays (True Color / Aerosol Optical Depth).

---

## 📊 Environmental Data Sources

| Source | Role in Platform | Provenance & Access |
|---|---|---|
| [**OpenAQ**](https://openaq.org/) | Ground-level CAAQMS PM2.5 observations | Official CPCB & SPCB monitoring stations (API key configured) |
| [**Open-Meteo**](https://open-meteo.com/) | High-resolution meteorological data | Wind speed/direction, temperature, precipitation, boundary layer height |
| [**Copernicus CDSE**](https://dataspace.copernicus.eu/) | Sentinel-5P UV Aerosol Index (UVAI) & NO2 | Screening for elevated smoke and industrial plumes |
| [**NASA FIRMS**](https://firms.modaps.eosdis.nasa.gov/) | VIIRS / MODIS satellite fire anomalies | Rapid corroboration of agricultural stubble burning and industrial flares |
| [**NASA GIBS**](https://wiki.earthdata.nasa.gov/display/GIBS) | Real-time true-color & AOD imagery | Visual satellite confirmation overlays |
| [**geoBoundaries**](https://www.geoboundaries.org/) | Administrative boundary hierarchies | Official India State and District boundaries (ODC-ODbL) |
| [**GeoNames**](https://www.geonames.org/) | Geographic gazetteer | Fast location search across Indian towns and cities (CC BY 4.0) |

*Full licensing, retention, and fallbacks are documented in [`docs/ENVIRONMENTAL_SOURCE_INVENTORY.md`](docs/ENVIRONMENTAL_SOURCE_INVENTORY.md).*

---

## 📱 Partner Mobile Apps & Ready-to-Install APKs

Pre-built release APKs are available directly in [`apks/`](apks/) for immediate testing on Android devices:

| Mobile Application | Description | Architecture | Download Link |
|---|---|---|---|
| **Air Health Companion** | Citizen air quality tracker, sensitivity health advisories, photo & sensor reporting | **ARM 64-bit** (Modern Phones)<br/>**ARM 32-bit**<br/>Universal Fat APK | [Download ARM64](apks/air_health_flutter-arm64.apk)<br/>[Download ARM32](apks/air_health_flutter-arm32.apk)<br/>[Download Universal](apks/air_health_flutter-release.apk) |
| **Fire Dept Simulator** | Incident response console for fire and pollution control authorities | **ARM 64-bit** (Modern Phones)<br/>**ARM 32-bit**<br/>Universal Fat APK | [Download ARM64](apks/fire_dept_simulator-arm64.apk)<br/>[Download ARM32](apks/fire_dept_simulator-arm32.apk)<br/>[Download Universal](apks/fire_dept_simulator-release.apk) |

### Installing via ADB:
```bash
adb install apks/air_health_flutter-arm64.apk
adb install apks/fire_dept_simulator-arm64.apk
```

---

## ⚡ Quick Start

### Prerequisites
- **Docker Desktop** (with Compose v2)
- **Node.js** (`^20.19.0` or `>=22.12.0`)
- **Git**

### Step-by-Step Local Deployment

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Hima-11-works/gdg-c4c.git
   cd gdg-c4c
   ```

2. **Set up environment configurations:**
   ```powershell
   Copy-Item .env.example .env
   Copy-Item frontend/.env.example frontend/.env
   ```
   *Note: `.env.example` comes pre-configured with `DEMO_MODE=true` so you can evaluate the entire pipeline immediately without needing external API keys.*

3. **Start PostgreSQL + PostGIS and FastAPI Backend:**
   ```bash
   docker compose up --build -d
   ```
   *The backend applies database migrations automatically on startup.*

4. **Populate demo data and trigger initial pipeline:**
   ```bash
   docker compose exec api python -m app.pipeline.run
   ```

5. **Start the React + MapLibre Web Map:**
   ```bash
   cd frontend
   npm ci
   npm run dev
   ```

6. **Access the Interfaces:**
   - 🌐 **Web Map Explorer**: [http://localhost:5173](http://localhost:5173)
   - 📖 **Interactive API Documentation (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)
   - 🩺 **Health Check**: [http://localhost:8000/health/ready](http://localhost:8000/health/ready)

---

## 🔄 Live Pipeline & Scheduled Ingestion

To feed the system with real-time live satellite and ground data, populate the following credentials in your root `.env`:

```env
DEMO_MODE=false
OPENAQ_API_KEY=your_openaq_api_key
CDSE_REFRESH_TOKEN=your_copernicus_data_space_token
FIRMS_MAP_KEY=your_nasa_firms_map_key
INGEST_BBOX_WEST=76.8
INGEST_BBOX_SOUTH=28.2
INGEST_BBOX_EAST=77.6
INGEST_BBOX_NORTH=28.9
```

- **Hourly Automation**: An automated workflow in [`.github/workflows/pipeline.yml`](.github/workflows/pipeline.yml) triggers hourly ingestion, updating H3 grid states, advection forecasts, and alert registries.
- **Manual Pipeline Trigger**:
  ```bash
  docker compose exec api python -m app.pipeline.run
  ```

---

## 🌐 Federation & Inter-Agency Workflows

The platform supports cross-jurisdictional collaboration through governed, privacy-preserving federated model updates:

### Two-Partition Local Demo
Simulates two distinct regional authorities training local models and aggregating them:
```bash
# In backend virtualenv:
python -m app.cli federation-demo
```
Inspect public status:
```bash
curl http://localhost:8000/api/v1/federation/status
```

### Multi-Process Client/Aggregator Workflow
Run an independent aggregator and two separate regional client processes:
```powershell
# 1. Start central model aggregator:
python -m app.federation_aggregator --port 8010 --run-id fed-run-01 --client-keys var/federation/client_keys.json

# 2. Start Regional Client A (e.g., Delhi NCR node):
python -m app.federation_client --participant region-a --aggregator-url http://localhost:8010

# 3. Start Regional Client B (e.g., Punjab / Haryana node):
python -m app.federation_client --participant region-b --aggregator-url http://localhost:8010
```
*Full protocol specification in [`docs/api/federation.md`](docs/api/federation.md).*

---

## 🔌 API Index & Contracts

| Endpoint Group | Method & Route | Description | Contract Doc |
|---|---|---|---|
| **Grid State** | `GET /api/v2/grid/current` | Active H3 grid cells with nowcasted PM2.5 & PDI | [Architecture](docs/architecture.md) |
| **Forecasts** | `GET /api/v2/grid/forecast` | 1h, 3h, 6h advection-dispersion predictions | [Architecture](docs/architecture.md) |
| **Corridors** | `GET /api/v1/corridors/{id}/evaluation` | Freight corridor exposure analysis & transit scoring | [Corridor Contract](docs/api/corridor-evaluation.md) |
| **Hotspots** | `GET /api/v1/hotspots` | Satellite UVAI + FIRMS hotspot candidates | [Hotspots Contract](docs/api/hotspots.md) |
| **Citizen Reports** | `POST /api/v1/citizen-reports` | Citizen smoke/fire submissions with photos | [Reports Contract](docs/api/citizen-reports.md) |
| **Photo Evidence** | `POST /api/v1/citizen-photos` | Upload field photos (strips EXIF, generates thumbs) | [Photos Contract](docs/api/citizen-photos.md) |
| **Sensors** | `POST /api/v1/citizen-sensor-readings` | Ingest crowdsourced community PM2.5 readings | [Sensors Contract](docs/api/citizen-sensor-readings.md) |
| **Incidents** | `GET /api/v1/incidents` | Operational incident queue for emergency response | [Incidents Contract](docs/api/incidents.md) |
| **Federation** | `GET /api/v1/federation/status` | Current inter-agency federation run & model status | [Federation Contract](docs/api/federation.md) |

---

## 🛠️ Development & Testing

### Backend (Python 3.11+)
```bash
cd backend
python -m venv .venv
# Activate virtual environment (.venv\Scripts\Activate.ps1 or source .venv/bin/activate)
pip install -e ".[dev]"
alembic upgrade head
pytest
ruff check .
```

### Frontend (React 19 + TypeScript + MapLibre)
```bash
cd frontend
npm ci
npm run lint
npm run build
```

### Flutter Partner Apps
```bash
cd partner_apps/air_health_flutter
flutter analyze
flutter test

cd ../fire_dept_simulator
flutter analyze
flutter test
```

---

## ⚠️ Limits & Operational Roadmap

1. **Station Density**: Continuous ground CAAQMS stations are concentrated in major metropolitan hubs like Delhi NCR; rural Indo-Gangetic plain coverage relies on satellite estimation and citizen sensors.
2. **Satellite Screening**: Sentinel-5P UVAI indicates aerosol presence in the atmospheric column; it is a screening signal that requires FIRMS or ground validation before issuing dispatch orders.
3. **Forecasting Scope**: The current pipeline uses a deterministic advection-dispersion model incorporating wind and boundary-layer dynamics; complex chemical transformation models (secondary aerosol formation) are slated for subsequent phases.
4. **Federation Maturation**: Shipped federation demonstrates cryptographically signed model parameter exchange; production rollout requires formal multi-party differential privacy guarantees.

See [`docs/GO_LIVE.md`](docs/GO_LIVE.md) for the pre-deployment checklist and operational safety procedures.

---

## 📂 Repository Guide

```text
├── .github/workflows/          # CI/CD and hourly scheduled ingestion pipeline
├── apks/                       # Pre-compiled Android release APKs (ARM64 & ARM32)
├── backend/                    # FastAPI service, SQLAlchemy/PostGIS models, pipeline
│   ├── app/
│   │   ├── api/                # REST endpoints (grid, alerts, hotspots, corridors, etc.)
│   │   ├── domain/             # Core domain models, protocols, H3 utilities
│   │   ├── ingestion/          # OpenAQ, Open-Meteo, NASA FIRMS, CDSE adapters
│   │   ├── pipeline/           # Pipeline runner & composition root
│   │   ├── services/           # Dispersion modeling, PDI estimation, incident logic
│   │   └── federation_*        # Federated learning client and aggregator modules
├── frontend/                   # React 19 web application (MapLibre, Tailwind, H3 layers)
├── partner_apps/               # Flutter cross-platform mobile apps
│   ├── air_health_flutter/     # Citizen health companion & field reporting app
│   └── fire_dept_simulator/    # Authority incident response & dispatch console
├── docs/                       # Architecture specifications and API contracts
│   ├── api/                    # OpenAPI contracts for hotspots, corridors, federation
│   ├── architecture.md         # In-depth architectural blueprint
│   ├── GO_LIVE.md              # Production deployment checklist
│   └── ENVIRONMENTAL_SOURCE_INVENTORY.md # Sensor & satellite provenance details
└── docker-compose.yml          # Container configuration for API and PostGIS database
```

---

*Air Health — Empowering citizens, authorities, and cities to breathe cleaner air through transparent, federated climate action.*
