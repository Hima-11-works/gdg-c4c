import { useMapUi } from '../state/MapUiContext'
import type { TimelineSelection } from '../state/mapUiReducer'

const OPTIONS: { horizon: TimelineSelection; label: string }[] = [
  { horizon: 'now', label: 'Now' },
  { horizon: 1, label: '+1h' },
  { horizon: 3, label: '+3h' },
  { horizon: 6, label: '+6h' },
]

export function TimelineControl() {
  const { state, dispatch } = useMapUi()

  return (
    <div className="panel timeline-control" role="group" aria-label="Forecast horizon">
      {OPTIONS.map((option) => (
        <button
          key={option.horizon}
          type="button"
          className={option.horizon === state.horizon ? 'active' : ''}
          aria-pressed={option.horizon === state.horizon}
          onClick={() => dispatch({ type: 'SELECT_HORIZON', horizon: option.horizon })}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
