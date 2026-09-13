import { useMapUi } from '../state/MapUiContext'

export function LayerToggle() {
  const { state, dispatch } = useMapUi()

  return (
    <label className="panel layer-toggle">
      <input
        type="checkbox"
        checked={state.showPdi}
        onChange={() => dispatch({ type: 'TOGGLE_PDI' })}
      />
      Show PDI layer
    </label>
  )
}
