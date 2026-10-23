import { useApiResource } from '../hooks/useApiResource'
import { fetchCurrentCorridor } from '../lib/api'
import type { FreightCorridorProperties } from '../lib/freightCorridors'
import { useMapUi } from '../state/MapUiContext'

interface CorridorDetailPanelProps {
  corridor: FreightCorridorProperties
  runId?: string
}

function formatNumber(value: number | null, digits = 1): string {
  return value === null
    ? '—'
    : value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export function CorridorDetailPanel({ corridor, runId }: CorridorDetailPanelProps) {
  const { dispatch } = useMapUi()
  const current = useApiResource(
    (signal) => fetchCurrentCorridor(corridor.corridor_id, runId, signal),
    [corridor.corridor_id, runId],
  )
  const close = () => dispatch({ type: 'SELECT_CORRIDOR', corridor: null })

  return (
    <aside className="panel cell-detail corridor-detail" aria-label="Corridor forecast details">
      <div className="cell-detail-header">
        <div className="cell-detail-titles">
          <h2>{corridor.name}</h2>
          <span className="cell-detail-index">
            {corridor.kind} · H3 resolution {corridor.h3_resolution} · {corridor.cell_count} cells
          </span>
        </div>
        <button
          type="button"
          className="cell-detail-close"
          onClick={close}
          aria-label="Close corridor details"
          title="Close"
        >
          ✕
        </button>
      </div>

      <section className="corridor-section" aria-labelledby="corridor-geometry-title">
        <h3 id="corridor-geometry-title">Geometry provenance</h3>
        <p className="corridor-demo-notice" role="status">
          {corridor.geometry_source}: {corridor.geometry_note}
        </p>
        <p className="muted">{corridor.geometry_description}</p>
      </section>

      <section className="corridor-section" aria-labelledby="corridor-forecast-title">
        <h3 id="corridor-forecast-title">Latest published forecast</h3>
        {current.resource.status === 'idle' || current.resource.status === 'loading' ? (
          <p role="status">Loading the corridor forecast…</p>
        ) : current.resource.status === 'error' ? (
          <p role="status">{current.resource.message}</p>
        ) : (
          <>
            {current.resource.isDemo && (
              <p className="banner banner-demo" role="status">
                Scenario data — illustrative, not live.
              </p>
            )}
            <dl className="cell-detail-grid">
              <dt>Run</dt>
              <dd>{current.resource.data.event.run_id}</dd>
              <dt>Run mode</dt>
              <dd>{current.resource.data.event.run_mode}</dd>
              <dt>Issued</dt>
              <dd>{formatTime(current.resource.data.event.issued_at)}</dd>
              <dt>Forecast method</dt>
              <dd>
                {current.resource.data.event.prediction_methods.join(', ') || 'Not recorded'}
              </dd>
              <dt>Model version</dt>
              <dd>
                {current.resource.data.event.model_versions.join(', ') || 'Baseline method'}
              </dd>
              <dt>Maximum forecast PM2.5</dt>
              <dd>
                {formatNumber(current.resource.data.event.peak_predicted_ugm3)} µg/m³
                {current.resource.data.event.peak_horizon_hours === null
                  ? ''
                  : ` at +${current.resource.data.event.peak_horizon_hours}h`}
              </dd>
              <dt>Forecast cells</dt>
              <dd>
                {current.resource.data.event.cell_count} corridor cells ·{' '}
                {current.resource.data.event.horizons.length} horizons
              </dd>
            </dl>
            <h4>Forecast input sources</h4>
            {current.resource.data.event.dataset_versions.length === 0 ? (
              <p className="muted">
                No dataset references were recorded for these corridor results.
              </p>
            ) : (
              <ul>
                {current.resource.data.event.dataset_versions.map((dataset) => (
                  <li key={dataset.dataset_id}>
                    {dataset.source} · {dataset.product} {dataset.version} · {dataset.kind}
                  </li>
                ))}
              </ul>
            )}

            <h4>Observed-data evaluation</h4>
            <p role="status">
              {current.resource.data.evaluation.usable_as_real_world_evidence
                ? `Evaluated against ${current.resource.data.event.label_count} observed station labels.`
                : 'Insufficient validation data; this run does not establish real-world accuracy.'}
            </p>
            {current.resource.data.evaluation.reasons.length > 0 && (
              <ul>
                {current.resource.data.evaluation.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
            {current.resource.data.evaluation.slices.length > 0 && (
              <ul>
                {current.resource.data.evaluation.slices.map((slice) => (
                  <li key={`${slice.horizon_hours}-${slice.geography}`}>
                    +{slice.horizon_hours}h · {slice.geography} · {slice.pairs} pairs ·{' '}
                    {slice.sufficient
                      ? `MAE ${formatNumber(slice.mae_ugm3)} µg/m³`
                      : slice.note}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <p className="muted">
        This panel reports the published model run and its evaluation; it sends no authority action.
      </p>
    </aside>
  )
}
