import { useState, useEffect } from 'react'
import {
  cellSatelliteThumbnailUrl,
  fetchCellSatelliteContext,
  triggerCellSatelliteAnalysis,
} from '../lib/api'
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
  const { resource, refetch } = useApiResource<CellSatelliteAnalysisOut>(
    () => fetchCellSatelliteContext(h3Cell, resolution),
    [h3Cell, resolution],
  )

  const [analysisOverride, setAnalysisOverride] = useState<CellSatelliteAnalysisOut | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisError, setAnalysisError] = useState<string | null>(null)

  useEffect(() => {
    setAnalysisOverride(null)
  }, [h3Cell, resolution])

  const handleRunAnalysis = async (reanalyze: boolean = false) => {
    setAnalyzing(true)
    setAnalysisError(null)
    try {
      const response = await triggerCellSatelliteAnalysis(h3Cell, reanalyze, resolution)
      setAnalysisOverride(response.data)
    } catch (err: unknown) {
      if (err instanceof Error) {
        setAnalysisError(err.message)
      } else {
        setAnalysisError('Unable to complete Gemini satellite analysis.')
      }
    } finally {
      setAnalyzing(false)
    }
  }

  if (resource.status === 'loading') {
    return (
      <section className="cell-satellite-section" aria-label="Satellite and air quality context">
        <h3>Satellite & Surface Air Quality</h3>
        <p className="muted">Loading satellite and monitor observations…</p>
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

  const analysis = analysisOverride ?? resource.data
  const bundle = analysis.evidence_bundle
  const interpretation = analysis.interpretation
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
        <span className="coverage-badge">
          H3 Res {bundle.resolution} · {Math.round((no2.coverage_fraction ?? 1) * 100)}% coverage
        </span>
      </div>

      {/* Surface PM2.5 vs Satellite Distinction */}
      <div className="surface-air-card">
        <div className="surface-air-title-row">
          <strong>Measured Surface Air Quality</strong>
          <span className={`surface-kind-tag ${surface.is_estimate ? 'tag-estimate' : 'tag-observed'}`}>
            {surface.is_estimate ? 'Validated Surface Estimate' : 'Station Observation'}
          </span>
        </div>

        <dl className="satellite-metrics-grid">
          <dt>Surface PM2.5</dt>
          <dd>
            {surface.status === 'available' && surface.value_ugm3 !== null ? (
              <>
                <strong>{formatNumber(surface.value_ugm3)} µg/m³</strong>
                {surface.uncertainty_ugm3 && (
                  <span className="muted"> (±{formatNumber(surface.uncertainty_ugm3)})</span>
                )}
                {surface.station_distance_km !== null && surface.station_distance_km > 0 && (
                  <span className="muted station-distance">
                    {' '}· {formatNumber(surface.station_distance_km)} km from cell center
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
                    <span className="muted">({no2.value.toExponential(2)} {no2.unit})</span>
                  </span>
                ) : (
                  <span className="muted">{no2.status}</span>
                )}
              </td>
              <td>
                {no2.qa_score !== null ? `${Math.round(no2.qa_score * 100)}% QA` : '—'}
              </td>
              <td className="muted">
                {no2.observed_at ? new Date(no2.observed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—'}
              </td>
            </tr>
            <tr>
              <td>
                <strong>Sentinel-5P UVAI</strong>
                <span className="muted sub-label">Aerosol absorption</span>
              </td>
              <td>
                {uvai.status === 'available' && uvai.value !== null ? (
                  <span>{formatNumber(uvai.value, 2)} <span className="muted">(index)</span></span>
                ) : (
                  <span className="muted">{uvai.status}</span>
                )}
              </td>
              <td>
                {uvai.qa_score !== null ? `${Math.round(uvai.qa_score * 100)}% QA` : '—'}
              </td>
              <td className="muted">
                {uvai.observed_at ? new Date(uvai.observed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—'}
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
            {firms.detection_count > 0 ? (
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
            {weather.temperature_c !== null || weather.wind_speed_ms !== null ? (
              <>
                {weather.temperature_c !== null && `${formatNumber(weather.temperature_c, 1)}°C`}
                {weather.wind_speed_ms !== null && ` · ${formatNumber(weather.wind_speed_ms, 1)} m/s`}
                {weather.wind_direction_deg !== null && ` (${Math.round(weather.wind_direction_deg)}°)`}
                {weather.humidity_pct !== null && ` · ${Math.round(weather.humidity_pct)}% RH`}
              </>
            ) : (
              'Weather observation unavailable'
            )}
          </p>
        </div>
      </div>

      {/* Gemini Advisory Section */}
      <div className="gemini-satellite-card">
        <div className="gemini-card-header">
          <div className="gemini-title-group">
            <span className="gemini-badge">Gemini Advisory</span>
            <strong>Satellite Pattern Interpretation</strong>
          </div>
          <span className="advisory-caution-tag">AI-assisted, uncertain, and advisory</span>
        </div>

        <p className="muted gemini-scope-text">
          Advisory visual pattern analysis. Gemini cannot infer ground AQI, confirm pollution
          sources, or trigger emergency response. All numeric data comes from server pipelines.
        </p>

        {analysis.thumbnail_available && (
          <div className="gemini-thumbnail-preview">
            <img
              src={cellSatelliteThumbnailUrl(h3Cell)}
              alt={`Satellite layer thumbnail for cell ${h3Cell}`}
              className="cell-thumbnail-img"
              loading="lazy"
            />
          </div>
        )}

        {interpretation ? (
          <div className="gemini-interpretation-body">
            <div className="pattern-row">
              <span className="pattern-label">Visual Pattern:</span>
              <strong className={`pattern-tag pattern-${interpretation.visual_pattern}`}>
                {interpretation.visual_pattern.replace('_', ' ')}
              </strong>
              <span className="muted">({interpretation.possible_event_type})</span>
            </div>

            <p className="interpretation-summary">{interpretation.summary}</p>

            {interpretation.supporting_evidence.length > 0 && (
              <div className="interpretation-evidence">
                <strong>Supporting observations:</strong>
                <ul>
                  {interpretation.supporting_evidence.map((item, idx) => (
                    <li key={idx}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {interpretation.limitations.length > 0 && (
              <div className="interpretation-limitations">
                <strong>Limitations & caveats:</strong>
                <ul>
                  {interpretation.limitations.map((item, idx) => (
                    <li key={idx}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="gemini-meta-footer">
              <span className="muted">
                {analysis.cached ? 'Loaded from cache' : 'Newly generated'} ·{' '}
                {analysis.generated_at ? new Date(analysis.generated_at).toLocaleTimeString() : ''} · Model:{' '}
                {analysis.model_id ?? 'Gemini'}
              </span>
              <button
                type="button"
                className="btn-gemini-rerun"
                disabled={analyzing}
                onClick={() => handleRunAnalysis(true)}
              >
                {analyzing ? 'Refreshing…' : 'Re-analyze with Gemini'}
              </button>
            </div>
          </div>
        ) : (
          <div className="gemini-empty-action">
            <p className="muted">
              Request an AI-assisted visual pattern interpretation of this cell’s satellite evidence.
            </p>
            <button
              type="button"
              className="btn-gemini-action"
              disabled={analyzing}
              onClick={() => handleRunAnalysis(false)}
            >
              {analyzing ? 'Analyzing with Gemini…' : '✨ Analyze Satellite Context with Gemini'}
            </button>
          </div>
        )}

        {analysisError && (
          <div className="gemini-error-banner" role="alert">
            <span>{analysisError}</span>
          </div>
        )}
      </div>
    </section>
  )
}
