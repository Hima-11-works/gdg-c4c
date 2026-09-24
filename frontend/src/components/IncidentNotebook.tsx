import { useState } from 'react'
import {
  INCIDENT_STATUSES,
  RESPONSE_HANDLED_BY,
  RESPONSE_LABEL,
  RESPONSE_REFERENCE_NOTE,
  RESPONSE_SCOPE,
  RESPONSE_SHORT,
  STATUS_DESCRIPTION,
  STATUS_LABEL,
  isIncidentStatus,
} from '../lib/responseTypes'
import {
  addIncidentNote,
  assignIncident,
  createIncident,
  deleteIncident,
  setIncidentStatus,
} from '../lib/incidentNotebook'
import { useIncident } from '../hooks/useIncidents'
import type { EvidenceSource, IncidentEvidence, LocalIncident } from '../lib/incidentNotebook'
import type { ResponseKind } from '../lib/responseTypes'

const EVIDENCE_LABEL: Record<EvidenceSource, string> = {
  alert: 'Alert',
  citizen_report: 'Citizen report',
  thermal_anomaly: 'Thermal detection',
  cell_reading: 'Cell reading',
}

function when(iso: string | null): string {
  if (iso === null) return 'time not recorded'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? 'time not recorded' : date.toLocaleString()
}

/**
 * The editable half of the notebook, mounted with the incident's id as its
 * key. That is deliberate: the assignment fields start from the stored record
 * and are then the operator's to edit, so a re-render must not overwrite what
 * they are typing. A key change (a different incident) remounts this and
 * re-seeds — no effect, no state synchronisation to get wrong.
 */
function IncidentEditor({ incident }: { incident: LocalIncident }) {
  const [assignee, setAssignee] = useState(incident.assignee)
  const [jurisdiction, setJurisdiction] = useState(incident.jurisdiction)
  const [note, setNote] = useState('')

  const status = incident.status

  return (
    <>
      <dl className="incident-facts">
        <dt>Status</dt>
        <dd>
          <select
            className="incident-status"
            value={status}
            aria-label="Incident status"
            onChange={(event) => {
              if (isIncidentStatus(event.target.value)) {
                setIncidentStatus(incident.id, event.target.value)
              }
            }}
          >
            {INCIDENT_STATUSES.map((value) => (
              <option key={value} value={value}>
                {STATUS_LABEL[value]}
              </option>
            ))}
          </select>
          <span className="muted"> · {STATUS_DESCRIPTION[status]}</span>
        </dd>

        <dt>Assigned to</dt>
        <dd>
          <input
            className="incident-input"
            value={assignee}
            placeholder="name or team — your own words, not a directory"
            aria-label="Assigned to"
            onChange={(event) => setAssignee(event.target.value)}
          />
        </dd>

        <dt>Jurisdiction</dt>
        <dd>
          <input
            className="incident-input"
            value={jurisdiction}
            placeholder="your own words — no boundary data exists"
            aria-label="Jurisdiction"
            onChange={(event) => setJurisdiction(event.target.value)}
          />
        </dd>
      </dl>
      <button
        type="button"
        className="incident-button"
        onClick={() => assignIncident(incident.id, assignee, jurisdiction)}
        disabled={assignee === incident.assignee && jurisdiction === incident.jurisdiction}
      >
        Save assignment
      </button>

      <p className="muted incident-reference">
        {RESPONSE_HANDLED_BY[incident.kind]}. {RESPONSE_REFERENCE_NOTE}
      </p>

      <h5 className="incident-subhead">Linked evidence</h5>
      {incident.evidence.length === 0 ? (
        <p className="muted incident-empty">Nothing linked yet.</p>
      ) : (
        <ul className="incident-evidence">
          {incident.evidence.map((item) => (
            <li key={`${item.source}-${item.ref}-${item.at ?? ''}`}>
              <span className="incident-evidence-source">{EVIDENCE_LABEL[item.source]}</span>
              <span>{item.summary}</span>
              <span className="muted">
                {item.ref} · {when(item.at)}
              </span>
            </li>
          ))}
        </ul>
      )}

      <h5 className="incident-subhead">History</h5>
      <ul className="incident-history">
        {[...incident.history].reverse().map((entry, index) => (
          <li key={`${entry.at}-${index}`}>
            <span className="muted">{when(entry.at)}</span>
            <span>{entry.text}</span>
          </li>
        ))}
      </ul>

      <div className="incident-note-row">
        <input
          className="incident-input"
          value={note}
          placeholder="Add a note to the history"
          aria-label="Add a note"
          onChange={(event) => setNote(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && note.trim() !== '') {
              addIncidentNote(incident.id, note)
              setNote('')
            }
          }}
        />
        <button
          type="button"
          className="incident-button"
          disabled={note.trim() === ''}
          onClick={() => {
            addIncidentNote(incident.id, note)
            setNote('')
          }}
        >
          Add
        </button>
      </div>

      <p className="muted incident-simulator-note">
        Response actions (dispatch, acknowledge, notify) would go through the incident API. This
        deployment has no such API, so none is offered here rather than offering one that would
        not do anything.
      </p>

      <button
        type="button"
        className="incident-button incident-button-danger"
        onClick={() => deleteIncident(incident.id)}
      >
        Discard this incident
      </button>
    </>
  )
}

/**
 * The operator's notebook for one subject: a fire response or a
 * pollution-control response at a cell.
 *
 * ## What this is, precisely
 *
 * There is no incidents API in this deployment — no incident table, no
 * assignment or status endpoint, and nothing for a simulator action to call.
 * So this notebook is honest about being exactly one thing: a record the
 * operator keeps **in this browser**. It survives a reload because
 * localStorage does, and for no other reason.
 *
 * It is therefore NOT:
 *  - shared — no other operator, device or session sees it;
 *  - authoritative — it is not an official log;
 *  - evidence — the *links* below point at real backend records (a filed
 *    report's id, a cell's readings), but the notebook stores the reference,
 *    not the record, and nothing here was produced by a backend workflow.
 *
 * Every incident carries that caveat in the UI rather than in a comment, and
 * the UI offers no action that would need the missing API without saying it is
 * local.
 */
export function IncidentNotebook({
  kind,
  h3Cell,
  title,
  evidence,
}: {
  kind: ResponseKind
  h3Cell: string
  title: string
  /** Links to the backend records this incident is about, built by the caller
   *  from data it already holds. */
  evidence: IncidentEvidence[]
}) {
  const incident = useIncident(kind, h3Cell)

  return (
    <section
      className={`incident incident-${kind}`}
      aria-label={`${RESPONSE_LABEL[kind]} notebook`}
    >
      <header className="incident-header">
        <span className={`incident-kind incident-kind-${kind}`}>{RESPONSE_SHORT[kind]}</span>
        <h4>{RESPONSE_LABEL[kind]}</h4>
      </header>

      <p className="incident-scope">{RESPONSE_SCOPE[kind]}</p>

      <p className="incident-local" role="note">
        <strong>On this device only.</strong> There is no incidents API in this deployment, so
        this notebook is stored in this browser and works nowhere else. It is not an official
        record, no other operator can see it, and clearing site data erases it. It survives a
        reload because browser storage does.
      </p>

      {incident === null ? (
        <>
          <p className="muted incident-empty">No incident is open for this cell on this device.</p>
          <button
            type="button"
            className="incident-button incident-button-primary"
            onClick={() => createIncident({ kind, h3Cell, title, evidence })}
          >
            Open a local incident
          </button>
        </>
      ) : (
        <IncidentEditor key={incident.id} incident={incident} />
      )}
    </section>
  )
}
