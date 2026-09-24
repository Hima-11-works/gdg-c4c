import { useState } from 'react'
import { fetchFederationStatus } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import {
  evaluation,
  hasRun,
  headline,
  latestAggregationTime,
  limitations,
  modelVersions,
  participants,
  scopeLine,
  syntheticOnly,
  usableAsEvidence,
} from '../lib/federation'
import { formatCount, formatNumber, relativeTime } from '../lib/format'
import type { AsyncResource } from '../hooks/useApiResource'
import type { FederationStatusOut } from '../lib/types'

// The demonstration is a recorded run, not a live feed: it changes only when
// someone runs `python -m app.cli federation-demo`. A slow poll is enough.
const POLL_INTERVAL_MS = 5 * 60 * 1000

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`
}

function when(iso: string | null | undefined): string {
  if (iso === null || iso === undefined) return 'not recorded'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'not recorded'
  return `${date.toLocaleString()} (${relativeTime(iso)})`
}

/**
 * The federation demonstration's status.
 *
 * This replaced a fixed "illustrative (12 nodes)" placeholder. The real
 * endpoint reports a two-partition *synthetic demonstration*, and the panel
 * repeats its caveats rather than smoothing them into a coverage claim: the
 * participating regions are named as partitions, the aggregate is marked
 * synthetic-only, the evaluation is marked not usable as real-world evidence,
 * and the recorded limitations are shown word for word.
 *
 * Three states are kept apart on purpose. A recorded run, no run recorded yet,
 * and **the endpoint being unreachable** are different facts, and only the
 * last is an error — an unavailable endpoint must never be rendered as
 * "no federation run", which would be a claim about the backend rather than
 * about the connection.
 */
export function FederatedStatusPill() {
  const [open, setOpen] = useState(false)
  const { resource, refetch } = useApiResource(fetchFederationStatus, [], {
    pollIntervalMs: POLL_INTERVAL_MS,
  })

  const status: FederationStatusOut | null =
    resource.status === 'success' ? resource.data : null
  const failed = resource.status === 'error'
  const loading = resource.status === 'idle' || resource.status === 'loading'

  const dotClass = failed
    ? 'federated-dot federated-dot-unavailable'
    : loading
      ? 'federated-dot federated-dot-loading'
      : hasRun(status)
        ? 'federated-dot federated-dot-ok'
        : 'federated-dot federated-dot-none'

  const label = loading ? 'Federation: checking…' : headline(status)

  const checkAgain = () => {
    if (resource.status === 'error') refetch()
  }

  return (
    <div className="federated">
      <button
        type="button"
        className="federated-pill"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title="The two-region federated-training demonstration: two synthetic partitions exchanging model updates. Not a nationwide deployment and not evidence of privacy."
      >
        <span className={dotClass} aria-hidden="true" />
        {label}
      </button>

      {open && (
        <div className="panel federated-panel">
          {loading && <p className="muted">Reading the demonstration status…</p>}

          {failed && (
            <>
              <h3>Federation status unavailable</h3>
              <p className="muted">
                The status endpoint could not be read, so this dashboard has nothing to report about
                the demonstration. This is not the same as “no run recorded” — the backend may well
                hold one.
              </p>
              <p className="federated-error" role="alert">
                {resource.message}
              </p>
              <button type="button" className="federated-refresh" onClick={checkAgain}>
                Check again
              </button>
            </>
          )}

          {resource.status === 'success' && !hasRun(status) && (
            <>
              <h3>No federation run recorded</h3>
              <p className="muted">
                The endpoint answered and reports no demonstration run yet. Run one with
                <code> python -m app.cli federation-demo</code> against this backend, then check
                again.
              </p>
              <p className="muted federated-scope">
                Scope if a run exists: <b>{scopeLine(status)}</b>.
              </p>
              <button type="button" className="federated-refresh" onClick={refetch}>
                Check again
              </button>
            </>
          )}

          {resource.status === 'success' && hasRun(status) && (
            <FederationRunDetails status={status} onRefresh={refetch} />
          )}
        </div>
      )}
    </div>
  )
}

function FederationRunDetails({
  status,
  onRefresh,
}: {
  status: FederationStatusOut
  onRefresh: () => void
}) {
  const regions = participants(status)
  const models = modelVersions(status)
  const evaluationBlock = evaluation(status)
  const synthetic = syntheticOnly(status)
  const usable = usableAsEvidence(status)
  const recordedLimitations = limitations(status)

  return (
    <>
      <h3>
        Federation demonstration
        <span className="federated-badge">experimental</span>
      </h3>

      <dl className="federated-grid">
        <dt>Run</dt>
        <dd className="federated-mono">{status.run_id ?? 'not recorded'}</dd>

        <dt>Run status</dt>
        <dd>{status.status}</dd>

        <dt>Scope</dt>
        <dd>
          {scopeLine(status)}
          <span className="muted">
            {' '}
            — two disjoint partitions of one synthetic demo dataset, not a nationwide deployment.
          </span>
        </dd>

        <dt>Latest aggregation</dt>
        <dd>{when(latestAggregationTime(status))}</dd>

        <dt>Feature schema</dt>
        <dd>{status.feature_schema_version ?? 'not recorded'}</dd>

        <dt>Data</dt>
        <dd>
          {synthetic === null
            ? 'synthetic-only flag not reported'
            : synthetic
              ? 'Synthetic only — no observed labels participate'
              : 'Not marked synthetic'}
        </dd>
      </dl>

      <h4 className="federated-subhead">Participating regions</h4>
      {regions.length === 0 ? (
        <p className="muted">None reported.</p>
      ) : (
        <ul className="federated-regions">
          {regions.map((region) => (
            <li key={region.participant_id}>
              <div className="federated-region-head">
                <b>{region.region_label}</b>
                <span className="muted">weight {percent(region.weight_fraction)}</span>
              </div>
              <span className="muted">
                {region.participant_id} · {formatCount(region.train_count)} train rows ·{' '}
                {formatCount(region.station_count)} stations
                {region.joined_at !== undefined && ` · joined ${relativeTime(region.joined_at)}`}
              </span>
            </li>
          ))}
        </ul>
      )}

      <h4 className="federated-subhead">Model versions</h4>
      {models.length === 0 ? (
        <p className="muted">None reported.</p>
      ) : (
        <ul className="federated-models">
          {models.map((model) => (
            <li key={model.modelId}>
              <span className="federated-mono">{model.modelId}</span>
              <span className="muted">
                {model.detail === null
                  ? // A persisted run reports ids only — say so rather than
                    // implying a status the payload never carried.
                    ' · per-model status not reported for a persisted run'
                  : ` · +${model.detail.horizon_hours}h · ${model.detail.status}${
                      model.detail.synthetic_only ? ' · synthetic only (never promotable)' : ''
                    }`}
              </span>
            </li>
          ))}
        </ul>
      )}

      <h4 className="federated-subhead">Evaluation</h4>
      {evaluationBlock === null ? (
        <p className="muted">Not reported.</p>
      ) : (
        <>
          <p className="muted">
            {evaluationBlock.status}
            {usable === null
              ? ''
              : usable
                ? ' — usable as real-world evidence'
                : ' — not usable as real-world evidence'}
            {evaluationBlock.reason !== '' && ` · ${evaluationBlock.reason}`}
          </p>
          {(evaluationBlock.horizons ?? []).length > 0 && (
            <ul className="federated-metrics">
              {(evaluationBlock.horizons ?? []).map((horizon) => (
                <li key={horizon.horizon_hours}>
                  +{horizon.horizon_hours}h: MAE {formatNumber(horizon.mae_ugm3)} µg/m³ vs baseline{' '}
                  {formatNumber(horizon.baseline_mae_ugm3)} · held out{' '}
                  {formatCount(horizon.heldout_count)}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <h4 className="federated-subhead">What was exchanged</h4>
      <p className="muted">
        Model updates only — {formatCount(status.raw_rows_exchanged_to_aggregator)}{' '}
        raw observation rows reached the aggregator. Participants exchange fitted parameters and
        counts, which is <b>not</b> a privacy guarantee.
      </p>

      {recordedLimitations.length > 0 && (
        <>
          <h4 className="federated-subhead">Limitations, as recorded</h4>
          <ul className="federated-limitations">
            {recordedLimitations.map((item) => (
              <li key={item.key}>
                <b>{item.key}:</b> {item.text}
              </li>
            ))}
          </ul>
        </>
      )}

      <button type="button" className="federated-refresh" onClick={onRefresh}>
        Check again
      </button>
    </>
  )
}

/** Narrow type used by the pill's callers; kept here so the component's
 *  public shape is explicit. */
export type FederatedResource = AsyncResource<FederationStatusOut>
