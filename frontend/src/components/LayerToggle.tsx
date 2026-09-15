import { PDI_LABEL, PDI_TOOLTIP } from '../lib/format'
import { useMapUi } from '../state/MapUiContext'

export function LayerToggle() {
  const { state, dispatch } = useMapUi()

  return (
    <label className="panel layer-toggle" title={PDI_TOOLTIP}>
      <input
        type="checkbox"
        checked={state.showPdi}
        onChange={() => dispatch({ type: 'TOGGLE_PDI' })}
      />
      Show {PDI_LABEL} layer
    </label>
  )
}
