import {
  EXPOSURE_LABEL,
  EXPOSURE_METRIC_TOOLTIP,
  METRIC_LABEL,
  PDI_LABEL,
  PDI_TOOLTIP,
  PM25_METRIC_TOOLTIP,
} from '../lib/format'
import { useMapUi } from '../state/MapUiContext'
import { SidePanel } from './SidePanel'
import type { MapViewMode } from '../state/mapUiReducer'
import type { MapMetric } from '../lib/format'

const VIEW_MODES: { mode: MapViewMode; label: string; title: string }[] = [
  { mode: 'hex', label: 'Hex cells', title: 'Discrete H3 hexagons (one value per cell)' },
  { mode: 'smooth', label: 'Smooth', title: 'Smooth continuous field rendered from the same values' },
]

const METRICS: { metric: MapMetric; label: string; title: string }[] = [
  { metric: 'pm25', label: METRIC_LABEL.pm25, title: PM25_METRIC_TOOLTIP },
  { metric: 'exposure', label: EXPOSURE_LABEL, title: EXPOSURE_METRIC_TOOLTIP },
]

export function LayerToggle() {
  const { state, dispatch } = useMapUi()

  return (
    <SidePanel
      id="settings-panel"
      side="up"
      open={state.settingsOpen}
      onToggle={() => dispatch({ type: 'TOGGLE_SETTINGS' })}
      label={state.settingsOpen ? 'Hide settings and layers' : 'Show settings and layers'}
    >
      <div className="panel layer-toggle">
        <p className="layer-toggle-heading">Measure</p>
        <div className="view-mode" role="group" aria-label="Map measure">
          {METRICS.map((option) => (
            <button
              key={option.metric}
              type="button"
              className={`view-mode-option ${state.mapMetric === option.metric ? 'active' : ''}`}
              aria-pressed={state.mapMetric === option.metric}
              title={option.title}
              onClick={() => dispatch({ type: 'SET_MAP_METRIC', metric: option.metric })}
            >
              {option.label}
            </button>
          ))}
        </div>

        <p className="layer-toggle-heading">Rendering</p>
        <div className="view-mode" role="group" aria-label="Pollution rendering">
          {VIEW_MODES.map((option) => (
            <button
              key={option.mode}
              type="button"
              className={`view-mode-option ${state.viewMode === option.mode ? 'active' : ''}`}
              aria-pressed={state.viewMode === option.mode}
              title={option.title}
              onClick={() => dispatch({ type: 'SET_VIEW_MODE', mode: option.mode })}
            >
              {option.label}
            </button>
          ))}
        </div>

        <label
          className="layer-toggle-option"
          title="Draw a line on the boundary between PM2.5 ranges - hex edges in the hex view, iso-lines across the smooth field in the smooth view"
        >
          <input
            type="checkbox"
            checked={state.contrast}
            onChange={() => dispatch({ type: 'TOGGLE_CONTRAST' })}
          />
          Contrast ranges
        </label>

        <label className="layer-toggle-option" title={PDI_TOOLTIP}>
          <input
            type="checkbox"
            checked={state.showPdi}
            onChange={() => dispatch({ type: 'TOGGLE_PDI' })}
          />
          Show {PDI_LABEL} layer
        </label>

        <div className="layer-toggle-extra" role="group" aria-label="Signal layers">
          <label
            className="layer-toggle-option"
            title="NASA GIBS VIIRS Suomi-NPP True Color daily satellite imagery, drawn as a raster basemap under the hex grid (yesterday's UTC composite, real imagery)"
          >
            <input
              type="checkbox"
              checked={state.showSatelliteImagery}
              onChange={() => dispatch({ type: 'TOGGLE_SATELLITE_IMAGERY' })}
            />
            Live Satellite Imagery (VIIRS)
          </label>

          <label
            className="layer-toggle-option"
            title="Real NASA FIRMS active thermal anomalies (VIIRS NRT, last 24h) - ingested by the backend and read from GET /api/v1/fires"
          >
            <input
              type="checkbox"
              checked={state.showActiveFires}
              onChange={() => dispatch({ type: 'TOGGLE_ACTIVE_FIRES' })}
            />
            Active Fires (NASA FIRMS)
          </label>

          <label
            className="layer-toggle-option"
            title="NASA GIBS VIIRS Deep Blue Aerosol Optical Depth (550 nm) - a real daily satellite smog proxy over India, drawn at 60% opacity so the hex grid stays visible"
          >
            <input
              type="checkbox"
              checked={state.showSeasonalSmog}
              onChange={() => dispatch({ type: 'TOGGLE_SEASONAL_SMOG' })}
            />
            Seasonal Smog (Aerosol Optical Depth)
          </label>

          <label
            className="layer-toggle-option"
            title="Copernicus Sentinel-5P tropospheric NO2 via WMS. Inert until VITE_NO2_WMS_URL is set to a GetMap endpoint - no fabricated data is shown in the meantime."
          >
            <input
              type="checkbox"
              checked={state.showIndustrialEmissions}
              onChange={() => dispatch({ type: 'TOGGLE_INDUSTRIAL_EMISSIONS' })}
            />
            Industrial Emissions (Sentinel-5P NO2)
          </label>

          <label
            className="layer-toggle-option"
            title="Illustrative VIIRS-style thermal anomalies - no satellite ingest exists yet; these are hand-authored mock detections (see lib/fireAnomalies)"
          >
            <input
              type="checkbox"
              checked={state.showFireHotspots}
              onChange={() => dispatch({ type: 'TOGGLE_FIRE_HOTSPOTS' })}
            />
            Satellite Fire / Thermal Hotspots (illustrative)
          </label>

          <label
            className="layer-toggle-option"
            title="Citizen fire/burning reports from GET /api/v1/reports - real submissions, snapped to the H3 cell they were filed in"
          >
            <input
              type="checkbox"
              checked={state.showCitizenSensors}
              onChange={() => dispatch({ type: 'TOGGLE_CITIZEN_SENSORS' })}
            />
            Citizen Fire Reports
          </label>

          <label
            className="layer-toggle-option"
            title="Illustrative hand-authored corridor geometry (Western DFC / DMIC) - no routes endpoint exists yet (see lib/freightCorridors)"
          >
            <input
              type="checkbox"
              checked={state.showFreightCorridors}
              onChange={() => dispatch({ type: 'TOGGLE_FREIGHT_CORRIDORS' })}
            />
            Major Freight Corridors (illustrative)
          </label>
        </div>
      </div>
    </SidePanel>
  )
}
