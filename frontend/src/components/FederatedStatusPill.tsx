import { useState } from 'react'
import { fetchFederationStatus } from '../lib/api'
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
import { STATE_FEDERATED_NODES } from '../lib/federationNodes'
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

export function FederatedStatusPill() {
  const [open, setOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<'nodes' | 'aggregator'>('nodes')
  const { resource, refetch } = useApiResource(fetchFederationStatus, [], {
    pollIntervalMs: POLL_INTERVAL_MS,
  })

  const status: FederationStatusOut | null = resource.status === 'success' ? resource.data : null
  const failed = resource.status === 'error'
  const loading = resource.status === 'idle' || resource.status === 'loading'
  const succeeded = hasRun(status) && status.status === 'succeeded'
  const runFailed = hasRun(status) && status.status === 'failed'

  const dotClass =
    failed || runFailed
      ? 'federated-dot federated-dot-unavailable'
      : loading
        ? 'federated-dot federated-dot-loading'
        : succeeded
          ? 'federated-dot federated-dot-ok'
          : 'federated-dot federated-dot-none'

  const label = loading
    ? 'Federation: checking…'
    : failed
      ? 'Federation: status unavailable'
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
        title="Federation demo status. The state-node cards are illustrative samples and are not connected to live state boards."
      >
        <span className={dotClass} aria-hidden="true" />
        {label}
      </button>

      {open && (
        <div className="panel federated-panel">
          <div className="federated-header-row">
            <div>
              <h3 className="federated-title">
                Federated Air Quality Network
                <span className="federated-badge">India Mesh</span>
              </h3>
              <p className="muted federated-subtitle">
                Demonstration of regional model collaboration; no live state-board nodes are
                connected.
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
              className={`federated-tab-btn ${activeTab === 'nodes' ? 'active' : ''}`}
              onClick={() => setActiveTab('nodes')}
            >
              Sample Nodes ({STATE_FEDERATED_NODES.length})
            </button>
            <button
              type="button"
              className={`federated-tab-btn ${activeTab === 'aggregator' ? 'active' : ''}`}
              onClick={() => setActiveTab('aggregator')}
            >
              Central Aggregator Telemetry
            </button>
          </div>

          {activeTab === 'nodes' && (
            <div className="federated-nodes-view">
              <p className="federated-demo-notice" role="note">
                Illustrative mock data only. Node status, station counts, sensor hours, weights,
                privacy methods, and hashes are examples—not live measurements or verified privacy
                controls. The aggregator tab is the only endpoint-backed status.
              </p>
              <div className="federated-weights-card">
                <div className="federated-weights-label">
                  <span>Example aggregation weights</span>
                  <span className="federated-mono">illustrative</span>
                </div>
                <div className="federated-weight-bar">
                  {STATE_FEDERATED_NODES.map((node) => (
                    <div
                      key={node.id}
                      className="federated-weight-segment"
                      style={{
                        width: `${node.aggregationWeight * 100}%`,
                        backgroundColor: node.color,
                      }}
                      title={`${node.code}: ${(node.aggregationWeight * 100).toFixed(0)}%`}
                    >
                      <span className="federated-segment-text">{node.code}</span>
                    </div>
                  ))}
                </div>
                <div className="federated-weights-legend">
                  {STATE_FEDERATED_NODES.map((node) => (
                    <div key={node.id} className="federated-legend-item">
                      <span
                        className="federated-legend-dot"
                        style={{ backgroundColor: node.color }}
                      />
                      <span className="federated-legend-code">{node.code}</span>
                      <span className="muted">{(node.aggregationWeight * 100).toFixed(0)}%</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="federated-nodes-list">
                {STATE_FEDERATED_NODES.map((node) => (
                  <div key={node.id} className="federated-node-card">
                    <div className="federated-node-header">
                      <div className="federated-node-title-group">
                        <span
                          className="federated-state-badge"
                          style={{ borderColor: `${node.color}66`, color: node.color }}
                        >
                          {node.code}
                        </span>
                        <div>
                          <div className="federated-node-name">{node.name}</div>
                          <div className="federated-node-agency muted">{node.agency}</div>
                        </div>
                      </div>
                      <span className="federated-status-tag">
                        Example round {node.currentRound}/{node.totalRounds} · not live
                      </span>
                    </div>

                    <div className="federated-node-jurisdiction">
                      <span className="muted">Jurisdiction:</span> {node.jurisdiction}
                    </div>

                    <div className="federated-node-focus">
                      <span className="muted">Monitoring Target:</span> {node.focusArea}
                    </div>

                    <div className="federated-node-metrics">
                      <div className="federated-node-metric-cell">
                        <div className="federated-metric-val">{node.stationCount}</div>
                        <div className="muted federated-metric-lbl">Sample stations</div>
                      </div>
                      <div className="federated-node-metric-cell">
                        <div className="federated-metric-val">
                          {formatCount(node.localSensorHours)}
                        </div>
                        <div className="muted federated-metric-lbl">Sample sensor hours</div>
                      </div>
                      <div className="federated-node-metric-cell">
                        <div className="federated-metric-val">
                          {(node.aggregationWeight * 100).toFixed(0)}%
                        </div>
                        <div className="muted federated-metric-lbl">Sample model weight</div>
                      </div>
                    </div>

                    <div className="federated-node-footer">
                      <div className="federated-privacy-note">
                        <span className="federated-shield-icon" aria-hidden="true">
                          ⓘ
                        </span>
                        <span>Example only; not verified: {node.privacyProtocol}</span>
                      </div>
                      <div
                        className="federated-mono federated-hash-text"
                        title="Illustrative placeholder hash, not from a live node"
                      >
                        Sample hash: {node.lastWeightHash}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeTab === 'aggregator' && (
            <div className="federated-aggregator-view">
              {loading && <p className="muted">Reading the demonstration status…</p>}

              {failed && (
                <>
                  <h3>Federation status unavailable</h3>
                  <p className="muted">
                    The status endpoint could not be read. The backend central aggregator may still
                    be running training rounds in the background.
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
                  <h3>Central CPCB Aggregator · Standby</h3>
                  <p className="muted">
                    The central server is currently listening for regional state board gradient
                    updates. Local nodes retain raw station telemetry on-premise.
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
