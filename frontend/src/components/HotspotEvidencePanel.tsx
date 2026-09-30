import { useEffect, useMemo, useRef, useState } from 'react'
import { useApiResource } from '../hooks/useApiResource'
import {
  fetchHotspotCatalog,
  fetchHotspotEvents,
  fetchHotspotScan,
  reviewHotspotEvent,
} from '../lib/api'
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
import type { HotspotEventOut, HotspotScanOut, HotspotScanSummaryOut } from '../lib/types'
import type { GridStateOut } from '../lib/types'
import type { LocalPollutionHotspot } from '../lib/localHotspots'
import type { AsyncResource } from '../hooks/useApiResource'

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
const EMPTY_EVENTS: HotspotEventOut[] = []

/**
 * The evidence behind one imagery-derived candidate, and what is missing from it.
 *
 * The panel is built around the same refusal as the map: a candidate is one
 * satellite thermal detection the detector flagged, not a fire. So the status
 * line comes from the detector's own confidence, the evidence below it is the
 * literal record, and the three fields the API does not carry are printed as
 * named gaps rather than left blank or filled with something plausible.
 */
export function HotspotEvidencePanel({
  resource,
  localHotspots,
  gridResource,
  resolutionLevel,
  forecastMinutes,
  selectedDetectionId,
  onSelectDetection,
}: {
  resource: AsyncResource<ActiveFire[]>
  localHotspots: LocalPollutionHotspot[]
  gridResource: AsyncResource<GridStateOut[]>
  resolutionLevel: number
  forecastMinutes: number
  selectedDetectionId: string | null
  onSelectDetection: (detectionId: string) => void
}) {
  const [selectedScanId, setSelectedScanId] = useState<string | null>(null)
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null)
  const [simulatorKey, setSimulatorKey] = useState('')
  const [actorId, setActorId] = useState('')
  const [reviewBusy, setReviewBusy] = useState(false)
  const [reviewError, setReviewError] = useState<string | null>(null)
  const [reviewNotice, setReviewNotice] = useState<string | null>(null)
  const { state, dispatch } = useMapUi()
  const panelRef = useRef<HTMLElement>(null)
  const scanCatalog = useApiResource(fetchHotspotCatalog, [], { pollIntervalMs: 5 * 60 * 1000 })
  const eventCatalog = useApiResource(fetchHotspotEvents, [], { pollIntervalMs: 5 * 60 * 1000 })

  const candidates = useMemo(
    () => (resource.status === 'success' ? resource.data.map(hotspotCandidateFromRow) : []),
    [resource],
  )
  const totals = useMemo(() => candidateTotals(candidates), [candidates])

  const selected: HotspotCandidate | null =
    candidates.find((candidate) => candidate.detectionId === selectedDetectionId) ?? null

  const scans = scanCatalog.resource.status === 'success' ? scanCatalog.resource.data.scans : []
  const events =
    eventCatalog.resource.status === 'success' ? eventCatalog.resource.data.events : EMPTY_EVENTS
  const orderedEvents = useMemo(
    () =>
      [...events].sort(
        (a, b) =>
          Number(a.status !== 'potential') - Number(b.status !== 'potential') ||
          b.confidence_score - a.confidence_score ||
          b.time_window.from.localeCompare(a.time_window.from),
      ),
    [events],
  )
  const selectedEvent =
    orderedEvents.find((event) => event.event_id === selectedEventId) ?? orderedEvents[0] ?? null
  const selectedScanSummary =
    scans.find((scan) => scan.scan_id === selectedScanId) ?? scans[0] ?? null
  const scanDetails = useApiResource(
    (signal) => fetchHotspotScan(selectedScanSummary?.scan_id ?? '', signal),
    [selectedScanSummary?.scan_id],
    { enabled: selectedScanSummary !== null },
  )

  const showEventOnMap = (cell: string) => {
    dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })
    dispatch({ type: 'FOCUS_CELL', cell })
  }

  const reviewEvent = async (reviewState: 'confirmed' | 'dismissed') => {
    if (selectedEvent === null || selectedEvent.synthetic || reviewBusy) return
    setReviewBusy(true)
    setReviewError(null)
    try {
      await reviewHotspotEvent(selectedEvent.event_id, reviewState, { simulatorKey, actorId })
      setSimulatorKey('')
      setActorId('')
      setReviewNotice(
        `Event ${reviewState}. The decision is saved; this does not dispatch or notify responders.`,
      )
      eventCatalog.refetch()
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : 'Could not save the event review.')
    } finally {
      setReviewBusy(false)
    }
  }

  const reason: CandidatesUnavailableReason | null =
    resource.status === 'error'
      ? 'unreachable'
      : resource.status === 'success' && candidates.length === 0
        ? 'no-detections'
        : resource.status === 'success' && totals.candidates === 0 && totals.rejected > 0
          ? 'none-passing'
          : null

  useEffect(() => {
    const panel = panelRef.current
    if (panel === null) return
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusableSelector =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    const focusableItems = () =>
      Array.from(panel.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (item) => item.getClientRects().length > 0 && item.getAttribute('aria-hidden') !== 'true',
      )

    const first = focusableItems()[0]
    if (first) first.focus()
    else panel.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })
        return
      }
      if (event.key !== 'Tab') return

      const items = focusableItems()
      const firstItem = items[0]
      const lastItem = items.at(-1)
      if (firstItem === undefined || lastItem === undefined) {
        event.preventDefault()
        panel.focus()
      } else if (
        event.shiftKey &&
        (document.activeElement === firstItem || !panel.contains(document.activeElement))
      ) {
        event.preventDefault()
        lastItem.focus()
      } else if (
        !event.shiftKey &&
        (document.activeElement === lastItem || !panel.contains(document.activeElement))
      ) {
        event.preventDefault()
        firstItem.focus()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [dispatch])

  if (!state.hotspotPanelOpen) return null

  return (
    <div
      className="hotspot-panel-container"
      id="hotspot-evidence-panel"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })
      }}
    >
      <section
        className="panel hotspot-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="hotspot-evidence-title"
        aria-describedby="hotspot-evidence-description"
        tabIndex={-1}
        ref={panelRef}
      >
        <div className="hotspot-panel-header">
          <h3 id="hotspot-evidence-title">Potential hotspot triage</h3>
          <button
            type="button"
            className="cell-detail-close"
            onClick={() => dispatch({ type: 'TOGGLE_HOTSPOT_PANEL' })}
            aria-label="Close hotspot triage"
            title="Close"
          >
            ✕
          </button>
        </div>
        <p className="muted" id="hotspot-evidence-description">
          Review satellite and local air-quality signals as potential events. A candidate is not a
          confirmed fire, pollution source, or PM2.5 measurement.
        </p>

        <h4 className="hotspot-subhead">Potential hotspot events</h4>
        <p className="muted">
          New events appear first, followed by reviewed events. Detector scores prioritize human
          review; they are not probabilities and do not identify a source.
        </p>
        {(eventCatalog.resource.status === 'loading' ||
          eventCatalog.resource.status === 'idle') && (
          <p className="muted">Reading persisted hotspot events…</p>
        )}
        {eventCatalog.resource.status === 'error' && (
          <div className="hotspot-unavailable" role="status">
            <p className="hotspot-unavailable-title">Hotspot event catalog unavailable</p>
            <p className="hotspot-unavailable-error">{eventCatalog.resource.message}</p>
          </div>
        )}
        {eventCatalog.resource.status === 'success' && events.length === 0 && (
          <p className="muted">
            No events are recorded. New qualifying satellite scans create potential events when the
            live pipeline is configured.
          </p>
        )}
        {orderedEvents.length > 0 && (
          <>
            <ul className="hotspot-list">
              {orderedEvents.slice(0, 12).map((event) => (
                <li className="hotspot-scan-candidate" key={event.event_id}>
                  <button
                    type="button"
                    className={`hotspot-row ${selectedEvent?.event_id === event.event_id ? 'hotspot-row-selected' : ''}`}
                    aria-pressed={selectedEvent?.event_id === event.event_id}
                    onClick={() => {
                      setSelectedEventId(event.event_id)
                      setReviewError(null)
                      setReviewNotice(null)
                    }}
                  >
                    <span className="hotspot-row-header">
                      <span className="hotspot-row-status">
                        {event.status === 'potential'
                          ? 'Awaiting review'
                          : event.status.replaceAll('_', ' ')}
                      </span>
                      <span className="hotspot-source-tag hotspot-source-unknown">
                        {event.synthetic ? 'DEMO' : `${event.confidence_band} triage`}
                      </span>
                    </span>
                    <span className="hotspot-row-meta">
                      {event.region} · {event.footprint_cells.length} cell(s) ·{' '}
                      {when(event.time_window.from, true)}
                    </span>
                    <span className="hotspot-row-id">{event.dedup_cell}</span>
                  </button>
                </li>
              ))}
            </ul>
            {selectedEvent !== null && (
              <section className="hotspot-event-detail" aria-label="Selected hotspot evidence">
                <div className="hotspot-row-header">
                  <strong>
                    {selectedEvent.status === 'potential'
                      ? 'Unreviewed potential event'
                      : `Event ${selectedEvent.status}`}
                  </strong>
                  {selectedEvent.synthetic && <span className="hotspot-row-meta">Demo data</span>}
                </div>
                <p className="muted">{selectedEvent.severity_basis}</p>
                <p className="hotspot-row-meta">
                  Observed {when(selectedEvent.time_window.from, true)} –{' '}
                  {when(selectedEvent.time_window.to, true)}
                  {' · '}confidence {Math.round(selectedEvent.confidence_score * 100)}% (
                  {selectedEvent.confidence_band} triage, not probability)
                </p>
                <p className="hotspot-row-meta">
                  {selectedEvent.footprint_cells.length} cell(s) · {selectedEvent.scan_ids.length}{' '}
                  scan(s) · {selectedEvent.evidence.length} evidence link(s)
                </p>
                <button
                  type="button"
                  className="btn-secondary hotspot-focus-button"
                  onClick={() =>
                    showEventOnMap(selectedEvent.dedup_cell || selectedEvent.footprint_cells[0])
                  }
                >
                  Show event cell on map
                </button>
                <ul className="hotspot-event-evidence">
                  {selectedEvent.evidence.map((item, index) => (
                    <li key={`${item.scan_id}-${item.ref}-${index}`}>
                      <strong>{item.source.replaceAll('_', ' ')}</strong> ·{' '}
                      {when(item.observed_at, true)}
                      {item.available_at && (
                        <span className="hotspot-row-meta">
                          Available {when(item.available_at, true)}
                        </span>
                      )}
                      <span>{item.detail}</span>
                    </li>
                  ))}
                </ul>
                {selectedEvent.status === 'potential' && !selectedEvent.synthetic && (
                  <div className="hotspot-review-form">
                    <p className="muted">
                      Enter a registered pollution-control responder. Credentials are sent to the
                      backend, held in memory while this dialog is open, and never saved in the
                      browser.
                    </p>
                    <label>
                      Simulator API key
                      <input
                        type="password"
                        autoComplete="off"
                        value={simulatorKey}
                        onChange={(event) => setSimulatorKey(event.target.value)}
                      />
                    </label>
                    <label>
                      Responder actor ID
                      <input
                        type="text"
                        autoComplete="off"
                        value={actorId}
                        onChange={(event) => setActorId(event.target.value)}
                      />
                    </label>
                    {reviewError && (
                      <p className="hotspot-unavailable-error" role="alert">
                        {reviewError}
                      </p>
                    )}
                    <div className="hotspot-review-actions">
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={reviewBusy || !simulatorKey.trim() || !actorId.trim()}
                        onClick={() => void reviewEvent('confirmed')}
                      >
                        {reviewBusy ? 'Saving…' : 'Confirm event'}
                      </button>
                      <button
                        type="button"
                        className="btn-secondary hotspot-dismiss-button"
                        disabled={reviewBusy || !simulatorKey.trim() || !actorId.trim()}
                        onClick={() => void reviewEvent('dismissed')}
                      >
                        Dismiss event
                      </button>
                    </div>
                  </div>
                )}
                {reviewNotice && (
                  <p className="hotspot-review-notice" role="status">
                    {reviewNotice}
                  </p>
                )}
                {selectedEvent.synthetic && (
                  <p className="hotspot-caveat">
                    Authored demo events cannot be reviewed as real incidents.
                  </p>
                )}
              </section>
            )}
          </>
        )}

        <h4 className="hotspot-subhead">Local PM2.5 outlier candidates</h4>
        <p className="muted">
          A spatial triage of the current fine-resolution PM2.5 grid. It points to an unusual local
          rise; it does not identify the emission source.
        </p>
        <div className="hotspot-caveat local-hotspot-rule">
          Candidate rule: PM2.5 at least 60 µg/m³, at least 25 µg/m³ and 1.5× above the nearby-cell
          median, with at least three neighbors. Live data also needs a contributing station reading
          no older than six hours. These are screening cutoffs, not health thresholds. Demo results
          are labeled and are not measurements.
        </div>
        {resolutionLevel < 3 ? (
          <p className="muted">Zoom to Resolution 3 or closer to check local cells.</p>
        ) : forecastMinutes !== 0 ? (
          <p className="muted">Switch the timeline to Now to review observed-backed candidates.</p>
        ) : gridResource.status === 'idle' || gridResource.status === 'loading' ? (
          <p className="muted">Loading the current local PM2.5 grid…</p>
        ) : gridResource.status === 'error' ? (
          <p className="hotspot-unavailable-error">
            The local grid is unavailable: {gridResource.message}
          </p>
        ) : localHotspots.length === 0 ? (
          <p className="muted">No cells in this view pass the local-outlier and evidence checks.</p>
        ) : (
          <ul className="hotspot-list">
            {localHotspots.slice(0, 10).map((hotspot) => (
              <li key={hotspot.h3Cell}>
                <button
                  type="button"
                  className={`hotspot-row ${
                    state.selectedCell === hotspot.h3Cell ? 'hotspot-row-selected' : ''
                  }`}
                  onClick={() => showEventOnMap(hotspot.h3Cell)}
                >
                  <span className="hotspot-row-status">Possible local PM2.5 anomaly</span>
                  <span className="hotspot-row-meta">
                    {hotspot.pm25.toFixed(0)} µg/m³ vs nearby median{' '}
                    {hotspot.nearbyMedian.toFixed(0)} (+{hotspot.delta.toFixed(0)})
                  </span>
                  <span className="hotspot-row-meta">
                    {hotspot.nearbyCellCount} nearby cells ·{' '}
                    {hotspot.isDemo
                      ? 'illustrative demo grid'
                      : `${hotspot.stationCount} station(s) · observations ${
                          hotspot.observationAgeHours === null
                            ? 'freshness unavailable'
                            : `${hotspot.observationAgeHours.toFixed(1)}h old`
                        }`}
                  </span>
                  <span className="hotspot-row-id">{hotspot.h3Cell}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <details className="hotspot-diagnostics">
          <summary>Satellite scan diagnostics</summary>
          {(scanCatalog.resource.status === 'loading' ||
            scanCatalog.resource.status === 'idle') && (
            <p className="muted">Reading recorded detector scans…</p>
          )}
          {scanCatalog.resource.status === 'error' && (
            <div className="hotspot-unavailable" role="status">
              <p className="hotspot-unavailable-title">Scan catalog unavailable</p>
              <p className="muted">
                The dashboard cannot tell whether any imagery scans were recorded.
              </p>
              <p className="hotspot-unavailable-error">{scanCatalog.resource.message}</p>
            </div>
          )}
          {scanCatalog.resource.status === 'success' && scans.length === 0 && (
            <p className="muted">
              No imagery scan is recorded yet. Configure CDSE_REFRESH_TOKEN and run the backend
              pipeline to scan new Sentinel-5P satellite swaths. Demo mode skips live imagery;
              shipped fixtures are authored examples, not observations.
            </p>
          )}
          {scans.length > 0 && (
            <>
              <ul className="hotspot-list">
                {scans.map((scan) => (
                  <li key={scan.scan_id}>
                    <button
                      type="button"
                      className={`hotspot-row ${
                        selectedScanSummary?.scan_id === scan.scan_id ? 'hotspot-row-selected' : ''
                      }`}
                      onClick={() => setSelectedScanId(scan.scan_id)}
                    >
                      <span className="hotspot-row-status">
                        {scan.verdict.replaceAll('_', ' ')}
                      </span>
                      <span className="hotspot-row-meta">
                        {scan.case_title} · {scan.candidate_count} candidate
                        {scan.candidate_count === 1 ? '' : 's'}
                        {scan.synthetic_input ? ' · authored fixture' : ''}
                      </span>
                      <span className="hotspot-row-id">{scan.scan_id}</span>
                    </button>
                  </li>
                ))}
              </ul>
              {selectedScanSummary !== null && (
                <RecordedScanDetails
                  summary={selectedScanSummary}
                  resource={scanDetails.resource}
                />
              )}
            </>
          )}
        </details>
        <details className="hotspot-diagnostics">
          <summary>Raw FIRMS fire detections</summary>
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
                A candidate is <b>not a confirmed fire</b>. It is one satellite thermal detection
                the detector was reasonably sure about, waiting for a person to look. The source is
                unconfirmed; this feed cannot distinguish agricultural, industrial, or urban
                sources.
              </p>

              <h4 className="hotspot-subhead">Detections</h4>
              <ul className="hotspot-list">
                {candidates.map((candidate) => (
                  <li key={candidate.detectionId}>
                    <button
                      type="button"
                      className={`hotspot-row ${STATUS_CLASS[candidate.status]} ${
                        selected?.detectionId === candidate.detectionId
                          ? 'hotspot-row-selected'
                          : ''
                      }`}
                      onClick={() => onSelectDetection(candidate.detectionId)}
                    >
                      <div className="hotspot-row-header">
                        <span className="hotspot-row-status">
                          {candidateStatusLabel(candidate.status)}
                        </span>
                        <span className={`hotspot-source-tag ${candidate.sourceInfo.badgeClass}`}>
                          {candidate.sourceInfo.icon} {candidate.sourceInfo.label}
                        </span>
                      </div>
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
              <div className={`hotspot-source-banner ${selected.sourceInfo.badgeClass}`}>
                <span className="hotspot-source-banner-icon">{selected.sourceInfo.icon}</span>
                <div>
                  <strong className="hotspot-source-banner-title">
                    {selected.sourceInfo.label}
                  </strong>
                  <p className="hotspot-source-banner-desc">{selected.sourceInfo.description}</p>
                </div>
              </div>
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
        </details>
      </section>
    </div>
  )
}

function RecordedScanDetails({
  summary,
  resource,
}: {
  summary: HotspotScanSummaryOut
  resource: AsyncResource<HotspotScanOut>
}) {
  if (resource.status === 'idle' || resource.status === 'loading') {
    return <p className="muted">Loading scan evidence…</p>
  }
  if (resource.status === 'error') {
    return (
      <p className="hotspot-unavailable-error">Scan evidence unavailable: {resource.message}</p>
    )
  }

  const scan = resource.data
  const uvaiTrigger = scan.config.sentinel5p_uvai_threshold_raw
  const uvaiStrong = scan.config.sentinel5p_uvai_strong_threshold_raw
  const qualityMinimum = scan.config.sentinel5p_quality_min_exclusive
  return (
    <div className="hotspot-scan-details">
      <p className="muted">
        {scan.detector_version} · evaluated {when(scan.evaluated_at, true)}
        {scan.imagery?.synthetic ? ' · authored imagery fixture' : ''}
      </p>
      {scan.imagery !== null && (
        <div className="hotspot-row-meta">
          <b>{scan.imagery.source}</b> · {scan.imagery.product} · version{' '}
          {scan.imagery.product_version}
          <div>
            Acquired {when(scan.imagery.acquired_at, true)} · available{' '}
            {when(scan.imagery.available_at, true)} · H3 resolution {scan.imagery.h3_resolution}
          </div>
          <div>{scan.imagery.index_name}</div>
          {typeof uvaiTrigger === 'number' && typeof uvaiStrong === 'number' && (
            <div>
              Screening thresholds: UVAI ≥ {uvaiTrigger.toFixed(1)}; strong ≥{' '}
              {uvaiStrong.toFixed(1)}
              {typeof qualityMinimum === 'number' &&
                ` · pixel quality > ${qualityMinimum.toFixed(2)}`}
            </div>
          )}
          <div className="muted">{scan.imagery.notes}</div>
        </div>
      )}
      {summary.synthetic_input && (
        <p className="hotspot-caveat">
          Fixture score only: {summary.false_positives} false positive(s), {summary.false_negatives}{' '}
          missed label(s). This does not establish real-world accuracy.
        </p>
      )}
      {scan.candidates.length === 0 ? (
        <>
          <p className="muted">This scan returned no candidate locations ({scan.verdict}).</p>
          {scan.reasons.map((reason) => (
            <p className="muted" key={reason}>
              {reason}
            </p>
          ))}
        </>
      ) : (
        <ul className="hotspot-list">
          {scan.candidates.map((candidate) => (
            <li className="hotspot-scan-candidate" key={candidate.candidate_id}>
              <b>{candidate.review_status.replaceAll('_', ' ')}</b> · {candidate.confidence} triage
              score {candidate.confidence_score.toFixed(2)} (not a probability)
              <div className="hotspot-row-meta">
                {candidate.supporting_sources.join(', ')} · {when(candidate.acquired_at, true)}
              </div>
              <div className="hotspot-mono">
                {candidate.latitude.toFixed(4)}, {candidate.longitude.toFixed(4)} · cell{' '}
                {candidate.h3_cell}
              </div>
              <div className="muted">Candidate location for human review; no PM2.5 value.</div>
              {candidate.evidence.map((evidence, index) => (
                <div className="muted" key={`${evidence.source}-${evidence.observed_at}-${index}`}>
                  {evidence.source}: {evidence.detail}
                </div>
              ))}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
