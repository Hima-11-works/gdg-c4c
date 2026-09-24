import { useState } from 'react'
import { fetchCellDetail } from '../lib/api'
import { PDI_LABEL, PDI_TOOLTIP, compassLabel, formatNumber, pdiFactorLabel } from '../lib/format'
import { cellCenter } from '../lib/h3Geometry'
import { regionTitle } from '../lib/regionName'
import {
  CITIZEN_VERIFICATION_BADGE,
  CITIZEN_VERIFICATION_TOOLTIP,
  FIRE_KIND_LABELS,
  minutesAgo,
  reportForCell,
  smokeLabel,
} from '../lib/citizenReports'
import {
  anomaliesInCell,
  priorityForSeverity,
  worstAnomalyInCell,
} from '../lib/fireAnomalies'
import type { FireSeverity, ThermalAnomaly } from '../lib/fireAnomalies'
import { useApiResource } from '../hooks/useApiResource'
import type { AsyncResource } from '../hooks/useApiResource'
import { useStateBoundaries } from '../hooks/useStateBoundaries'
import { useMapUi } from '../state/MapUiContext'
import { IncidentPanel } from './IncidentPanel'
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
      {/* The one thing a reader must not get wrong about this row: it is a
          resident's concern, not a measurement. The backend reports no
          verification status for these, so the honest label is the constant. */}
      <p className="citizen-report-unverified" title={CITIZEN_VERIFICATION_TOOLTIP}>
        {CITIZEN_VERIFICATION_BADGE}
      </p>
    </section>
  )
}

/** Triage priority of the clicked cell, from the thermal anomalies that
 *  fall inside it (lib/fireAnomalies). Cell-based matching on purpose: a
 *  detection is a point and the drawer's unit is a cell, so "in this cell"
 *  is the only claim that is actually true — no nearest-neighbour
 *  guessing. The detections behind it are hand-authored mocks, hence the
 *  "(illustrative)" note; severity counts up, priority counts down, so
 *  severity 3 is Priority 1. */
function PriorityBadge({ h3Cell }: { h3Cell: string }) {
  const anomalies = anomaliesInCell(h3Cell)
  if (anomalies.length === 0) return null

  const worst = anomalies[0]
  const priority = priorityForSeverity(worst.severity)
  const label =
    worst.severity === 3 ? 'Critical risk' : worst.severity === 2 ? 'Elevated' : 'Minor / localized'
  const count =
    anomalies.length === 1 ? '1 thermal anomaly' : `${anomalies.length} thermal anomalies`
  const frp = anomalies.length === 1 ? 'FRP' : 'worst FRP'

  return (
    <div className={`priority-badge priority-${worst.severity}`} role="status">
      <span className="priority-dot" aria-hidden="true" />
      <span>
        <strong>
          Priority {priority}: {label}
        </strong>{' '}
        <span className="muted">
          — {count} in this cell ({frp} {worst.frp.toFixed(1)} MW, illustrative)
        </span>
      </span>
    </div>
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
  const environmental = detail.environmental
  const provenance = environmental?.metadata ?? current?.metadata ?? null
  const staticFeatures = environmental?.static_features ?? null
  const exposure = environmental?.exposure ?? current?.exposure ?? null

  if (
    current === null &&
    detail.forecasts.length === 0 &&
    detail.weather === null &&
    environmental?.static_features == null &&
    environmental?.exposure == null
  ) {
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

      <h3>Environmental context</h3>
      <dl className="cell-detail-grid">
        <dt>Population</dt>
        <dd>{formatNumber(staticFeatures?.population_count ?? null, 0)}</dd>

        <dt>Population density</dt>
        <dd>
          {formatNumber(staticFeatures?.population_density_per_km2 ?? null, 0)} people/km²
        </dd>

        <dt>Road length</dt>
        <dd>
          {staticFeatures === null
            ? '—'
            : `${formatNumber(Object.values(staticFeatures.road_length_km_by_class).reduce((a, b) => a + b, 0))} km`}
        </dd>

        <dt>Built-up land</dt>
        <dd>
          {staticFeatures?.built_up_fraction == null
            ? '—'
            : `${Math.round(staticFeatures.built_up_fraction * 100)}%`}
        </dd>

        <dt>Vegetation cover</dt>
        <dd>
          {staticFeatures?.vegetation_fraction == null
            ? '—'
            : `${Math.round(staticFeatures.vegetation_fraction * 100)}%`}
        </dd>

        <dt>Population-weighted PM2.5</dt>
        <dd>
          {formatNumber(exposure?.population_weighted_pm25 ?? null)} µg/m³
        </dd>

        <dt>Residents over threshold</dt>
        <dd>
          {exposure?.residents_above_threshold == null
            ? 'Unknown'
            : `${formatNumber(exposure.residents_above_threshold, 0)} above ${formatNumber(exposure.threshold_pm25)} µg/m³`}
        </dd>

        <dt>Population covered</dt>
        <dd>
          {exposure === null
            ? 'Unknown'
            : `${formatNumber(exposure.covered_population, 0)} residents`}
        </dd>
      </dl>

      {provenance !== null && (
        <p className="muted cell-provenance">
          {provenance.prediction_method}
          {provenance.model_version ? ` · model ${provenance.model_version}` : ''}
          {` · ${provenance.input_kind} inputs · ${Math.round(provenance.quality.coverage_fraction * 100)}% feature coverage`}
          {environmental?.run_id ? ` · run ${environmental.run_id}` : ''}
        </p>
      )}

      <h3>Forecast</h3>
      {detail.forecasts.length === 0 ? (
        <p>No forecast available for this cell yet.</p>
      ) : (
        <ul className="cell-forecast-list">
          {detail.forecasts.map((forecast) => (
            <li key={forecast.forecast_hours}>
              +{forecast.forecast_hours}h: {formatNumber(forecast.predicted_pm25)} µg/m³
              {forecast.lower_pm25 != null && forecast.upper_pm25 != null && (
                <span className="muted">
                  {' '}(80% interval {formatNumber(forecast.lower_pm25)}–{formatNumber(forecast.upper_pm25)})
                </span>
              )}
              <span className="muted"> ({forecast.metadata?.prediction_method ?? `${Math.round(forecast.confidence * 100)}% feature coverage`})</span>
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
 * Bottom action bar of the drawer: the FIRE RESPONSE for this cell.
 *
 * Named that way on purpose. The app also carries a pollution-control
 * response (the PM2.5 alerts), and the two are not interchangeable — this one
 * is about a source (a thermal detection, a filed report), that one is about
 * an area. The bar used to address a fire escalation to a pollution body,
 * which is exactly the confusion the labels now prevent.
 *
 * Every action here is something that genuinely happens on this device and
 * nothing more:
 *
 *  - Critical severity (3) copies a fire-response note to the clipboard.
 *  - Elevated severity (2) and no-detection copy the same note.
 *  - Minor severity (1) adds the cell to a watch list kept in localStorage on
 *    this device only.
 *
 * Nothing is addressed to an authority and nothing is sent: there is no
 * authority-routing backend, the button labels say what they do ("copy",
 * "add to watch list"), and the status line repeats that nothing left the
 * device. The note text names no recipient, because there isn't one.
 */
// Kept under its original key so watch lists already saved in a browser are
// not silently lost; the user-facing name is "watch list".
const WARD_LOG_KEY = 'air-health:ward-log'

interface WatchListEntry {
  h3Cell: string
  loggedAt: string
  pm25: number | null
  pdi: number | null
}

function readWatchList(): WatchListEntry[] {
  try {
    const raw = window.localStorage.getItem(WARD_LOG_KEY)
    if (raw === null) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as WatchListEntry[]) : []
  } catch {
    return []
  }
}

function InterventionActionBar({
  h3Cell,
  detail,
  anomaly,
}: {
  h3Cell: string
  detail: CellDetailOut
  anomaly: ThermalAnomaly | null
}) {
  const [status, setStatus] = useState<'idle' | 'copied' | 'logged' | 'failed'>('idle')
  const [watchCount, setWatchCount] = useState(() => readWatchList().length)

  const severity: FireSeverity | null = anomaly?.severity ?? null
  // Two outcomes, named for what they do rather than for an authority: a
  // critical detection gets the same note as any other (it just carries the
  // reason), and a minor one goes on the watch list.
  const variant = severity === 1 ? 'watch' : severity === 3 ? 'urgent' : 'note'

  const current = detail.current
  const observed = [
    `H3 cell: ${h3Cell}`,
    `Observed: ${current?.timestamp ?? 'no current reading'}`,
    `PM2.5: ${current?.pm25 === null || current?.pm25 === undefined ? 'n/a' : `${formatNumber(current.pm25)} µg/m³`}`,
    `PDI: ${current?.pdi === null || current?.pdi === undefined ? 'n/a' : formatNumber(current.pdi)}`,
    `PDI factors: ${
      detail.pdi_factors === null
        ? 'not available'
        : Object.entries(detail.pdi_factors)
            .map(([key, value]) => `${key} ${Math.round(value * 100)}%`)
            .join(', ')
    }`,
  ]

  const detection =
    anomaly === null
      ? []
      : [
          `Thermal anomaly: FRP ${anomaly.frp.toFixed(1)} MW, detected ${anomaly.detectionMinutesAgo} mins ago, confidence ${Math.round(anomaly.confidence * 100)}% (illustrative mock detection, not a satellite feed)`,
        ]

  const note =
    variant === 'urgent'
      ? [
          'Fire response note (Air Health dashboard) - for your own record',
          `Priority 1 of 3: critical thermal detection in this cell`,
          ...observed,
          ...detection,
          'Reason this is marked urgent: a critical-severity detection is inside a cell already under watch.',
        ].join('\n')
      : ['Fire response note (Air Health dashboard) - for your own record', ...observed].join('\n')

  const runAction = async () => {
    if (variant === 'watch') {
      try {
        const entry: WatchListEntry = {
          h3Cell,
          loggedAt: new Date().toISOString(),
          pm25: current?.pm25 ?? null,
          pdi: current?.pdi ?? null,
        }
        // The list is a set of cells, not an append-only log: re-adding a
        // cell refreshes its entry instead of piling up duplicates the
        // operator would have to de-duplicate by hand.
        const next = [...readWatchList().filter((existing) => existing.h3Cell !== h3Cell), entry]
        window.localStorage.setItem(WARD_LOG_KEY, JSON.stringify(next))
        setWatchCount(next.length)
        setStatus('logged')
      } catch {
        setStatus('failed')
      }
      return
    }

    try {
      await navigator.clipboard.writeText(note)
      setStatus('copied')
    } catch {
      setStatus('failed')
    }
  }

  const ctaLabel =
    status === 'copied'
      ? '✓ Note copied'
      : status === 'logged'
        ? '✓ Added to watch list'
        : variant === 'urgent'
          ? 'Copy fire-response note'
          : variant === 'watch'
            ? 'Add to watch list (this device)'
            : 'Copy fire-response note'

  const statusText =
    status === 'copied'
      ? 'On your clipboard — nothing was sent anywhere.'
      : status === 'logged'
        ? `Saved on this device only (${watchCount} ${watchCount === 1 ? 'cell' : 'cells'}). Nothing left this device.`
        : status === 'failed'
          ? 'Could not access local storage or the clipboard.'
          : variant === 'watch'
            ? 'Nothing is sent automatically; this only adds the cell to a list in this browser.'
            : 'Nothing is sent automatically; this only puts a note on your clipboard.'

  const title =
    variant === 'watch'
      ? 'Add this cell to a watch list kept in this browser only'
      : 'Copy a plain-text fire-response note for this cell to your own clipboard'

  return (
    <div className="intervention-bar" role="group" aria-label="Fire response note">
      <button
        type="button"
        className={`intervention-cta intervention-cta-${variant} ${
          status === 'copied' || status === 'logged' ? 'intervention-cta-done' : ''
        }`}
        onClick={runAction}
        title={title}
      >
        {ctaLabel}
      </button>
      <p className="intervention-status">{statusText}</p>
    </div>
  )
}

export function CellDetailPanel({
  publishedRunId,
  citizenReports,
}: {
  publishedRunId?: string
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
    () => fetchCellDetail(selectedCell ?? '', resolution, publishedRunId),
    [selectedCell, resolution, publishedRunId],
    { enabled: selectedCell !== null && publishedRunId !== undefined },
  )

  if (selectedCell === null) return null

  const close = () => dispatch({ type: 'SELECT_CELL', cell: null })

  // Worst thermal anomaly inside the clicked cell (if any) - drives both the
  // triage badge and the severity of the action bar.
  const anomaly = worstAnomalyInCell(selectedCell)

  // The citizen report the drawer already looks up, hoisted so the incident
  // panel can open an incident from it rather than looking it up twice.
  const report =
    state.showCitizenSensors && citizenReports.status === 'success'
      ? reportForCell(citizenReports.data, selectedCell)
      : null

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

      <PriorityBadge h3Cell={selectedCell} />

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
          <CellDetailContent detail={resource.data} isDemo={resource.isDemo} report={report} />

          {/* A fire incident is opened from a citizen report — that is the only
              fire source the incident service accepts, alongside published and
              persisted alerts. With no report in this cell there is nothing to
              open one from, and the panel says so instead of offering a button
              that would have no source. */}
          {report !== null ? (
            <IncidentPanel
              kind="fire"
              source={{
                type: 'report',
                id: report.id,
                cell: selectedCell,
                title: `${FIRE_KIND_LABELS[report.kind] ?? report.kind}, smoke ${report.smoke_intensity}/5`,
              }}
              title={`Citizen report #${report.id}`}
              evidenceReportIds={[report.id]}
            />
          ) : (
            <p className="muted incident-empty">
              No citizen report is filed in this cell, so there is no source to open a fire incident
              from. File one with “Report a fire”; the incident can then be opened from it.
            </p>
          )}

          <InterventionActionBar h3Cell={selectedCell} detail={resource.data} anomaly={anomaly} />
        </>
      )}
    </aside>
  )
}
