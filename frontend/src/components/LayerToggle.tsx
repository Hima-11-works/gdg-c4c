import { PDI_LABEL, PDI_TOOLTIP } from '../lib/format'
import { useMapUi } from '../state/MapUiContext'
import type { MapViewMode } from '../state/mapUiReducer'

const VIEW_MODES: { mode: MapViewMode; label: string; title: string }[] = [
  { mode: 'hex', label: 'Hex cells', title: 'Discrete H3 hexagons (one value per cell)' },
  { mode: 'smooth', label: 'Smooth', title: 'Smooth continuous field rendered from the same values' },
]

export function LayerToggle() {
  const { state, dispatch } = useMapUi()

  return (
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
    </div>
  )
}
