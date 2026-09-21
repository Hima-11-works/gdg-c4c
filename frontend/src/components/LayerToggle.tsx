import { PDI_LABEL, PDI_TOOLTIP } from '../lib/format'
import { useMapUi } from '../state/MapUiContext'
import { SidePanel } from './SidePanel'
import type { MapViewMode } from '../state/mapUiReducer'

const VIEW_MODES: { mode: MapViewMode; label: string; title: string }[] = [
  { mode: 'hex', label: 'Hex cells', title: 'Discrete H3 hexagons (one value per cell)' },
  { mode: 'smooth', label: 'Smooth', title: 'Smooth continuous field rendered from the same values' },
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

        <label
          className={`layer-toggle-option ${state.viewMode === 'smooth' ? 'disabled' : ''}`}
          title="Draw a border on the boundary between PM2.5 ranges (hex view)"
        >
          <input
            type="checkbox"
            checked={state.contrast}
            disabled={state.viewMode === 'smooth'}
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
