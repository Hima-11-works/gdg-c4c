import { useState } from 'react'
import {
  ApiError,
  assignIncident,
  createIncident,
} from '../lib/api'
import {
  DELIVERY_DISCLAIMER,
  EVENT_LABEL,
  RESPONDER_ROLE_LABEL,
  STATUS_DESCRIPTION,
  STATUS_LABEL,
  actorSummary,
  classifyIncidentError,
  createBodyFor,
  describeEvent,
  explainIncidentRejection,
  findIncidentForSource,
  isTerminalStatus,
  type IncidentFailure,
  type IncidentSource,
} from '../lib/incidents'
import {
  SUGGESTED_ACTOR_IDS,
  actorId,
  setActorOverride,
  writesAreConfigured,
  writesUnavailableReason,
} from '../lib/operatorIdentity'
import { useIncidentDeliveries, useIncidentHistory, useIncidents } from '../hooks/useIncidents'
import type { IncidentOut } from '../lib/types'
import {
  RESPONSE_HANDLED_BY,
  RESPONSE_LABEL,
  RESPONSE_SCOPE,
  RESPONSE_SHORT,
  type ResponseKind,
} from '../lib/responseTypes'

function when(iso: string | null): string {
  if (iso === null) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

const SEVERITY_LABEL = {
  watch: 'Watch',
  warning: 'Warning',
  critical: 'Critical',
} as const

/**
 * A write that failed, shown as exactly what happened.
 *
 * A refusal is not an outage and an outage is not a refusal, so the two are
 * never merged: "rejected" carries the server's code, and "unreachable" says
 * plainly that nothing is known about whether the write took effect.
 */
function WriteFailure({ failure }: { failure: IncidentFailure }) {
  const rejected = failure.kind === 'rejected'
  return (
    <div
      className={`incident-write-failure ${rejected ? 'incident-write-rejected' : 'incident-write-unreachable'}`}
      role="alert"
    >
      <p className="incident-write-failure-title">
        {rejected ? 'Write rejected' : 'Cannot reach the incident service'}
        {rejected && failure.code !== null && (
          <span className="muted incident-write-code">
            {' '}
            — {failure.code}
            {failure.status === null ? '' : `, HTTP ${failure.status}`}
          </span>
        )}
      </p>
      <p className="muted">{explainIncidentRejection(failure)}</p>
      {failure.code !== null && <p className="muted">The service said: {failure.message}</p>}
    </div>
  )
}

/** The facts the server owns, read-only. Status moves when the responder moves
 *  it — an operator cannot resolve an incident from this screen, and the panel
 *  does not pretend to offer it. */
function IncidentFacts({ incident }: { incident: IncidentOut }) {
  return (
    <dl className="incident-facts">
      <dt>Incident</dt>
      <dd>#{incident.id}</dd>
      <dt>Status</dt>
      <dd>
        <strong>{STATUS_LABEL[incident.status]}</strong>
        <span className="muted"> · {STATUS_DESCRIPTION[incident.status]}</span>
      </dd>
      <dt>Handled by</dt>
      <dd>
        {RESPONDER_ROLE_LABEL[incident.responder_role]}
        {incident.jurisdiction !== null && <span className="muted"> · {incident.jurisdiction}</span>}
      </dd>
      <dt>Severity</dt>
      <dd>{SEVERITY_LABEL[incident.severity]}</dd>
      <dt>Assigned to</dt>
      <dd>{incident.assignee === null ? <span className="muted">Nobody yet</span> : incident.assignee}</dd>
      {incident.h3_cell !== null && (
        <>
          <dt>H3 cell</dt>
          <dd className="report-result-cell">{incident.h3_cell}</dd>
        </>
      )}
      {incident.linked_prediction_run_id !== null && (
        <>
          <dt>Published run</dt>
          <dd className="report-result-cell">{incident.linked_prediction_run_id}</dd>
        </>
      )}
      <dt>Opened</dt>
      <dd>{when(incident.created_at)}</dd>
      {incident.resolved_at !== null && (
        <>
          <dt>Closed</dt>
          <dd>{when(incident.resolved_at)}</dd>
        </>
      )}
    </dl>
  )
}

/** The simulated hand-off, in words that cannot be read as a dispatch. */
function Deliveries({ incidentId }: { incidentId: number }) {
  const { deliveries, error } = useIncidentDeliveries(incidentId)
  if (error !== null) {
    return <p className="muted incident-empty">Could not read the delivery record: {error}</p>
  }
  if (deliveries.length === 0) {
    return (
      <p className="muted incident-empty">
        No hand-off yet. Assigning the incident puts it in that role’s inbox.
      </p>
    )
  }
  return (
    <>
      <p className="muted incident-simulator-note">{DELIVERY_DISCLAIMER}</p>
      <ul className="incident-evidence">
        {deliveries.map((delivery) => (
          <li key={delivery.id}>
            <span className="incident-evidence-source">
              {RESPONDER_ROLE_LABEL[delivery.audience_role]}
            </span>
            <span>
              {delivery.assignee === null ? 'Unnamed unit' : delivery.assignee} ·{' '}
              {delivery.status === 'simulated' ? 'awaiting acknowledgement' : 'acknowledged'}
            </span>
            <span className="muted">{when(delivery.simulated_at)}</span>
          </li>
        ))}
      </ul>
    </>
  )
}

/** The append-only history, newest first, with the acting authority on each row. */
function History({ incidentId }: { incidentId: number }) {
  const { events, loading, error } = useIncidentHistory(incidentId)
  if (error !== null) {
    return <p className="muted incident-empty">Could not read the history: {error}</p>
  }
  if (events.length === 0) {
    return <p className="muted incident-empty">{loading ? 'Reading history…' : 'No events yet.'}</p>
  }
  return (
    <ul className="incident-history">
      {[...events].reverse().map((event) => (
        <li key={event.id}>
          <span className="muted">{when(event.created_at)}</span>
          <span className="incident-history-kind">{EVENT_LABEL[event.event_type]}</span>
          <span>{describeEvent(event)}</span>
          <span className="muted">{actorSummary(event)}</span>
        </li>
      ))}
    </ul>
  )
}

/** Which responder this browser acts as. The server is the authority on what
 *  that actor may do; this only chooses the name to send. */
function ActorControl({
  actor,
  onActorChange,
}: {
  actor: string
  onActorChange: (next: string) => void
}) {
  return (
    <div className="incident-actor">
      <label className="report-field">
        <span>Acting as</span>
        <input
          className="incident-input"
          value={actor}
          list="incident-actor-options"
          placeholder="a configured responder id"
          aria-label="Acting as responder"
          onChange={(event) => onActorChange(event.target.value)}
        />
      </label>
      <datalist id="incident-actor-options">
        {SUGGESTED_ACTOR_IDS.map((id) => (
          <option key={id} value={id} />
        ))}
      </datalist>
      <button
        type="button"
        className="incident-button"
        onClick={() => {
          setActorOverride(actor)
          onActorChange(actor.trim())
        }}
      >
        Use this actor
      </button>
      <p className="muted incident-actor-note">
        The deployment key plus this actor id is the whole identity model — there is no login. The
        service decides the actor’s role and jurisdiction; asking for a different one is refused.
      </p>
    </div>
  )
}

/**
 * The incident for one source, backed by the real API.
 *
 * ## What replaced what
 *
 * This was a notebook in `localStorage`: the operator grouped an alert or a
 * report into a case, typed an assignee, moved a status dropdown and kept a
 * local history — none of it shared, authoritative or recorded anywhere. It was
 * honest about that in every panel, which was the best that could be done while
 * there was no incidents API. There is one now, so status, assignment and
 * history are read from it and written to it.
 *
 * What the panel will not do:
 *
 *  - **It never presents a device-local record as an operational one.** There
 *    is no local copy left to confuse with the real thing.
 *  - **It never implies a dispatch.** Assignment is a *simulated* hand-off into
 *    a role's inbox; no notification of any kind is sent, and the panel says so
 *    where the hand-off is shown.
 *  - **It never conflates the three empty/failed states.** No incident, an
 *    unreachable service, and a rejected write each say something different, and
 *    only the middle one leaves the truth unknown.
 */
export function IncidentPanel({
  kind,
  source,
  title,
  evidenceReportIds = [],
}: {
  kind: ResponseKind
  source: IncidentSource
  title: string
  /** Citizen report ids to record as evidence on the new incident. */
  evidenceReportIds?: number[]
}) {
  const { resource, refetch } = useIncidents()
  const incident =
    resource.status === 'success' ? findIncidentForSource(resource.data, source) : null

  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<IncidentFailure | null>(null)
  const [assignee, setAssignee] = useState('')
  // Held in state rather than read from the module on every render, so changing
  // it re-renders the write controls. The value is still only a name to send:
  // setActorOverride persists it for this session, and the server decides what
  // that name may do.
  const [actor, setActor] = useState(actorId)

  const openIncident = async () => {
    if (busy) return
    setBusy(true)
    setFailure(null)
    try {
      await createIncident(createBodyFor(source, evidenceReportIds))
      refetch()
    } catch (error) {
      setFailure(
        error instanceof ApiError
          ? classifyIncidentError(error)
          : classifyIncidentError(new Error('unexpected')),
      )
    } finally {
      setBusy(false)
    }
  }

  const doAssign = async () => {
    if (busy || incident === null) return
    setBusy(true)
    setFailure(null)
    try {
      await assignIncident(incident.id, assignee.trim())
      setAssignee('')
      refetch()
    } catch (error) {
      setFailure(classifyIncidentError(error))
    } finally {
      setBusy(false)
    }
  }

  const unavailable = writesUnavailableReason()
  const canWrite = writesAreConfigured()

  return (
    <section className={`incident incident-${kind}`} aria-label={`${RESPONSE_LABEL[kind]} incident`}>
      <header className="incident-header">
        <span className={`incident-kind incident-kind-${kind}`}>{RESPONSE_SHORT[kind]}</span>
        <h4>{RESPONSE_LABEL[kind]}</h4>
      </header>

      <p className="incident-scope">{RESPONSE_SCOPE[kind]}</p>
      <p className="muted incident-source-line">
        Source: {source.type === 'published_alert' ? 'published alert' : 'citizen report'} —{' '}
        <span className="report-result-cell">
          {source.type === 'published_alert' ? source.ref : `#${source.id}`}
        </span>{' '}
        · {title}
      </p>

      {resource.status === 'loading' && (
        <p className="muted incident-empty">Checking the incident service…</p>
      )}

      {resource.status === 'error' && (
        <div className="incident-service-down" role="alert">
          <p className="incident-write-failure-title">Cannot reach the incident service</p>
          <p className="muted">
            {resource.message} This says nothing about whether an incident exists for this source —
            it only says this browser could not ask.
          </p>
          <button type="button" className="incident-button" onClick={refetch}>
            Try again
          </button>
        </div>
      )}

      {resource.status === 'success' && incident === null && (
        <>
          <p className="muted incident-empty">
            No incident is open for this source. That is a fact about the service, not a gap in this
            browser.
          </p>
          {canWrite ? (
            <button
              type="button"
              className="incident-button incident-button-primary"
              onClick={openIncident}
              disabled={busy}
            >
              {busy ? 'Opening…' : 'Open an incident'}
            </button>
          ) : (
            <p className="muted incident-empty">{unavailable}</p>
          )}
        </>
      )}

      {resource.status === 'success' && incident !== null && (
        <>
          <IncidentFacts incident={incident} />

          {incident.source_synthetic && (
            <p className="incident-synthetic" role="note">
              Opened from a demo/synthetic publication — an operational record of a demonstration,
              not a real-world event.
            </p>
          )}

          <h5 className="incident-subhead">Hand-off</h5>
          <Deliveries incidentId={incident.id} />

          {!isTerminalStatus(incident.status) && (
            <div className="incident-assign">
              {incident.status === 'reported' ? (
                <>
                  <p className="muted incident-assign-note">
                    {RESPONDER_ROLE_LABEL[incident.responder_role]} acts next. Naming a unit records
                    a simulated hand-off to that role’s inbox — nobody is contacted.
                  </p>
                  <div className="incident-note-row">
                    <input
                      className="incident-input"
                      value={assignee}
                      placeholder="unit or person name"
                      aria-label="Assign to"
                      onChange={(event) => setAssignee(event.target.value)}
                    />
                    <button
                      type="button"
                      className="incident-button"
                      onClick={doAssign}
                      disabled={busy || assignee.trim() === ''}
                    >
                      {busy ? 'Assigning…' : 'Assign'}
                    </button>
                  </div>
                </>
              ) : (
                <p className="muted incident-assign-note">
                  The responder moves this on from their app. This screen shows their updates as
                  they arrive.
                </p>
              )}
            </div>
          )}

          <h5 className="incident-subhead">History</h5>
          <History incidentId={incident.id} />

          {canWrite ? (
            <ActorControl actor={actor} onActorChange={setActor} />
          ) : (
            <p className="muted incident-empty">{unavailable}</p>
          )}

          <p className="muted incident-reference">
            {RESPONSE_HANDLED_BY[kind]}. {DELIVERY_DISCLAIMER}
          </p>
        </>
      )}

      {failure !== null && <WriteFailure failure={failure} />}
    </section>
  )
}
