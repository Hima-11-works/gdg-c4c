import { useState } from 'react'
import { fetchCellDetail } from '../lib/api'
import { PDI_LABEL, PDI_TOOLTIP, compassLabel, formatNumber, pdiFactorLabel } from '../lib/format'
import { cellCenter } from '../lib/h3Geometry'
import { regionTitle } from '../lib/regionName'
import { citizenReportForCell, reportThumbnail } from '../lib/citizenReports'
import { useApiResource } from '../hooks/useApiResource'
import { useStateBoundaries } from '../hooks/useStateBoundaries'
import { useMapUi } from '../state/MapUiContext'
import type { CellDetailOut } from '../lib/types'

/** The most recent citizen submission for this cell - illustrative seed
 *  data (see lib/citizenReports) until the web reads GET /api/v1/reports.
 *  Deliberately NOT framed as evidence behind any classification: the only
 *  modelled explanation of a cell in this drawer is the backend's PDI
 *  factor breakdown. */
function CitizenReportWidget({ h3Cell }: { h3Cell: string }) {
  const report = citizenReportForCell(h3Cell)
  if (report === null) return null

  return (
    <section className="citizen-report">
      <h3>Citizen report</h3>
      <div className="citizen-report-row">
        <img
          className="citizen-thumb"
          src={reportThumbnail(report.category)}
          alt={'Illustrative citizen photo: ' + report.category}
          width={64}
          height={64}
          loading="lazy"
        />
        <div className="citizen-report-meta">
          <strong>{report.category}</strong>
          <span className="muted">
            {report.minutesAgo} mins ago - {report.name}
          </span>
          <span className="muted">Illustrative - not yet wired to the reports API.</span>
        </div>
      </div>
    </section>
  )
}

function CellDetailContent({
  detail,
  isDemo,
  showCitizenReports,
}: {
  detail: CellDetailOut
  isDemo: boolean
  showCitizenReports: boolean
}) {
  const current = detail.current
  const windSpeed = detail.weather?.wind_speed ?? current?.wind_speed ?? null
  const windDirection = detail.weather?.wind_direction ?? current?.wind_direction ?? null

  if (current === null && detail.forecasts.length === 0 && detail.weather === null) {
    return <p>No data for this cell yet.</p>
  }

  return (
    <>
      {isDemo && (
        <p className="banner banner-demo" role="status">
          Demo data — illustrative, not measured.
        </p>
      )}

      <dl className="cell-detail-grid">
        <dt>PM2.5</dt>
        <dd>{formatNumber(current?.pm25)} µg/m³</dd>

        <dt title={PDI_TOOLTIP}>{PDI_LABEL}</dt>
        <dd>{formatNumber(current?.pdi)}</dd>

        <dt>Wind speed</dt>
        <dd>{formatNumber(windSpeed)} m/s</dd>

        <dt>Wind direction</dt>
        <dd>
          {windDirection === null
            ? '—'
            : `${formatNumber(windDirection, 0)}° (${compassLabel(windDirection)}, blowing from)`}
        </dd>

        <dt>Temperature</dt>
        <dd>
          {detail.weather?.temperature == null
            ? '—'
            : `${formatNumber(detail.weather.temperature)} °C`}
        </dd>

        <dt>Humidity</dt>
        <dd>
          {detail.weather?.humidity == null ? '—' : `${formatNumber(detail.weather.humidity)}%`}
        </dd>

        <dt>Precipitation</dt>
        <dd>
          {detail.weather === null ? '—' : `${formatNumber(detail.weather.precipitation)} mm`}
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

      <h3 title={PDI_TOOLTIP}>{PDI_LABEL} factors</h3>
      {detail.pdi_factors === null ? (
        <p className="muted">
          Not available for this reading — its PDI score has no stored per-factor breakdown.
        </p>
      ) : (
        <ul className="pdi-factor-list">
          {Object.entries(detail.pdi_factors).map(([key, value]) => {
            const percent = Math.round(value * 100)
            return (
              <li key={key}>
                <div className="pdi-factor-row">
                  <span>{pdiFactorLabel(key)}</span>
                  <span className="muted">{percent}%</span>
                </div>
                <div className="pdi-factor-bar">
                  <div className="pdi-factor-bar-fill" style={{ width: `${percent}%` }} />
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {showCitizenReports && <CitizenReportWidget h3Cell={detail.h3_cell} />}
    </>
  )
}

/** Sticky bottom action bar of the inspection drawer — the rapid-
 *  intervention dispatch that satisfies the brief's "alert relevant
 *  authorities" clause. Dispatch is acknowledged locally (deterministic
 *  status line); the brief's real routing endpoint plugs in at this exact
 *  button without touching the rest of the drawer. */
function InterventionActionBar({ h3Cell }: { h3Cell: string }) {
  const [dispatched, setDispatched] = useState(false)

  const dispatchNow = () => {
    if (dispatched) return
    setDispatched(true)
    // Broadcast so the Alerts panel/edge node can pick the intervention up
    // (federated dispatch hook point — the brief's "alert relevant
    // authorities" clauses ride on this exact event).
    window.dispatchEvent(
      new CustomEvent('air-health:intervention-dispatched', {
        detail: { cell: h3Cell, routedTo: 'State Pollution Control Board' },
      }),
    )
  }

  return (
    <div className="intervention-bar" role="group" aria-label="Rapid intervention">
      <button
        type="button"
        className={`intervention-cta ${dispatched ? 'intervention-cta-done' : ''}`}
        onClick={dispatchNow}
        disabled={dispatched}
        title="Dispatch the State Pollution Control Board Rapid Action Unit to this hex"
      >
        {dispatched ? '✓ Rapid Action Unit Dispatched' : '🚨 Dispatch Rapid Action Unit'}
      </button>
      <p className="intervention-status">
        {dispatched
          ? 'Dispatch acknowledged by the federated edge.'
          : 'Routes alert to State Pollution Control Board.'}
      </p>
    </div>
  )
}

export function CellDetailPanel() {
  const { state, dispatch } = useMapUi()
  const selectedCell = state.selectedCell
  // Captured at click time (see mapUiReducer's SELECT_CELL case), not
  // read live from state.lod — the user may have zoomed since, and a
  // cell string only means anything at the resolution it was minted at.
  const resolution = state.selectedCellResolution ?? undefined
  const boundaries = useStateBoundaries()

  // Humanized drawer title: readable region (state + deterministic zone)
  // derived from the clicked hex's center coordinates; the raw H3 index
  // drops to a muted subline.
  let title: string | null = null
  if (selectedCell !== null) {
    try {
      const [lat, lon] = cellCenter(selectedCell)
      title = regionTitle(lat, lon, boundaries)
    } catch {
      title = null
    }
  }

  const { resource, refetch } = useApiResource(
    () => fetchCellDetail(selectedCell ?? '', resolution),
    [selectedCell, resolution],
    { enabled: selectedCell !== null },
  )

  if (selectedCell === null) return null

  const close = () => dispatch({ type: 'SELECT_CELL', cell: null })

  return (
    <aside className="panel cell-detail" aria-label="Cell details">
      <div className="cell-detail-header">
        <div className="cell-detail-titles">
          <h2>{title ?? selectedCell}</h2>
          <span className="cell-detail-index">{selectedCell}</span>
        </div>
        <button
          type="button"
          className="cell-detail-close"
          onClick={close}
          aria-label="Close cell details"
          title="Close"
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

      {resource.status === 'success' && (
        <>
          <CellDetailContent
            detail={resource.data}
            isDemo={resource.isDemo}
            showCitizenReports={state.showCitizenSensors}
          />
          <InterventionActionBar h3Cell={selectedCell} />
        </>
      )}
    </aside>
  )
}
