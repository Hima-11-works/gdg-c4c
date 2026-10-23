# Air Health — Interactive Web Map Prototype

[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6%2B-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Vite](https://img.shields.io/badge/Vite-6.0%2B-646CFF?logo=vite&logoColor=white)](https://vitejs.dev/)
[![MapLibre GL](https://img.shields.io/badge/MapLibre%20GL-5.0%2B-396B9C?logo=maplibre&logoColor=white)](https://maplibre.org/)

The **Air Health Frontend** is a prototype web map for exploring multi-resolution H3 air-quality estimates, available forecasts, satellite-derived context, corridor runs, and citizen-reported evidence. Source freshness and coverage depend on backend configuration. Candidate events require human review; corridor views do not provide logistics or travel recommendations.

---

## 🏛️ Visual Architecture & Layers

```mermaid
flowchart TB
    subgraph UI["1. User Interface & Controls"]
        MAP["MapLibre GL Base Map<br/>CartoDB Positron / Satellite Basemap"]
        TIMELINE["Timeline Slider<br/>Nowcast · +1h · +3h · +6h Forecast"]
        PANELS["Interactive Analytics Panels<br/>Corridors · Hotspots · Alerts · Cell Details"]
    end

    subgraph LAYERS["2. Dynamic Map Layers"]
        H3_HEX["Uber H3 Hexagonal Grid<br/>Dynamic LOD: Res 3 to Res 7"]
        IDW_HEAT["Continuous IDW Surface<br/>Smooth Pollution Heatmap"]
        WIND["Wind Vectors<br/>Available Weather Feed"]
        FIRMS["NASA FIRMS Thermal Markers<br/>VIIRS Active Fire Detections"]
        PHOTOS["Citizen Photo Evidence<br/>Submitted Reports for Review"]
        GIBS["NASA GIBS Satellite Overlays<br/>MODIS TrueColor & Deep Blue AOD"]
    end

    subgraph STATE["3. State & Data Layer"]
        API["Backend API Client (`src/lib/api.ts`)<br/>V2 Grid, V1 Corridors, Hotspots, Alerts"]
        LOD["LOD Resolution Controller (`src/lib/lod.ts`)<br/>Viewport-Aware Hexagon Densification"]
        SCALE["CPCB NAQI Color Scale (`src/lib/colors.ts`)<br/>Standardized Health Risk Palette"]
    end

    API --> STATE
    STATE --> LAYERS
    LAYERS --> MAP
    UI --> STATE
```

---

## 🚀 Key Features

### 1. Dynamic Hexagonal Grid (Uber H3 LOD)
The viewport automatically adjusts its spatial granularity based on user zoom level:
- **Res 3 (~110 km)**: National overview across India.
- **Res 4 (~41 km)**: Regional / Interstate level.
- **Res 5 (~16 km)**: State-level and industrial corridor focus.
- **Res 6 (~6 km)**: District and Municipal Corporation boundary analysis.
- **Res 7 (~2.2 km)**: Ward and hyper-local neighborhood resolution.

### 2. Multi-Layer Environmental Visualization
- **Discrete vs Continuous**: Toggle between structured H3 hexagons and seamless Inverse Distance Weighting (IDW) heat surfaces.
- **Wind Vector Dynamics**: Animated particle trails driven by the latest available Open-Meteo $u/v$ wind components.
- **NASA FIRMS Fires**: Visual fire anomaly markers highlighting active agricultural stubble burning and industrial flares.
- **NASA GIBS WMS Satellite Overlays**: Satellite imagery and Aerosol Optical Depth (AOD) products proxied through the backend; date and cadence depend on the selected product.

### 3. Published Corridor Forecast Inspection
- Inspect forecast runs for configured corridor geometry when a published run is available:
  - **Delhi–Kanpur Interstate Corridor** (Indo-Gangetic Plain axis).
  - **DMIC** (Delhi–Mumbai Industrial Corridor).
  - **EDFC** (Eastern Dedicated Freight Corridor).
- Geometry may be illustrative. The panel displays run provenance and validation status; it does not route freight or recommend travel windows.

### 4. Hotspot Candidate Review
- Shows candidate evidence assembled from available satellite context, NASA FIRMS detections, and citizen smoke reports.
- A candidate is an unverified signal for human review. The dashboard does not confirm an emission source or dispatch responders.

### 5. Citizen Photo Evidence Inspection
- Interactive map markers showing citizen-reported smoke and fire plumes.
- Inspect photo evidence after backend media processing strips image metadata.

---

## 🛠️ Project Structure

```text
frontend/
├── public/                 # Static assets, fallback boundary data, Indian locations
│   └── data/               # GeoJSON boundaries & Indian place database
├── src/
│   ├── components/         # React UI and map components
│   │   ├── MapView.tsx     # MapLibre GL map instance and layer rendering
│   │   ├── CorridorPanel.tsx # Economic corridor routing and time-series charts
│   │   ├── HotspotPanel.tsx # Satellite candidate hotspot inspection drawer
│   │   ├── TimelineSlider.tsx # Interactive forecast horizon scrubber
│   │   ├── AlertBanner.tsx # NAQI warning and critical alert banner
│   │   └── LayerToggle.tsx # Layer visibility and satellite overlay switch
│   ├── lib/                # Utilities and core algorithms
│   │   ├── api.ts          # Strongly-typed backend API client
│   │   ├── lod.ts          # Viewport zoom to H3 resolution mapping
│   │   ├── colors.ts       # CPCB NAQI color scale definitions
│   │   └── types.ts        # TypeScript data contracts
│   ├── App.tsx             # Root dashboard layout and panel composition
│   └── main.tsx            # Application entry point
├── package.json
└── vite.config.ts          # Vite build and proxy configuration
```

---

## ⚡ Getting Started

### Prerequisites
- **Node.js**: `^20.19.0` or `>=22.12.0`
- **npm**: `>=10.0.0`

### Installation & Run

1. **Install dependencies:**
   ```bash
   cd frontend
   npm ci
   ```

2. **Configure environment:**
   ```powershell
   Copy-Item .env.example .env
   ```
   Set your API target in `.env`:
   ```env
   VITE_API_BASE_URL=http://localhost:8000
   ```

3. **Start development server:**
   ```bash
   npm run dev
   ```
   Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## 📜 Available Scripts

| Command | Action |
|---|---|
| `npm run dev` | Starts Vite local development server at `http://localhost:5173` |
| `npm run build` | Runs TypeScript compilation and generates production bundle in `dist/` |
| `npm run preview` | Previews the production build locally |
| `npm run lint` | Runs `oxlint` fast linter across source files |
| `npm run format` | Formats code with `prettier` |
