import { useState } from 'react'
import { fetchCellDetail } from '../lib/api'
import { PDI_LABEL, PDI_TOOLTIP, compassLabel, formatNumber, pdiFactorLabel } from '../lib/format'
import { cellCenter } from '../lib/h3Geometry'
import { regionTitle } from '../lib/regionName'
import { FIRE_KIND_LABELS, minutesAgo, reportForCell, smokeLabel } from '../lib/citizenReports'
import { useApiResource } from '../hooks/useApiResource'
import type { AsyncResource } from '../hooks/useApiResource'
import { useStateBoundaries } from '../hooks/useStateBoundaries'
import { useMapUi } from '../state/MapUiContext'
import type { CellDetailOut, FireReportOut } from '../lib/types'

/** The most recent citizen report filed in this cell, from
 *  GET /api/v1/reports. Deliberately NOT framed as evidence behind any
 *  classification: the only modelled explanation of a cell in this drawer is
 *  the backend's PDI factor breakdown. */
function CitizenReportWidget({ report }: { report: FireReportOut }) {
  const age = minutesAgo(report.reported_at)
  return (
    <section className="citizen-report">
      <h3>Citizen report</h3>
      <div className="citizen-report-meta">
        <strong>{FIRE_KIND_LABELS[report.kind] ?? report.kind}</strong>
        <span className="muted">
          Smoke: {smokeLabel(report.smoke_intensity)} ({report.smoke_intensity}/5) ·{' '}
          {report.duration_hours === 0
            ? 'just started'
            : `~${formatNumber(report.duration_hours)}h`}{' '}
          · {age} mins ago
        </span>
        {report.notes !== null && report.notes !== '' && (
          <span className="muted">{report.notes}</span>
        )}
        <span className="muted">
          Cell {report.h3_cell} — the model treats this as an active source.
        </span>
      </div>
    </section>
  )
}

function CellDetailContent({
  detail,
  isDemo,
  report,
}: {
  detail: CellDetailOut
  isDemo: boolean
  report: FireReportOut | null
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

      {report !== null && <CitizenReportWidget report={report} />}
    </>
  )
}

/**
 * Bottom action bar of the inspection drawer.
 *
 * There is no authority-routing backend, so this does the one real, honest
 * thing available locally: it copies a plain-text inspection note for this
 * cell to the clipboard, for the operator to send through whatever channel
 * they actually have (email, a CPCB/SPCB portal). It does NOT dispatch,
 * notify, or record anything anywhere.
 */
function InterventionActionBar({
  h3Cell,
  detail,
}: {
  h3Cell: string
  detail: CellDetailOut
}) {
  const [status, setStatus] = useState<'idle' | 'copied' | 'failed'>('idle')

  const copyNote = async () => {
    const current = detail.current
    const factors =
      detail.pdi_factors === null
        ? 'not available'
        : Object.entries(detail.pdi_factors)
            .map(([key, value]) => `${key} ${Math.round(value * 100)}%`)
            .join(', ')
    const note = [
      'Air-quality inspection note (Air Health dashboard)',
      `H3 cell: ${h3Cell}`,
      `Observed: ${current?.timestamp ?? 'no current reading'}`,
      `PM2.5: ${current?.pm25 === null || current?.pm25 === undefined ? 'n/a' : `${formatNumber(current.pm25)} µg/m³`}`,
      `PDI: ${current?.pdi === null || current?.pdi === undefined ? 'n/a' : formatNumber(current.pdi)}`,
      `PDI factors: ${factors}`,
    ].join('\n')

    try {
      await navigator.clipboard.writeText(note)
      setStatus('copied')
    } catch {
      setStatus('failed')
    }
  }

  return (
    <div className="intervention-bar" role="group" aria-label="Inspection note">
      <button
        type="button"
        className={`intervention-cta ${status === 'copied' ? 'intervention-cta-done' : ''}`}
        onClick={copyNote}
        title="Copy a plain-text inspection note for this cell to the clipboard"
      >
        {status === 'copied' ? '✓ Note copied' : 'Copy inspection note'}
      </button>
      <p className="intervention-status">
        {status === 'copied'
          ? 'Copied — send it to the relevant authority yourself.'
          : status === 'failed'
            ? 'Could not access the clipboard.'
            : 'Nothing is sent automatically; this only prepares a note.'}
      </p>
    </div>
  )
}

export function CellDetailPanel({
  citizenReports,
}: {
  citizenReports: AsyncResource<FireReportOut[]>
}) {
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
            report={
              state.showCitizenSensors
                ? reportForCell(
                    citizenReports.status === 'success' ? citizenReports.data : [],
                    selectedCell,
                  )
                : null
            }
          />
          <InterventionActionBar h3Cell={selectedCell} detail={resource.data} />
        </>
      )}
    </aside>
  )
}
