import { useState } from 'react'
import { latLngToCell } from 'h3-js'
import { fetchCellDetail } from '../lib/api'
import { PDI_LABEL, PDI_TOOLTIP, compassLabel, formatNumber, pdiFactorLabel } from '../lib/format'
import { cellCenter, resolutionOfCell } from '../lib/h3Geometry'
import { regionTitle } from '../lib/regionName'
import {
  FIRE_KIND_LABELS,
  REPORT_STATUS_LABELS,
  minutesAgo,
  reportForCell,
  smokeLabel,
} from '../lib/citizenReports'
import { anomaliesInCell, priorityForSeverity, worstAnomalyInCell } from '../lib/fireAnomalies'
import type { FireSeverity, ThermalAnomaly } from '../lib/fireAnomalies'
import { useApiResource, type AsyncResource } from '../hooks/useApiResource'
import { useStateBoundaries } from '../hooks/useStateBoundaries'
import { useMapUi } from '../state/MapUiContext'
import type { CellDetailOut, FireReportWithStatus } from '../lib/types'
import type { ActiveFire } from '../lib/activeFires'
import { estimatePlumeDrift } from '../lib/transboundaryDrift'
import { CellSatelliteContextPanel } from './CellSatelliteContextPanel'

/** The most recent citizen report filed in this cell, from
 *  GET /api/v1/reports. Deliberately NOT framed as evidence behind any
 *  classification: the only modelled explanation of a cell in this drawer is
 *  the backend's PDI factor breakdown. */
/** Takes the v2 row shape so the status fields are available; it is a superset
 *  of the v1 one, so anything that only reads the submission fields still fits. */
function CitizenReportWidget({ report }: { report: FireReportWithStatus }) {
  const age = minutesAgo(report.reported_at)
  return (
    <section className="citizen-report">
      <h3>Citizen report</h3>
      <div className="citizen-report-meta">
        <strong>{FIRE_KIND_LABELS[report.kind] ?? report.kind}</strong>
        <span className="muted">
          {/* The status is shown here, not only in the submit form: someone
              reading another person's report needs to know whether it counts.
              `affects_air_quality_model` is the backend's own answer, so this
              label cannot drift from the rule the model actually applies. */}
          {REPORT_STATUS_LABELS[report.status] ?? report.status}
          {report.affects_air_quality_model ? '' : ' — not counted in the model yet'}
        </span>
        <span className="muted">
          Smoke: {smokeLabel(report.smoke_intensity)} ({report.smoke_intensity}/5) ·{' '}
          {report.duration_hours === 0
            ? 'just started'
            : `~${formatNumber(report.duration_hours)}h`}{' '}
          · {age} mins ago
          {report.corroborating_report_count > 0 &&
            ` · ${report.corroborating_report_count} corroborating report(s)`}
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

function SatelliteFireEvidence({
  h3Cell,
  resource,
}: {
  h3Cell: string
  resource: AsyncResource<ActiveFire[]>
}) {
  let matches: ActiveFire[] = []
  if (resource.status === 'success') {
    const resolution = resolutionOfCell(h3Cell)
    if (resolution !== undefined) {
      matches = resource.data.filter((fire) => {
        try {
          return latLngToCell(fire.latitude, fire.longitude, resolution) === h3Cell
        } catch {
          return false
        }
      })
    }
  }

  return (
    <section className="cell-firms-evidence">
      <h3>Satellite thermal evidence</h3>
      <p className="muted">
        NASA FIRMS detections from the latest 24-hour feed. A thermal detection is not a confirmed
        ground fire or a PM2.5 measurement.
      </p>
      {resource.status === 'idle' || resource.status === 'loading' ? (
        <p className="muted">Loading satellite evidence…</p>
      ) : null}
      {resource.status === 'error' ? (
        <p role="status" className="muted">
          The FIRMS feed could not be read; this does not mean there were no detections.
        </p>
      ) : null}
      {resource.status === 'success' && matches.length === 0 ? (
        <p className="muted">
          No detection in this cell was returned for the feed window. This is not proof that no
          source is present.
        </p>
      ) : null}
      {resource.status === 'success' && matches.length > 0 ? (
        <ul className="cell-firms-list">
          {matches.slice(0, 5).map((fire) => (
            <li key={fire.id}>
              <strong>{fire.confidenceClass} confidence</strong>
              <span>
                {fire.satellite} · {fire.acquiredAt || 'acquisition time unavailable'}
              </span>
              <span>
                FRP {fire.frp === null ? 'not reported' : `${formatNumber(fire.frp)} MW`}
                {' · '}
                Brightness{' '}
                {fire.brightness === null ? 'not reported' : `${formatNumber(fire.brightness)} K`}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
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

function ForecastChart({ forecasts }: { forecasts: CellDetailOut['forecasts'] }) {
  const points = [...forecasts].sort((a, b) => a.forecast_hours - b.forecast_hours)
  const left = 58
  const right = 526
  const top = 18
  const bottom = 202
  const maxHour = Math.max(1, ...points.map((point) => point.forecast_hours))
  const maxValue = Math.max(
    50,
    ...points.map((point) => Math.max(point.predicted_pm25, point.upper_pm25 ?? 0)),
  )
  const yMax = Math.ceil(maxValue / 50) * 50
  const x = (hour: number) => left + (hour / maxHour) * (right - left)
  const y = (value: number) => bottom - (value / yMax) * (bottom - top)
  const line = points
    .map(
      (point, index) =>
        `${index === 0 ? 'M' : 'L'} ${x(point.forecast_hours)} ${y(point.predicted_pm25)}`,
    )
    .join(' ')
  const yTicks = [0, yMax / 4, yMax / 2, (yMax * 3) / 4, yMax]

  return (
    <div className="cell-forecast-chart-wrap">
      <svg
        className="cell-forecast-chart"
        viewBox="0 0 560 250"
        role="img"
        aria-label={`PM2.5 forecast: ${points.map((point) => `plus ${point.forecast_hours} hours, ${formatNumber(point.predicted_pm25)} micrograms per cubic meter`).join('; ')}`}
      >
        <text
          className="forecast-axis-title forecast-y-title"
          x="14"
          y="112"
          transform="rotate(-90 14 112)"
        >
          PM2.5 (µg/m³)
        </text>
        {yTicks.map((tick) => (
          <g key={tick}>
            <line className="forecast-gridline" x1={left} x2={right} y1={y(tick)} y2={y(tick)} />
            <text className="forecast-tick" x={left - 9} y={y(tick) + 4} textAnchor="end">
              {formatNumber(tick, 0)}
            </text>
          </g>
        ))}
        <line className="forecast-axis" x1={left} x2={right} y1={bottom} y2={bottom} />
        <path className="forecast-line" d={line} />
        {points.map((point) => (
          <g key={point.forecast_hours}>
            {point.lower_pm25 != null && point.upper_pm25 != null && (
              <line
                className="forecast-interval"
                x1={x(point.forecast_hours)}
                x2={x(point.forecast_hours)}
                y1={y(point.lower_pm25)}
                y2={y(point.upper_pm25)}
              />
            )}
            <circle
              className="forecast-point"
              cx={x(point.forecast_hours)}
              cy={y(point.predicted_pm25)}
              r="4"
            >
              <title>{`+${point.forecast_hours}hr · ${formatNumber(point.predicted_pm25)} µg/m³`}</title>
            </circle>
            <text
              className="forecast-tick"
              x={x(point.forecast_hours)}
              y={bottom + 19}
              textAnchor="middle"
            >
              +{point.forecast_hours}hr
            </text>
          </g>
        ))}
        <text className="forecast-axis-title" x={(left + right) / 2} y="242" textAnchor="middle">
          Time from now
        </text>
      </svg>
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
  report: FireReportWithStatus | null
}) {
  const current = detail.current
  const windSpeed = detail.weather?.wind_speed ?? current?.wind_speed ?? null
  const windDirection = detail.weather?.wind_direction ?? current?.wind_direction ?? null
  const environmental = detail.environmental
  const provenance = environmental?.metadata ?? current?.metadata ?? null
  const staticFeatures = environmental?.static_features ?? null
  const exposure = environmental?.exposure ?? current?.exposure ?? null
  const drift = estimatePlumeDrift(windSpeed, windDirection)

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
        <dd>{formatNumber(staticFeatures?.population_density_per_km2 ?? null, 0)} people/km²</dd>

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
        <dd>{formatNumber(exposure?.population_weighted_pm25 ?? null)} µg/m³</dd>

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

      {drift !== null && (
        <section className="transboundary-drift-card">
          <div className="transboundary-drift-header">
            <span className="transboundary-drift-icon">💨</span>
            <div>
              <strong className="transboundary-drift-title">Wind-only transport estimate</strong>
              <div className="transboundary-drift-vector">
                Wind: {drift.blowsFrom} → Carrying {drift.blowsToward} ({drift.speedKmh} km/h)
              </div>
            </div>
          </div>
          <p className="transboundary-drift-advisory">
            Straight-line wind travel only; assumes the reported wind stays constant. This does not
            model pollutant transport or predict air-quality spikes.
          </p>
          <div className="transboundary-drift-reach">
            <span>Estimated wind travel:</span>
            <strong>~{drift.drift3hKm} km in 3h</strong> ·{' '}
            <strong>~{drift.drift6hKm} km in 6h</strong>
          </div>
        </section>
      )}

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
        <ForecastChart forecasts={detail.forecasts} />
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
 * There is no authority-routing backend, so every action here is something
 * that genuinely happens on this device and nothing more:
 *
 *  - Critical severity (3) copies an escalation note addressed to a State
 *    Rapid Action Unit.
 *  - Elevated severity (2) copies the plain inspection note.
 *  - Minor severity (1) appends the cell to a ward list kept in
 *    localStorage on this device only.
 *  - No detection in the cell keeps the plain inspection note.
 *
 * It does NOT dispatch, notify, or record anything anywhere — the status
 * line says so, and the ward list is explicitly local.
 */
const WARD_LOG_KEY = 'air-health:ward-log'

interface WardLogEntry {
  h3Cell: string
  loggedAt: string
  pm25: number | null
  pdi: number | null
}

function readWardLog(): WardLogEntry[] {
  try {
    const raw = window.localStorage.getItem(WARD_LOG_KEY)
    if (raw === null) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as WardLogEntry[]) : []
  } catch {
    return []
  }
}

function GrapAssessmentNotice() {
  return (
    <section className="grap-card" aria-label="GRAP assessment not available">
      <strong>Official GRAP stage not assessed</strong>
      <p className="grap-card-advisory">
        CAQM GRAP applies to Delhi-NCR and uses AQI stages. This view does not have verified AQI and
        jurisdiction data to determine an official stage or statutory action.
      </p>
      <a
        href="https://caqm.nic.in/Contents.aspx?langid=1&lev=1&lid=4174&lsid=4171&pid=0"
        target="_blank"
        rel="noreferrer"
      >
        View CAQM GRAP orders
      </a>
    </section>
  )
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
  const [wardCount, setWardCount] = useState(() => readWardLog().length)

  const severity: FireSeverity | null = anomaly?.severity ?? null
  const variant = severity === 3 ? 'escalate' : severity === 1 ? 'log' : 'inspect'

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
    variant === 'escalate'
      ? [
          'Escalation note (Air Health dashboard) - for the State Rapid Action Unit',
          `Priority 1 of 3: critical thermal anomaly in this cell`,
          ...observed,
          ...detection,
          'Reason for escalation: a critical-severity detection is inside a cell already under watch.',
        ].join('\n')
      : ['Air-quality inspection note (Air Health dashboard)', ...observed].join('\n')

  const runAction = async () => {
    if (variant === 'log') {
      try {
        const entry: WardLogEntry = {
          h3Cell,
          loggedAt: new Date().toISOString(),
          pm25: current?.pm25 ?? null,
          pdi: current?.pdi ?? null,
        }
        // The list is a set of cells, not an append-only log: re-logging a
        // cell refreshes its entry instead of piling up duplicates the
        // operator would have to de-duplicate by hand.
        const next = [...readWardLog().filter((existing) => existing.h3Cell !== h3Cell), entry]
        window.localStorage.setItem(WARD_LOG_KEY, JSON.stringify(next))
        setWardCount(next.length)
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
        ? '✓ Logged to ward list'
        : variant === 'escalate'
          ? 'Copy escalation note for the State Rapid Action Unit'
          : variant === 'log'
            ? 'Log to ward list (this device)'
            : 'Copy inspection note'

  const statusText =
    status === 'copied'
      ? 'Copied. Nothing was sent; forward this draft through the authority’s official channel.'
      : status === 'logged'
        ? `Saved on this device only (${wardCount} ${wardCount === 1 ? 'entry' : 'entries'}). Nothing left this device.`
        : status === 'failed'
          ? 'Could not access local storage or the clipboard.'
          : variant === 'escalate'
            ? 'Nothing is sent automatically; this only prepares an escalation note.'
            : variant === 'log'
              ? 'Nothing is sent automatically; this only appends to a list on this device.'
              : 'Nothing is sent automatically; this only prepares a note.'

  const title =
    variant === 'escalate'
      ? 'Copy a plain-text escalation note for this cell, addressed to a State Rapid Action Unit'
      : variant === 'log'
        ? 'Append this cell to a ward list kept in this browser only'
        : 'Copy a plain-text inspection note for this cell to the clipboard'

  return (
    <div className="intervention-bar" role="group" aria-label="Inspection note">
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
  activeFires,
}: {
  publishedRunId?: string
  citizenReports: AsyncResource<FireReportWithStatus[]>
  activeFires: AsyncResource<ActiveFire[]>
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
    { enabled: selectedCell !== null },
  )

  if (selectedCell === null) return null

  const close = () => dispatch({ type: 'SELECT_CELL', cell: null })

  // Worst thermal anomaly inside the clicked cell (if any) - drives both the
  // triage badge and the severity of the action bar.
  const anomaly = worstAnomalyInCell(selectedCell)

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

      {resource.status === 'success' && <GrapAssessmentNotice />}

      {state.selectedCellGeneralized && (
        <p className="banner banner-demo" role="status">
          This finer map cell uses its nearest coarser estimate. Details below are for the parent
          cell that supplied the map color.
        </p>
      )}

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
          <CellSatelliteContextPanel h3Cell={selectedCell} resolution={resolution} />
          <SatelliteFireEvidence h3Cell={selectedCell} resource={activeFires} />
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
          <InterventionActionBar h3Cell={selectedCell} detail={resource.data} anomaly={anomaly} />
        </>
      )}
    </aside>
  )
}
