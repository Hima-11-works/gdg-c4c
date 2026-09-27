import { useEffect, useMemo, useState } from 'react'
import { useMapUi } from '../state/MapUiContext'
import {
  EVIDENCE_GAPS,
  candidateStatusLabel,
  candidateTotals,
  candidatesUnavailable,
  hotspotCandidateFromRow,
} from '../lib/hotspotCandidates'
import type { CandidatesUnavailableReason, HotspotCandidate } from '../lib/hotspotCandidates'
import type { ActiveFire } from '../lib/activeFires'
import type { AsyncResource } from '../hooks/useApiResource'
import { SidePanel } from './SidePanel'

function when(iso: string, valid: boolean): string {
  if (!valid) return 'unreadable timestamp'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'unreadable timestamp'
  // A satellite overpass is a UTC instant and is reported as one. Rendering it
  // in the browser's local zone without saying so would quietly shift the time
  // by up to a day, which for a day/night overpass is not a cosmetic problem.
  return `${date.toLocaleString('en-GB', { timeZone: 'UTC' })} UTC`
}

const STATUS_CLASS: Record<string, string> = {
  candidate: 'hotspot-status-candidate',
  rejected: 'hotspot-status-rejected',
  unclassifiable: 'hotspot-status-unclassifiable',
}

/**
 * The evidence behind one imagery-derived candidate, and what is missing from it.
 *
 * The panel is built around the same refusal as the map: a candidate is one
 * satellite thermal detection the detector flagged, not a fire. So the status
 * line comes from the detector's own confidence, the evidence below it is the
 * literal record, and the three fields the API does not carry are printed as
 * named gaps rather than left blank or filled with something plausible.
 */
export function HotspotEvidencePanel({ resource }: { resource: AsyncResource<ActiveFire[]> }) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { state, dispatch } = useMapUi()

  const candidates = useMemo(
    () => (resource.status === 'success' ? resource.data.map(hotspotCandidateFromRow) : []),
    [resource],
  )
  const totals = useMemo(() => candidateTotals(candidates), [candidates])

  // A selection from the map is keyed by detection_id, so a popup and this
  // panel always describe the same record.
  useEffect(() => {
    const onSelected = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail
      const id = detail?.detection_id
      if (typeof id === 'string') setSelectedId(id)
    }
    window.addEventListener('air-health:hotspot-candidate-selected', onSelected)
    return () => window.removeEventListener('air-health:hotspot-candidate-selected', onSelected)
  }, [])

  const selected: HotspotCandidate | null =
    candidates.find((candidate) => candidate.detectionId === selectedId) ?? null

  const reason: CandidatesUnavailableReason | null =
    resource.status === 'error'
      ? 'unreachable'
      : resource.status === 'success' && candidates.length === 0
        ? 'no-detections'
        : resource.status === 'success' && totals.candidates === 0 && totals.rejected > 0
          ? 'none-passing'
          : null

  return (
    <SidePanel
      id="hotspot-evidence-panel"
      side="left"
      open={state.hotspotPanelOpen}
      onToggle={() => dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })}
      label={
        state.hotspotPanelOpen ? 'Hide fire candidate evidence' : 'Show fire candidate evidence'
      }
    >
      <div className="panel hotspot-panel">
        <h3>Fire candidate evidence</h3>
        <p className="muted">
          Imagery-derived candidates, triaged from NASA FIRMS thermal detections. This is not
          measured PM2.5, and not a confirmed fire.
        </p>
        {resource.status === 'loading' || resource.status === 'idle' ? (
          <p className="muted">Reading the satellite detection feed…</p>
        ) : null}

        {reason !== null && (
          <div className="hotspot-unavailable" role="status">
            <p className="hotspot-unavailable-title">
              {reason === 'unreachable' ? 'Detection feed unavailable' : 'No candidates to show'}
            </p>
            <p className="muted">{candidatesUnavailable(reason, totals)}</p>
            {resource.status === 'error' && (
              <p className="hotspot-unavailable-error">{resource.message}</p>
            )}
          </div>
        )}

        {resource.status === 'success' && candidates.length > 0 && (
          <>
            <p className="muted hotspot-tally">
              {totals.detected} detection{totals.detected === 1 ? '' : 's'} returned ·{' '}
              <b>{totals.candidates}</b> put forward as candidate
              {totals.candidates === 1 ? '' : 's'}
              {totals.rejected > 0 && ` · ${totals.rejected} below threshold`}
              {totals.unclassifiable > 0 && ` · ${totals.unclassifiable} not classifiable`}
            </p>

            <p className="hotspot-caveat">
              A candidate is <b>not a confirmed fire</b>. It is one satellite thermal detection the
              detector was reasonably sure about, waiting for a person to look.
            </p>

            <h4 className="hotspot-subhead">Detections</h4>
            <ul className="hotspot-list">
              {candidates.map((candidate) => (
                <li key={candidate.detectionId}>
                  <button
                    type="button"
                    className={`hotspot-row ${STATUS_CLASS[candidate.status]} ${
                      selected?.detectionId === candidate.detectionId ? 'hotspot-row-selected' : ''
                    }`}
                    onClick={() => setSelectedId(candidate.detectionId)}
                  >
                    <span className="hotspot-row-status">
                      {candidateStatusLabel(candidate.status)}
                    </span>
                    <span className="hotspot-row-meta">
                      {candidate.confidenceClass} confidence · {candidate.satellite} ·{' '}
                      {when(candidate.acquiredAt, candidate.acquiredAtValid)}
                    </span>
                    <span className="hotspot-row-id">{candidate.detectionId}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        {selected !== null && (
          <>
            <h4 className="hotspot-subhead">Evidence for {selected.detectionId}</h4>
            <p className={`hotspot-status ${STATUS_CLASS[selected.status]}`}>
              {candidateStatusLabel(selected.status)} — from the detector's own confidence, not a
              human review.
            </p>
            <dl className="hotspot-facts">
              <dt>Acquisition time</dt>
              <dd>{when(selected.acquiredAt, selected.acquiredAtValid)}</dd>

              <dt>Confidence</dt>
              <dd>
                {selected.confidenceClass}{' '}
                <span className="muted">(raw token “{selected.confidenceRaw}”)</span>
              </dd>

              <dt>Satellite</dt>
              <dd>
                {selected.satellite}
                {selected.daynight === 'D'
                  ? ' · day overpass'
                  : selected.daynight === 'N'
                    ? ' · night overpass'
                    : ' · overpass time of day not reported'}
              </dd>

              <dt>Fire Radiative Power</dt>
              <dd>
                {selected.frpMw === null ? 'not reported' : `${selected.frpMw.toFixed(1)} MW`}
              </dd>

              <dt>Brightness (4 µm)</dt>
              <dd>
                {selected.brightnessTi4K === null
                  ? 'not reported'
                  : `${selected.brightnessTi4K.toFixed(1)} K`}
              </dd>

              <dt>Snapped cell</dt>
              <dd className="hotspot-mono">{selected.h3Cell}</dd>

              <dt>Position</dt>
              <dd className="hotspot-mono">
                {selected.latitude.toFixed(4)}, {selected.longitude.toFixed(4)}
              </dd>
            </dl>

            <h4 className="hotspot-subhead">What the API does not report</h4>
            <ul className="hotspot-gaps">
              {EVIDENCE_GAPS.map((gap) => (
                <li key={gap.field}>
                  <b>{gap.field}:</b> {gap.gap}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </SidePanel>
  )
}
