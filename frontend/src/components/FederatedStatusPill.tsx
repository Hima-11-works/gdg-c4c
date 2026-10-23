import { useState } from 'react'
import { fetchFederationStatus, fetchFederationWorkflowStatus } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import {
  evaluation,
  hasRun,
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
import type { FederationStatusOut, FederationWorkflowStatusOut } from '../lib/types'

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

export function FederatedStatusPill() {
  const [open, setOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<'workflow' | 'partition'>('workflow')
  const { resource, refetch } = useApiResource(fetchFederationStatus, [], {
    pollIntervalMs: POLL_INTERVAL_MS,
  })
  const { resource: workflowResource, refetch: refetchWorkflow } = useApiResource(
    fetchFederationWorkflowStatus,
    [],
    { pollIntervalMs: POLL_INTERVAL_MS },
  )

  const status: FederationStatusOut | null = resource.status === 'success' ? resource.data : null
  const workflowStatus: FederationWorkflowStatusOut | null =
    workflowResource.status === 'success' ? workflowResource.data : null
  const failed = workflowResource.status === 'error'
  const loading = workflowResource.status === 'idle' || workflowResource.status === 'loading'
  const partitionFailed = resource.status === 'error'
  const partitionLoading = resource.status === 'idle' || resource.status === 'loading'
  const succeeded = workflowStatus?.status === 'aggregated'
  const runFailed = workflowStatus?.status === 'failed'

  const dotClass =
    failed || runFailed
      ? 'federated-dot federated-dot-unavailable'
      : loading
        ? 'federated-dot federated-dot-loading'
        : succeeded
          ? 'federated-dot federated-dot-ok'
          : 'federated-dot federated-dot-none'

  const label = loading
    ? 'Federation workflow: checking…'
    : failed
      ? 'Federation workflow: status unavailable'
      : runFailed
        ? 'Federation demo: last run failed'
        : succeeded
          ? 'Federation demo: run succeeded'
          : 'Federation demo: no run recorded'

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
        title="Status from the configured, separately running federation aggregator."
      >
        <span className={dotClass} aria-hidden="true" />
        {label}
      </button>

      {open && (
        <div className="panel federated-panel">
          <div className="federated-header-row">
            <div>
              <h3 className="federated-title">
                Federated Climate Model Demo
                <span className="federated-badge">multi-client</span>
              </h3>
              <p className="muted federated-subtitle">
                Status comes from the configured client and aggregator processes. Participant
                identities are not independently verified as Indian agencies.
              </p>
            </div>
            <button
              type="button"
              className="cell-close-btn"
              onClick={() => setOpen(false)}
              aria-label="Close panel"
              style={{ alignSelf: 'flex-start' }}
            >
              ✕
            </button>
          </div>

          <div className="federated-tab-bar">
            <button
              type="button"
              className={`federated-tab-btn ${activeTab === 'workflow' ? 'active' : ''}`}
              onClick={() => setActiveTab('workflow')}
            >
              Client/aggregator run
            </button>
            <button
              type="button"
              className={`federated-tab-btn ${activeTab === 'partition' ? 'active' : ''}`}
              onClick={() => setActiveTab('partition')}
            >
              Single-process partition demo
            </button>
          </div>

          {activeTab === 'workflow' && (
            <div className="federated-aggregator-view">
              {loading && <p className="muted">Reading the configured federation aggregator…</p>}
              {failed && (
                <>
                  <h3>Client/aggregator status unavailable</h3>
                  <p className="muted">
                    Configure the backend's trusted FEDERATION_WORKFLOW_STATUS_URL to the
                    aggregator's GET /status endpoint. The separate demo process may still be
                    running; this screen does not substitute sample telemetry.
                  </p>
                  <p className="federated-error" role="alert">
                    {workflowResource.message}
                  </p>
                  <button type="button" className="federated-refresh" onClick={refetchWorkflow}>
                    Check again
                  </button>
                </>
              )}
              {workflowResource.status === 'success' && (
                <FederationWorkflowRunDetails
                  status={workflowResource.data}
                  onRefresh={refetchWorkflow}
                />
              )}
            </div>
          )}

          {activeTab === 'partition' && (
            <div className="federated-aggregator-view">
              {partitionLoading && <p className="muted">Reading the demonstration status…</p>}

              {partitionFailed && (
                <>
                  <h3>Federation status unavailable</h3>
                  <p className="muted">
                    The database-backed, single-process partition demonstration could not be read.
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
                  <h3>No single-process partition run recorded</h3>
                  <p className="muted">
                    Run python -m app.cli federation-demo to create this optional local run.
                  </p>
                  <p className="muted federated-scope">
                    Default regional scope: <b>{scopeLine(status)}</b>.
                  </p>
                  <button type="button" className="federated-refresh" onClick={refetch}>
                    Refresh Aggregator State
                  </button>
                </>
              )}

              {resource.status === 'success' && hasRun(status) && (
                <FederationRunDetails status={status} onRefresh={refetch} />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FederationWorkflowRunDetails({
  status,
  onRefresh,
}: {
  status: FederationWorkflowStatusOut
  onRefresh: () => void
}) {
  const comparisons = status.evaluation.local_vs_aggregate ?? []
  return (
    <>
      <h3>
        Separate client/aggregator workflow
        <span className="federated-badge">{status.status}</span>
      </h3>
      <p className="muted">{status.workflow}</p>
      <dl className="federated-grid">
        <dt>Run</dt>
        <dd className="federated-mono">{status.run_id}</dd>
        <dt>Round completion</dt>
        <dd>
          {formatCount(status.participants_received)} /{' '}
          {formatCount(status.participants_expected)} client updates · {status.status}
        </dd>
        <dt>Update bytes received</dt>
        <dd>
          {status.model_update_bytes_exchanged === undefined
            ? 'not reported by this aggregator version'
            : `${formatCount(status.model_update_bytes_exchanged)} bytes`}
        </dd>
        <dt>Raw rows received</dt>
        <dd>{formatCount(status.raw_rows_exchanged_to_aggregator)}</dd>
        <dt>Data provenance</dt>
        <dd>
          {status.synthetic_only
            ? 'Synthetic-only demonstration'
            : 'Observed data, participant-reported'}{' '}
          · {status.independent_agencies
            ? 'agency identity verified'
            : 'agencies not independently verified'}
        </dd>
      </dl>

      <h4 className="federated-subhead">Authenticated client updates</h4>
      {status.participants.length === 0 ? (
        <p className="muted">No client updates received for this run yet.</p>
      ) : (
        <ul className="federated-regions">
          {status.participants.map((participant) => (
            <li key={participant.participant_id}>
              <div className="federated-region-head">
                <b>{participant.participant_id}</b>
                <span className="muted">
                  {participant.signature_verified
                    ? 'signature verified'
                    : 'signature not verified'}
                </span>
              </div>
              <span className="muted">
                Scope {participant.scope.scope_id ?? 'not reported'} · update{' '}
                {participant.update_size_bytes === undefined
                  ? 'update size not reported'
                  : `${formatCount(participant.update_size_bytes)} bytes`}
                {' · '}held-out evaluation{' '}
                {participant.evaluation_reported ? 'reported' : 'pending'}
              </span>
              {participant.scope.dataset_sha256 && (
                <div className="federated-mono federated-hash-text">
                  Local dataset digest: {participant.scope.dataset_sha256}
                </div>
              )}
              <div className="federated-mono federated-hash-text">
                Update SHA-256: {participant.update_sha256}
              </div>
            </li>
          ))}
        </ul>
      )}

      {status.aggregate && (
        <>
          <h4 className="federated-subhead">Aggregate artifact</h4>
          <p className="federated-mono federated-hash-text">
            SHA-256: {status.aggregate.artifact_sha256}
          </p>
          <p className="muted">{status.aggregate.algorithm}</p>
        </>
      )}

      <h4 className="federated-subhead">Local versus aggregate evaluation</h4>
      <p className="muted">
        Evaluation is calculated on each client&apos;s local held-out examples. Only metrics are
        returned; raw rows stay on the client. These prototype scores are not evidence of
        real-world accuracy.
      </p>
      {comparisons.length === 0 ? (
        <p className="muted">Client evaluations have not been reported yet.</p>
      ) : (
        <ul className="federated-metrics">
          {comparisons.flatMap((client) =>
            client.horizons.map((horizon) => (
              <li key={`${client.participant_id}-${horizon.horizon_hours}`}>
                {client.participant_id} +{horizon.horizon_hours}h: local MAE{' '}
                {formatNumber(horizon.local_mae_ugm3)} · aggregate MAE{' '}
                {formatNumber(horizon.aggregate_mae_ugm3)} · persistence{' '}
                {formatNumber(horizon.baseline_mae_ugm3)} µg/m³ · held out{' '}
                {formatCount(horizon.heldout_count)}
              </li>
            )),
          )}
        </ul>
      )}

      <p className="muted">
        {status.evaluation.status}: {status.evaluation.reason}
      </p>
      {Object.entries(status.limitations).length > 0 && (
        <ul className="federated-limitations">
          {Object.entries(status.limitations).map(([key, value]) => (
            <li key={key}>
              <b>{key}:</b> {value}
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="federated-refresh" onClick={onRefresh}>
        Refresh workflow status
      </button>
    </>
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
        Model updates only — {formatCount(status.raw_rows_exchanged_to_aggregator)} raw observation
        rows reached the aggregator. Participants exchange fitted parameters and counts, which is{' '}
        <b>not</b> a privacy guarantee.
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
