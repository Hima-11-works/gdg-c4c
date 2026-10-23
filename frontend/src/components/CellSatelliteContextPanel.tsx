import { useState, useEffect } from 'react'
import { fetchCellSatelliteContext } from '../lib/api'
import { formatNumber } from '../lib/format'
import { useApiResource } from '../hooks/useApiResource'
import type { CellSatelliteAnalysisOut } from '../lib/types'

interface CellSatelliteContextPanelProps {
  h3Cell: string
  resolution?: number
}

function cpcbBadgeClass(category: string | null): string {
  switch (category?.toLowerCase()) {
    case 'good':
      return 'aqi-badge-good'
    case 'satisfactory':
      return 'aqi-badge-satisfactory'
    case 'moderate':
      return 'aqi-badge-moderate'
    case 'poor':
      return 'aqi-badge-poor'
    case 'very poor':
      return 'aqi-badge-very-poor'
    case 'severe':
      return 'aqi-badge-severe'
    default:
      return 'aqi-badge-unavailable'
  }
}

export function CellSatelliteContextPanel({ h3Cell, resolution }: CellSatelliteContextPanelProps) {
  const [loadProgress, setLoadProgress] = useState(4)
  const [responseStarted, setResponseStarted] = useState(false)
  const { resource, refetch } = useApiResource<CellSatelliteAnalysisOut>(
    (signal, onProgress) => fetchCellSatelliteContext(h3Cell, resolution, signal, onProgress),
    [h3Cell, resolution],
    {
      onProgress: (loaded, total) => {
        if (loaded > 0) setResponseStarted(true)
        if (loaded > 0 && total > 0) {
          const transferProgress = Math.min(99, 90 + Math.round((loaded / total) * 9))
          setLoadProgress((current) => Math.max(current, transferProgress))
        }
      },
    },
  )

  useEffect(() => {
    if (resource.status !== 'loading') {
      setLoadProgress(resource.status === 'success' ? 100 : 0)
      setResponseStarted(false)
      return
    }

    setLoadProgress(4)
    setResponseStarted(false)
    const timer = window.setInterval(() => {
      setLoadProgress((current) =>
        current >= 88
          ? current
          : Math.min(88, current + Math.max(1, Math.ceil((88 - current) * 0.08))),
      )
    }, 160)
    return () => window.clearInterval(timer)
  }, [resource.status, h3Cell, resolution])

  const handlePrint = () => {
    const source = document.querySelector<HTMLElement>('.cell-satellite-section')
    if (!source) return

    const printRoot = document.createElement('div')
    printRoot.id = 'cell-satellite-print-root'
    const report = source.cloneNode(true) as HTMLElement
    report.querySelectorAll('button').forEach((button) => button.remove())
    printRoot.append(report)
    document.body.append(printRoot)

    const previousTitle = document.title
    document.title = `Air quality cell ${h3Cell}`
    const cleanup = () => {
      printRoot.remove()
      document.title = previousTitle
    }
    window.addEventListener('afterprint', cleanup, { once: true })
    window.requestAnimationFrame(() => window.print())
  }

  if (resource.status === 'loading') {
    return (
      <section className="cell-satellite-section" aria-label="Satellite and air quality context">
        <h3>Satellite & Surface Air Quality</h3>
        <div className="cell-satellite-loading" role="status" aria-live="polite">
          <div className="cell-satellite-progress-heading">
            <span>{responseStarted ? 'Receiving cell data' : 'Preparing cell analysis'}</span>
            <strong>{loadProgress}%</strong>
          </div>
          <div
            className="cell-satellite-progress-track"
            role="progressbar"
            aria-label="Satellite and surface data loading progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={loadProgress}
          >
            <span style={{ transform: `scaleX(${loadProgress / 100})` }} />
          </div>
          <p className="muted">
            Progress is estimated during analysis and follows transferred bytes when the response
            size is available.
          </p>
        </div>
      </section>
    )
  }

  if (resource.status === 'error') {
    return (
      <section className="cell-satellite-section" aria-label="Satellite and air quality context">
        <h3>Satellite & Surface Air Quality</h3>
        <p className="muted">
          Satellite context unavailable for this cell: {resource.message}{' '}
          <button type="button" className="btn-inline-retry" onClick={refetch}>
            Retry
          </button>
        </p>
      </section>
    )
  }

  if (resource.status !== 'success') {
    return null
  }

  const analysis = resource.data
  const bundle = analysis.evidence_bundle
  const surface = bundle.surface_pm25
  const cpcb = bundle.cpcb_aqi
  const no2 = bundle.satellite_no2
  const uvai = bundle.satellite_uvai
  const firms = bundle.thermal_anomalies
  const weather = bundle.weather
  const baseline = bundle.baseline_anomaly

  return (
    <section className="cell-satellite-section" aria-label="Satellite and air quality context">
      <div className="cell-satellite-header">
        <h3>Satellite & Surface Air Quality</h3>
        <p className="cell-satellite-print-title">
          H3 cell {h3Cell} · Resolution {bundle.resolution}
        </p>
        <div className="cell-satellite-actions">
          <span className="coverage-badge">
            H3 Res {bundle.resolution} ·{' '}
            {no2.coverage_fraction === null
              ? 'satellite coverage unavailable'
              : `${Math.round(no2.coverage_fraction * 100)}% satellite coverage`}
          </span>
          <button
            type="button"
            className="btn-cell-print"
            onClick={handlePrint}
            title="Print or save this cell report as PDF"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M7 8V3h10v5M7 17H5a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2M7 14h10v7H7z" />
              <path d="M17 11h.01" />
            </svg>
            <span>Print / Save PDF</span>
          </button>
        </div>
      </div>

      {/* Surface PM2.5 vs Satellite Distinction */}
      <div className="surface-air-card">
        <div className="surface-air-title-row">
            <strong>Nearest Ground Monitor</strong>
          <span
            className={`surface-kind-tag ${surface.is_estimate ? 'tag-estimate' : 'tag-observed'}`}
          >
            {surface.is_estimate
              ? 'Model estimate'
              : surface.status === 'stale'
                ? 'Stale station reading'
                : 'Station reading'}
          </span>
        </div>

        <dl className="satellite-metrics-grid">
          <dt>Surface PM2.5</dt>
          <dd>
            {surface.status !== 'unavailable' && surface.value_ugm3 !== null ? (
              <>
                <strong>{formatNumber(surface.value_ugm3)} µg/m³</strong>
                {surface.uncertainty_ugm3 && (
                  <span className="muted"> (±{formatNumber(surface.uncertainty_ugm3)})</span>
                )}
                {surface.station_distance_km !== null && surface.station_distance_km > 0 && (
                  <span className="muted station-distance">
                    {' '}
                    · {formatNumber(surface.station_distance_km)} km from cell center
                  </span>
                )}
              </>
            ) : (
              <span className="muted">Unavailable</span>
            )}
          </dd>

          <dt>CPCB National AQI</dt>
          <dd>
            {cpcb.status === 'available' && cpcb.aqi !== null ? (
              <div className="cpcb-row">
                <span className={`aqi-badge ${cpcbBadgeClass(cpcb.category)}`}>
                  {cpcb.aqi} — {cpcb.category}
                </span>
                {cpcb.prominent_pollutant && (
                  <span className="muted"> (Prominent: {cpcb.prominent_pollutant})</span>
                )}
              </div>
            ) : (
              <div className="cpcb-row">
                <span className="aqi-badge aqi-badge-unavailable">Unavailable</span>
                <span className="muted cpcb-reason">{cpcb.reason}</span>
              </div>
            )}
          </dd>
        </dl>
        <p className="muted disclaimer-text">{surface.disclaimer}</p>
      </div>

      {/* Satellite Indicators */}
      <div className="satellite-indicators-card">
        <div className="satellite-indicators-header">
          <strong>Satellite Column Indicators</strong>
          <span className="indicator-caveat-label">
            satellite indicator — not ground-level concentration
          </span>
        </div>

        <table className="satellite-indicator-table">
          <thead>
            <tr>
              <th scope="col">Indicator</th>
              <th scope="col">Observed Column</th>
              <th scope="col">Quality / QA</th>
              <th scope="col">Timestamp</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <strong>Sentinel-5P NO₂</strong>
                <span className="muted sub-label">Tropospheric column</span>
              </td>
              <td>
                {no2.status === 'available' && no2.value !== null ? (
                  <span>
                    {(no2.value * 1e6).toFixed(1)} µmol/m²{' '}
                    <span className="muted">
                      ({no2.value.toExponential(2)} {no2.unit})
                    </span>
                  </span>
                ) : (
                  <span className="muted">{no2.status}</span>
                )}
              </td>
              <td>{no2.qa_score !== null ? `${Math.round(no2.qa_score * 100)}% QA` : '—'}</td>
              <td className="muted">
                {no2.observed_at
                  ? new Date(no2.observed_at).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  : '—'}
              </td>
            </tr>
            <tr>
              <td>
                <strong>Sentinel-5P UVAI</strong>
                <span className="muted sub-label">Aerosol absorption</span>
              </td>
              <td>
                {uvai.status === 'available' && uvai.value !== null ? (
                  <span>
                    {formatNumber(uvai.value, 2)} <span className="muted">(index)</span>
                  </span>
                ) : (
                  <span className="muted">{uvai.status}</span>
                )}
              </td>
              <td>{uvai.qa_score !== null ? `${Math.round(uvai.qa_score * 100)}% QA` : '—'}</td>
              <td className="muted">
                {uvai.observed_at
                  ? new Date(uvai.observed_at).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  : '—'}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Corroborating Signals (FIRMS, Baseline & Weather) */}
      <div className="satellite-corroborating-row">
        <div className="corroborating-item">
          <strong>Thermal Anomalies (FIRMS)</strong>
          <p className="muted">
            {firms.status === 'unavailable' ? (
              'FIRMS observations unavailable'
            ) : firms.detection_count > 0 ? (
              <>
                {firms.detection_count} detection(s)
                {firms.max_frp_mw !== null && ` · max FRP ${formatNumber(firms.max_frp_mw)} MW`}
                {firms.confidence_class && ` · ${firms.confidence_class} confidence`}
              </>
            ) : (
              'No thermal anomalies detected in cell window'
            )}
          </p>
          <span className="micro-disclaimer">{firms.disclaimer}</span>
        </div>

        <div className="corroborating-item">
          <strong>Baseline Screening</strong>
          <p className="muted">
            {baseline.baseline_eligible ? (
              <span className={`signal-tag signal-${baseline.screening_signal}`}>
                {baseline.note}
              </span>
            ) : (
              baseline.note
            )}
          </p>
        </div>

        <div className="corroborating-item">
          <strong>Surface Weather</strong>
          <p className="muted">
            {weather.status === 'unavailable' ? (
              'Weather observation unavailable'
            ) : weather.temperature_c !== null || weather.wind_speed_ms !== null ? (
              <>
                {weather.status === 'stale' && 'Stale observation · '}
                {weather.temperature_c !== null && `${formatNumber(weather.temperature_c, 1)}°C`}
                {weather.wind_speed_ms !== null &&
                  ` · ${formatNumber(weather.wind_speed_ms, 1)} m/s`}
                {weather.wind_direction_deg !== null &&
                  ` (${Math.round(weather.wind_direction_deg)}°)`}
                {weather.humidity_pct !== null && ` · ${Math.round(weather.humidity_pct)}% RH`}
              </>
            ) : (
              'Weather observation unavailable'
            )}
          </p>
        </div>
      </div>

      {/* Visual interpretation requires a real georeferenced satellite raster. */}
      <div className="gemini-satellite-card">
        <div className="gemini-card-header">
          <div className="gemini-title-group">
            <span className="gemini-badge">Satellite imagery</span>
            <strong>Visual interpretation unavailable</strong>
          </div>
          <span className="advisory-caution-tag">Awaiting georeferenced raster data</span>
        </div>

        <p className="muted gemini-scope-text">
          {analysis.analysis_unavailable_reason ??
            'A satellite image is required before visual interpretation can run.'}
        </p>
      </div>
    </section>
  )
}
