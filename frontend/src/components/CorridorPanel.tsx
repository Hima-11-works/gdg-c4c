import { useState } from 'react'
import { ApiError, fetchCorridorEvent, fetchCorridors } from '../lib/api'
import {
  GEOMETRY_LABEL,
  coveragePhrase,
  geographyPhrase,
  hasQuotableMetrics,
  horizonLabel,
  isIllustrativeGeometry,
  labelProvenanceLabel,
  peakPhrase,
  quotableSlices,
  unquotableSlices,
  verdictExplanation,
  verdictLabel,
} from '../lib/corridors'
import { useApiResource } from '../hooks/useApiResource'
import { SidePanel } from './SidePanel'
import { useMapUi } from '../state/MapUiContext'
import type { CorridorCatalogEntry, CorridorEventBundle, CorridorSlice } from '../lib/types'

/** The three kinds of thing this view shows, labelled on screen. */
const PROVENANCE = {
  evidence: 'Measured evidence',
  prediction: 'Model prediction',
  illustrative: 'Illustrative geometry',
} as const

function when(iso: string | null | undefined): string {
  if (iso === null || iso === undefined || iso === '') return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

function percent(value: number | null): string {
  if (value === null) return '—'
  return `${(value * 100).toFixed(0)}%`
}

function fixed(value: number | null, unit = ''): string {
  if (value === null) return '—'
  return `${value.toFixed(1)}${unit}`
}

/** One horizon × geography row, shown only when the backend says it may be. */
function SliceTable({ slices }: { slices: CorridorSlice[] }) {
  return (
    <table className="corridor-slices">
      <thead>
        <tr>
          <th>Horizon</th>
          <th>Area</th>
          <th>Pairs</th>
          <th>Stations</th>
          <th>MAE</th>
          <th>RMSE</th>
          <th>Bias</th>
          <th>Recall</th>
          <th>Precision</th>
        </tr>
      </thead>
      <tbody>
        {slices.map((slice) => (
          <tr key={`${slice.horizon_hours}-${slice.geography}`}>
            <td>{horizonLabel(slice.horizon_hours)}</td>
            <td>{slice.geography.replace('_', ' ')}</td>
            <td>{slice.pairs}</td>
            <td>{slice.station_count}</td>
            <td>{fixed(slice.mae_ugm3, ' µg/m³')}</td>
            <td>{fixed(slice.rmse_ugm3, ' µg/m³')}</td>
            <td>{fixed(slice.bias_ugm3, ' µg/m³')}</td>
            <td>{percent(slice.high_pollution_recall)}</td>
            <td>{percent(slice.high_pollution_precision)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * The corridor event view.
 *
 * ## What it is for
 *
 * A corridor event is a *forecast* over a named geography plus an *evaluation*
 * of that forecast against real station observations the forecaster could not
 * have seen. Those are three different things and this panel never merges them:
 *
 * - the **geometry** is illustrative (a straight line between two published
 *   city coordinates, not a road route) and says so, repeatedly;
 * - the **peak** and every forecast number are model predictions;
 * - the only measured evidence in the whole view is the station observations,
 *   and their count is always shown beside the metric.
 *
 * ## The one conclusion it must never blur
 *
 * `insufficient_data` is a *result*. The service looked for real observations in
 * the target windows and found none, so it says so and returns null metrics. The
 * panel shows that conclusion plainly and shows **no** metric at all — a zero
 * recall computed from no observations would be a fabricated performance claim,
 * which is precisely what the contract forbids.
 */
export function CorridorPanel({
  runId,
  onSelect,
  onEventLoaded,
}: {
  /** The published run the rest of the dashboard is showing. The event lookup is
   *  pinned to it, so this panel cannot display an event scored over another
   *  publication. */
  runId?: string
  onSelect: (corridor: CorridorCatalogEntry | null) => void
  /** Hands the loaded bundle up so the map can draw the event's own cells. */
  onEventLoaded: (bundle: CorridorEventBundle | null) => void
}) {
  const { state, dispatch } = useMapUi()
  const catalog = useApiResource(() => fetchCorridors(), [], {})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [eventId, setEventId] = useState('latest')
  const [bundle, setBundle] = useState<CorridorEventBundle | null>(null)
  const [eventError, setEventError] = useState<{ code: string; message: string } | null>(null)
  const [loadingEvent, setLoadingEvent] = useState(false)

  const selected: CorridorCatalogEntry | null =
    catalog.resource.status === 'success'
      ? (catalog.resource.data.find((entry) => entry.corridor_id === selectedId) ??
        catalog.resource.data[0] ??
        null)
      : null

  const loadEvent = async (corridor: CorridorCatalogEntry, id: string) => {
    if (id.trim() === '') return
    setLoadingEvent(true)
    setEventError(null)
    setBundle(null)
    try {
      const envelope = await fetchCorridorEvent(corridor.corridor_id, id.trim(), { runId })
      setBundle(envelope.data)
      // The map is only handed the event's cells when the event belongs to the
      // publication on screen. A mismatched event is still *shown* — with a
      // loud warning — but drawing its cells over the current run's map would
      // put two publications on one screen, which is the thing this whole view
      // is meant to avoid.
      onEventLoaded(
        runId === undefined || envelope.data.event.run_id === runId ? envelope.data : null,
      )
    } catch (error) {
      // The map must not keep drawing cells for an event the panel has just
      // said does not exist, so a failure clears them too.
      onEventLoaded(null)
      if (error instanceof ApiError) {
        setEventError({ code: error.code, message: error.message })
      } else {
        setEventError({ code: 'unknown', message: 'Could not read the corridor event.' })
      }
    } finally {
      setLoadingEvent(false)
    }
  }

  const event = bundle?.event ?? null
  const evaluation = bundle?.evaluation ?? null
  // An event from a different publication than the one on screen. The service
  // pins lookups to `runId`, so this should not happen — but a stale id typed
  // into the box, or a service that resolved a different run, must not produce
  // a screen where the map and the detail describe two publications.
  const runMismatch = event !== null && runId !== undefined && event.run_id !== runId

  return (
    <SidePanel
      id="corridor-panel"
      // Left, not right: the run panel already owns the right, and two panels on
      // the same side stack on top of each other. This one is opened from the
      // top-left control and belongs beside the legend it explains.
      side="left"
      open={state.corridorPanelOpen}
      onToggle={() => dispatch({ type: 'TOGGLE_CORRIDOR_PANEL' })}
      label={state.corridorPanelOpen ? 'Hide corridor event' : 'Show corridor event'}
    >
      <div className="panel corridor-panel">
        <section>
          <h3>Corridor event</h3>
          <p className="muted corridor-intro">
            A forecast over a named geography, and whether it could be scored against real
            station observations the forecaster could not have seen.
          </p>

          {catalog.resource.status === 'loading' && <p className="muted">Loading corridors…</p>}

          {catalog.resource.status === 'error' && (
            <p className="corridor-error" role="alert">
              Could not read the corridor catalog: {catalog.resource.message}{' '}
              <button type="button" onClick={catalog.refetch}>
                Retry
              </button>
            </p>
          )}

          {selected !== null && (
            <>
              <label className="report-field">
                <span>Corridor</span>
                <select
                  value={selected.corridor_id}
                  onChange={(event) => {
                    setSelectedId(event.target.value)
                    onSelect(
                      catalog.resource.status === 'success'
                        ? (catalog.resource.data.find(
                            (entry) => entry.corridor_id === event.target.value,
                          ) ?? null)
                        : null,
                    )
                    setBundle(null)
                    setEventError(null)
                  }}
                >
                  {catalog.resource.status === 'success' &&
                    catalog.resource.data.map((entry) => (
                      <option key={entry.corridor_id} value={entry.corridor_id}>
                        {entry.name}
                      </option>
                    ))}
                </select>
              </label>

              {/* The geometry caveat, in the one place a reader meets the
                  corridor. It is not a footnote: the cells ARE this line. */}
              {isIllustrativeGeometry(selected.geometry_source) && (
                <p className="corridor-geometry-warning" role="note">
                  <strong>{GEOMETRY_LABEL[selected.geometry_source] ?? selected.geometry_source}.</strong>{' '}
                  {selected.geometry_note}
                </p>
              )}

              <dl className="corridor-facts">
                <dt>{PROVENANCE.illustrative}</dt>
                <dd>{geographyPhrase(selected, event)}</dd>
                <dt>Endpoints</dt>
                <dd>
                  {selected.endpoints
                    .map((endpoint) => `${endpoint.label} (${endpoint.latitude}, ${endpoint.longitude})`)
                    .join(' → ')}
                </dd>
              </dl>

              <label className="report-field">
                <span>Event id</span>
                <input
                  className="incident-input"
                  value={eventId}
                  placeholder="corridor:delhi-kanpur:<run_id>:h<digest>"
                  aria-label="Corridor event id"
                  onChange={(event) => setEventId(event.target.value)}
                />
              </label>
              <p className="muted corridor-run-note">
                {runId === undefined
                  ? 'No published run is loaded, so the lookup is unpinned.'
                  : `Lookups are pinned to the run on screen: ${runId}.`}
              </p>
              <div className="incident-note-row">
                <button
                  type="button"
                  className="incident-button incident-button-primary"
                  disabled={loadingEvent || eventId.trim() === ''}
                  onClick={() => loadEvent(selected, eventId)}
                >
                  {loadingEvent ? 'Reading…' : 'Read this event'}
                </button>
                <button type="button" className="incident-button" onClick={() => onSelect(null)}>
                  Clear map selection
                </button>
              </div>

              {eventError !== null && (
                <div className="corridor-error" role="alert">
                  <p>
                    No corridor event to show ({eventError.code}).
                  </p>
                  <p className="muted">{eventError.message}</p>
                  <p className="muted">
                    That is a statement about this publication, not about the corridor: the run on
                    screen has no results there, so there is nothing to evaluate.
                  </p>
                </div>
              )}
            </>
          )}
        </section>

        {event !== null && evaluation !== null && (
          <>
            {runMismatch && (
              <div className="corridor-error corridor-run-mismatch" role="alert">
                <p>
                  <strong>Different publication.</strong> This event is from run{' '}
                  <span className="report-result-cell">{event.run_id}</span>, while the dashboard
                  is showing <span className="report-result-cell">{runId}</span>.
                </p>
                <p className="muted">
                  Nothing below describes the run on screen, and its cells are not drawn on the map.
                  Load an event for {runId} to compare like with like.
                </p>
              </div>
            )}
            <section>
              <h3>Event</h3>
              <dl className="corridor-facts">
                <dt>Event id</dt>
                <dd className="report-result-cell">{event.event_id}</dd>
                <dt>Corridor</dt>
                <dd>
                  {event.corridor_name} ({event.corridor_id})
                </dd>
                <dt>Published run</dt>
                <dd className="report-result-cell">{event.run_id}</dd>
                <dt>Run mode</dt>
                <dd>
                  {event.run_mode}
                  {event.run_synthetic && (
                    <span className="muted"> · contains synthetic results</span>
                  )}
                </dd>
                <dt>Forecast issued</dt>
                <dd>{when(event.issued_at)}</dd>
                <dt>Affected geography</dt>
                <dd>{geographyPhrase(selected as CorridorCatalogEntry, event)}</dd>
              </dl>

              <h4 className="corridor-subhead">Forecast timing</h4>
              <table className="corridor-slices">
                <thead>
                  <tr>
                    <th>Horizon</th>
                    <th>Issued</th>
                    <th>Valid</th>
                  </tr>
                </thead>
                <tbody>
                  {event.horizons.map((horizon) => (
                    <tr key={horizon.horizon_hours}>
                      <td>{horizonLabel(horizon.horizon_hours)}</td>
                      <td>{when(horizon.issued_at)}</td>
                      <td>{when(horizon.valid_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <p className="corridor-provenance corridor-provenance-prediction">
                <strong>{PROVENANCE.prediction}.</strong> Peak {peakPhrase(event)}
              </p>
            </section>

            <section>
              <h3>Source observations</h3>
              <p className="corridor-provenance corridor-provenance-evidence">
                <strong>{PROVENANCE.evidence}.</strong>{' '}
                {event.label_count === 0
                  ? 'None found in the target windows.'
                  : `${event.label_count} observation(s) from ${event.label_sources.join(', ') || 'an unnamed source'}.`}
              </p>
              <p className="muted">
                Provenance: {labelProvenanceLabel(evaluation.label_provenance)}. Labels are read only
                from the target window (valid_at ± 1h), so a label always post-dates the forecast's
                issue time — that is what “withheld” means here.
              </p>
            </section>

            <section>
              <h3>Evaluation</h3>
              <p
                className={`corridor-verdict ${
                  evaluation.verdict === 'evaluated'
                    ? 'corridor-verdict-evaluated'
                    : 'corridor-verdict-insufficient'
                }`}
                role="status"
              >
                <strong>{verdictLabel(evaluation.verdict)}</strong>
                <span className="muted"> · usable as real-world evidence: {String(evaluation.usable_as_real_world_evidence)}</span>
              </p>
              <p className="muted">{verdictExplanation(evaluation)}</p>

              {evaluation.reasons.length > 0 && (
                <ul className="corridor-reasons">
                  {evaluation.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}

              <p className="muted">{coveragePhrase(evaluation)}</p>

              {hasQuotableMetrics(evaluation) ? (
                <>
                  <h4 className="corridor-subhead">Metrics (scored against real observations)</h4>
                  <SliceTable slices={quotableSlices(evaluation)} />
                </>
              ) : (
                <p className="corridor-no-metrics" role="note">
                  <strong>No metric is shown.</strong> Every slice in this evaluation is below the{' '}
                  {evaluation.min_labels}-observation minimum, so each one reports its metrics as
                  null. A number computed from too few observations would describe the sample, not
                  the corridor.
                </p>
              )}

              {unquotableSlices(evaluation).length > 0 && (
                <details className="corridor-insufficient-slices">
                  <summary>
                    {unquotableSlices(evaluation).length} slice(s) with too few observations
                  </summary>
                  <p className="muted">
                    Listed for completeness. Their metrics are null and are deliberately not
                    tabulated.
                  </p>
                  <ul>
                    {unquotableSlices(evaluation).map((slice) => (
                      <li key={`${slice.horizon_hours}-${slice.geography}`}>
                        {horizonLabel(slice.horizon_hours)} · {slice.geography.replace('_', ' ')} —{' '}
                        {slice.pairs} pair(s), {slice.station_count} station(s)
                        {slice.note !== null ? ` · ${slice.note}` : ''}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </section>
          </>
        )}
      </div>
    </SidePanel>
  )
}
