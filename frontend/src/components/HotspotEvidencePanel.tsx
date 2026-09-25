import { useEffect, useState } from 'react'
import { useMapUi } from '../state/MapUiContext'
import {
  confidencePhrase,
  evaluationCaveat,
  isInsufficientScan,
  metricOrNotComputed,
  reviewPhrase,
} from '../lib/hotspotScans'
import type { HotspotScanOut } from '../lib/types'
import type { AsyncResource } from '../hooks/useApiResource'
import { SidePanel } from './SidePanel'

function when(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'unreadable timestamp'
  return `${date.toLocaleString('en-GB', { timeZone: 'UTC' })} UTC`
}

const CONFIDENCE_CLASS: Record<string, string> = {
  low: 'hotspot-status-low',
  medium: 'hotspot-status-medium',
  high: 'hotspot-status-high',
}

/**
 * What the candidate-hotspot detector recorded, and what it refuses to claim.
 *
 * The three states the contract separates are kept apart here: a scan with
 * candidates, a scan that *ran and found none*, and a scan that could not look
 * at all (`insufficient_evidence`). Only the last means no imagery reached the
 * detector, and it is never shown as "no hotspots found".
 */
export function HotspotEvidencePanel({
  index,
  scans,
  onSelectScan,
  scanError = null,
}: {
  index: AsyncResource<{
    detector_version: string
    trigger: string
    supporting_signals: string[]
    bounds: Record<string, unknown>
    scans: import('../lib/types').HotspotScanRowOut[]
    limitations: Record<string, string>
  }>
  scans: Record<string, HotspotScanOut | undefined>
  onSelectScan: (scanId: string) => void
  scanError?: string | null
}) {
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(null)
  const { state, dispatch } = useMapUi()

  // A map click names a candidate_id; the panel keys on the same string.
  useEffect(() => {
    const onSelected = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail
      const id = detail?.candidate_id
      if (typeof id === 'string') setSelectedCandidate(id)
    }
    window.addEventListener('air-health:hotspot-candidate-selected', onSelected)
    return () => window.removeEventListener('air-health:hotspot-candidate-selected', onSelected)
  }, [])

  const indexData = index.status === 'success' ? index.data : null
  const rows = indexData?.scans ?? []
  const failed = index.status === 'error'
  const loading = index.status === 'loading' || index.status === 'idle'

  // The scan whose candidates are on the map is the last one selected.
  const activeScanId = Object.keys(scans).find((id) => scans[id] !== undefined) ?? null
  const activeScan = activeScanId === null ? undefined : scans[activeScanId]
  const candidate =
    activeScan?.candidates.find((c) => c.candidate_id === selectedCandidate) ?? null

  return (
    <SidePanel
      id="hotspot-evidence-panel"
      side="left"
      open={state.hotspotPanelOpen}
      onToggle={() => dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })}
      label={state.hotspotPanelOpen ? 'Hide hotspot candidate review' : 'Show hotspot candidate review'}
    >
      <div className="panel hotspot-panel">
        <h3>Candidate hotspots</h3>
        <p className="muted">
          A candidate is a <b>location for a person to look at</b>. It is not a PM2.5 value, not
          a confirmed fire, and it does not attribute a source.
        </p>

        {loading && <p className="muted">Reading the detector's recorded scans…</p>}

        {failed && (
          <div className="hotspot-unavailable" role="status">
            <p className="hotspot-unavailable-title">Hotspot detector unavailable</p>
            <p className="muted">
              The hotspot endpoint could not be read, so this dashboard cannot say whether the
              detector ran. That is not the same as the detector having found nothing.
            </p>
            <p className="hotspot-unavailable-error">{index.message}</p>
          </div>
        )}

        {scanError !== null && (
          <div className="hotspot-unavailable" role="alert">
            <p className="hotspot-unavailable-title">That scan could not be read</p>
            <p className="hotspot-unavailable-error">{scanError}</p>
          </div>
        )}

        {indexData && (
          <>
            <p className="hotspot-caveat">
              Detector <b>{indexData.detector_version}</b>. {indexData.trigger}
            </p>

            <h4 className="hotspot-subhead">Recorded scans</h4>
            <ul className="hotspot-list">
              {rows.map((row) => {
                const loaded = scans[row.scan_id]
                const insufficient = isInsufficientScan(row)
                return (
                  <li key={row.scan_id}>
                    <button
                      type="button"
                      className={`hotspot-row ${
                        insufficient ? 'hotspot-status-insufficient' : 'hotspot-status-candidate'
                      } ${activeScanId === row.scan_id ? 'hotspot-row-selected' : ''}`}
                      onClick={() => {
                        setSelectedCandidate(null)
                        onSelectScan(row.scan_id)
                      }}
                    >
                      <span className="hotspot-row-status">
                        {insufficient
                          ? 'Insufficient evidence — could not look'
                          : `${row.candidate_count} candidate${row.candidate_count === 1 ? '' : 's'}`}
                      </span>
                      <span className="hotspot-row-meta">
                        {row.case_title || row.case_id}
                      </span>
                      <span className="hotspot-row-id">
                        {row.scan_id}
                        {row.synthetic_input ? ' · synthetic input' : ''}
                      </span>
                    </button>

                    {insufficient && loaded && (
                      <ul className="hotspot-reasons">
                        {loaded.reasons.map((reason) => (
                          <li key={reason}>{reason}</li>
                        ))}
                      </ul>
                    )}
                  </li>
                )
              })}
            </ul>
          </>
        )}

        {activeScan && (
          <>
            <h4 className="hotspot-subhead">
              Scan {activeScan.case_id} — verdict {activeScan.verdict}
            </h4>

            {isInsufficientScan(activeScan) ? (
              <div className="hotspot-unavailable" role="status">
                <p className="hotspot-unavailable-title">The detector could not look</p>
                <ul className="hotspot-reasons">
                  {activeScan.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
                <p className="muted">
                  No candidate can be produced from FIRMS or station data alone: imagery is the
                  trigger. The signals that <i>were</i> supplied were recorded and used for
                  nothing:
                </p>
                <ul className="hotspot-reasons">
                  <li>
                    FIRMS: {activeScan.signal_counts.fires_supplied ?? 0} supplied,{' '}
                    {activeScan.signal_counts.fires_usable ?? 0} usable
                  </li>
                  <li>
                    Stations: {activeScan.signal_counts.stations_supplied ?? 0} supplied,{' '}
                    {activeScan.signal_counts.stations_usable ?? 0} usable
                    {(activeScan.signal_counts.stations_excluded_source ?? 0) > 0 && (
                      <span className="muted">
                        {' '}
                        — {activeScan.signal_counts.stations_excluded_source} excluded by source
                        (synthetic/fixture readings cannot corroborate)
                      </span>
                    )}
                  </li>
                </ul>
              </div>
            ) : activeScan.candidates.length === 0 ? (
              <p className="muted">
                The detector ran and produced no candidate: the imagery was present but no cell
                reached the trigger threshold. That is a finding, not a failure.
              </p>
            ) : (
              <>
                <ul className="hotspot-list">
                  {activeScan.candidates.map((c) => (
                    <li key={c.candidate_id}>
                      <button
                        type="button"
                        className={`hotspot-row ${
                          CONFIDENCE_CLASS[c.confidence.toLowerCase()] ?? 'hotspot-status-unclassifiable'
                        } ${candidate?.candidate_id === c.candidate_id ? 'hotspot-row-selected' : ''}`}
                        onClick={() => setSelectedCandidate(c.candidate_id)}
                      >
                        <span className="hotspot-row-status">{confidencePhrase(c)}</span>
                        <span className="hotspot-row-meta">
                          {c.supporting_sources.join(' + ')} · {c.review_status}
                        </span>
                        <span className="hotspot-row-id">{c.candidate_id}</span>
                      </button>
                    </li>
                  ))}
                </ul>

                <h4 className="hotspot-subhead">How this scan evaluated</h4>
                <p className="muted">{evaluationCaveat(activeScan)}</p>
                <dl className="hotspot-facts">
                  <dt>Labels</dt>
                  <dd>
                    {activeScan.evaluation.label_provenance} · {activeScan.evaluation.labels_total}{' '}
                    ({activeScan.evaluation.labels_positive} positive)
                  </dd>
                  <dt>True positives</dt>
                  <dd>{activeScan.evaluation.true_positives}</dd>
                  <dt>False positives</dt>
                  <dd>
                    {activeScan.evaluation.false_positives}
                    {activeScan.evaluation.false_positive_cells.length > 0 && (
                      <span className="muted">
                        {' '}
                        — {activeScan.evaluation.false_positive_cells.join(', ')}
                      </span>
                    )}
                  </dd>
                  <dt>Missed</dt>
                  <dd>
                    {activeScan.evaluation.false_negatives}
                    {activeScan.evaluation.missed_cells.length > 0 && (
                      <span className="muted"> — {activeScan.evaluation.missed_cells.join(', ')}</span>
                    )}
                  </dd>
                  <dt>Unlabelled</dt>
                  <dd>
                    {activeScan.evaluation.unlabelled_predictions}
                    <span className="muted"> — not scored either way</span>
                  </dd>
                  <dt>Precision</dt>
                  <dd>{metricOrNotComputed(activeScan.evaluation.precision)}</dd>
                  <dt>Recall</dt>
                  <dd>{metricOrNotComputed(activeScan.evaluation.recall)}</dd>
                </dl>
              </>
            )}

            <h4 className="hotspot-subhead">Detector limits, as recorded</h4>
            <ul className="hotspot-gaps">
              {Object.entries(activeScan.limitations).map(([key, text]) => (
                <li key={key}>
                  <b>{key}:</b> {text}
                </li>
              ))}
            </ul>
          </>
        )}

        {candidate && (
          <>
            <h4 className="hotspot-subhead">Candidate {candidate.candidate_id}</h4>
            <p className="hotspot-caveat">{reviewPhrase(candidate)}</p>
            <dl className="hotspot-facts">
              <dt>Location</dt>
              <dd className="hotspot-mono">
                {candidate.latitude.toFixed(4)}, {candidate.longitude.toFixed(4)} ·{' '}
                {candidate.h3_cell}
              </dd>
              <dt>Acquired</dt>
              <dd>{when(candidate.acquired_at)}</dd>
              <dt>Detector</dt>
              <dd>{candidate.detector_version}</dd>
              <dt>Confidence</dt>
              <dd>
                {confidencePhrase(candidate)}
                <ul className="hotspot-basis">
                  {candidate.confidence_basis.map((term) => (
                    <li key={term}>{term}</li>
                  ))}
                </ul>
              </dd>
              <dt>Imagery index</dt>
              <dd>{candidate.index_value.toFixed(2)}</dd>
              <dt>PM2.5</dt>
              <dd>
                not reported <span className="muted">— a candidate carries no concentration</span>
              </dd>
              <dt>Attribution</dt>
              <dd>{candidate.source_attribution}</dd>
            </dl>

            <h4 className="hotspot-subhead">Supporting evidence</h4>
            <ul className="hotspot-evidence">
              {candidate.evidence.map((item) => (
                <li key={`${item.source}-${item.observed_at}-${item.detail}`}>
                  <b>{item.source}</b> · {when(item.observed_at)}
                  <span className="muted"> — {item.detail}</span>
                </li>
              ))}
            </ul>
            <p className="muted hotspot-note">{candidate.notes}</p>
          </>
        )}
      </div>
    </SidePanel>
  )
}
