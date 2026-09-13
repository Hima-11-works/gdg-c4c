import { fetchCellDetail } from '../lib/api'
import { compassLabel, formatNumber } from '../lib/format'
import { useApiResource } from '../hooks/useApiResource'
import { useMapUi } from '../state/MapUiContext'
import type { CellDetailOut } from '../lib/types'

function CellDetailContent({ detail }: { detail: CellDetailOut }) {
  const current = detail.current
  const windSpeed = detail.weather?.wind_speed ?? current?.wind_speed ?? null
  const windDirection = detail.weather?.wind_direction ?? current?.wind_direction ?? null

  if (current === null && detail.forecasts.length === 0 && detail.weather === null) {
    return <p>No data for this cell yet.</p>
  }

  return (
    <>
      <dl className="cell-detail-grid">
        <dt>PM2.5</dt>
        <dd>{formatNumber(current?.pm25)} µg/m³</dd>

        <dt>PDI</dt>
        <dd>{formatNumber(current?.pdi)} (heuristic, not a measurement)</dd>

        <dt>Wind speed</dt>
        <dd>{formatNumber(windSpeed)} m/s</dd>

        <dt>Wind direction</dt>
        <dd>
          {windDirection === null
            ? '—'
            : `${formatNumber(windDirection, 0)}° (${compassLabel(windDirection)}, blowing from)`}
        </dd>

        <dt>Confidence</dt>
        <dd>{current === null ? '—' : `${Math.round(current.confidence * 100)}%`}</dd>
      </dl>

      <h3>Forecast</h3>
      {detail.forecasts.length === 0 ? (
        <p>No forecast available for this cell yet.</p>
      ) : (
        <ul className="cell-forecast-list">
          {detail.forecasts.map((forecast) => (
            <li key={forecast.forecast_hours}>
              +{forecast.forecast_hours}h: {formatNumber(forecast.predicted_pm25)} µg/m³
              <span className="muted"> ({Math.round(forecast.confidence * 100)}% confidence)</span>
            </li>
          ))}
        </ul>
      )}

      <h3>PDI contributing factors</h3>
      <p className="muted">
        Not available from the API yet — PDI is currently a single value with no per-factor
        breakdown exposed by the backend.
      </p>
    </>
  )
}

export function CellDetailPanel() {
  const { state, dispatch } = useMapUi()
  const selectedCell = state.selectedCell

  const { resource, refetch } = useApiResource(
    () => fetchCellDetail(selectedCell ?? ''),
    [selectedCell],
    { enabled: selectedCell !== null },
  )

  if (selectedCell === null) return null

  return (
    <aside className="panel cell-detail" aria-label="Cell details">
      <div className="cell-detail-header">
        <h2>{selectedCell}</h2>
        <button
          type="button"
          onClick={() => dispatch({ type: 'SELECT_CELL', cell: null })}
          aria-label="Close"
        >
          ✕
        </button>
      </div>

      {resource.status === 'loading' && <p>Loading…</p>}

      {resource.status === 'error' && (
        <p>
          Couldn't load this cell: {resource.message}{' '}
          <button type="button" onClick={refetch}>
            Retry
          </button>
        </p>
      )}

      {resource.status === 'success' && <CellDetailContent detail={resource.data} />}
    </aside>
  )
}
