import { PDI_LABEL, PDI_TOOLTIP } from '../lib/format'
import { useMapUi } from '../state/MapUiContext'
import { SidePanel } from './SidePanel'
import type { MapViewMode } from '../state/mapUiReducer'

const VIEW_MODES: { mode: MapViewMode; label: string; title: string }[] = [
  { mode: 'hex', label: 'Hex cells', title: 'Discrete H3 hexagons (one value per cell)' },
  {
    mode: 'smooth',
    label: 'Smooth',
    title: 'Smooth continuous field rendered from the same values',
  },
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

        <section className="appearance-control" aria-label="Appearance">
          <span className="appearance-label">Appearance</span>
          <div className="appearance-options" role="group" aria-label="Map theme">
            <button
              type="button"
              className={`appearance-option ${state.theme === 'light' ? 'active' : ''}`}
              aria-pressed={state.theme === 'light'}
              onClick={() => dispatch({ type: 'SET_THEME', theme: 'light' })}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" />
              </svg>
              Light
            </button>
            <button
              type="button"
              className={`appearance-option ${state.theme === 'dark' ? 'active' : ''}`}
              aria-pressed={state.theme === 'dark'}
              onClick={() => dispatch({ type: 'SET_THEME', theme: 'dark' })}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M20.2 15.4A8.5 8.5 0 0 1 8.6 3.8 8.7 8.7 0 1 0 20.2 15.4Z" />
              </svg>
              Dark
            </button>
          </div>
        </section>

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

          <div className="layer-toggle-group-item">
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
            <span className="layer-sub-hint">
              Real NRT VIIRS detections. If empty, no active-fire detections were returned.
            </span>
          </div>

          <div className="layer-toggle-group-item">
            <div className="layer-toggle-row-between">
              <label
                className="layer-toggle-option"
                title="Show FIRMS thermal detections and local PM2.5 outlier candidates. Local outliers appear from Resolution 3 and are not confirmed sources."
              >
                <input
                  type="checkbox"
                  checked={state.showHotspotCandidates}
                  onChange={() => dispatch({ type: 'TOGGLE_HOTSPOT_CANDIDATES' })}
                />
                Pollution hotspot candidates
              </label>
              <button
                type="button"
                className="btn-layer-aux"
                onClick={() => {
                  if (!state.showHotspotCandidates) dispatch({ type: 'TOGGLE_HOTSPOT_CANDIDATES' })
                  if (!state.hotspotPanelOpen) dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })
                }}
                title="Open fire candidate evidence & triage dialog"
              >
                Evidence
              </button>
            </div>
            <span className="layer-sub-hint">
              Amber spatial anomaly rings require Resolution 3+ (zoom in).
            </span>
          </div>

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

          <div className="layer-toggle-group-item">
            <label
              className="layer-toggle-option"
              title="Shows backend-verified citizen fire reports and map-derived PM2.5 anomaly predictions. Predictions are candidates, not confirmed fires."
            >
              <input
                type="checkbox"
                checked={state.showFireHotspots}
                onChange={() => dispatch({ type: 'TOGGLE_FIRE_HOTSPOTS' })}
              />
              Verified & Predicted Fire Hotspots
            </label>
            <span className="layer-sub-hint">
              Verified reports are green; amber markers are map-derived candidates, not confirmed
              fires. Predictions require fine-resolution map data.
            </span>
          </div>

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
            title="Major Economic Freight Corridors — predictive interstate air-quality forecast & logistics routing (illustrative sample corridors)"
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
