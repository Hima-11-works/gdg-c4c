// The operator's incident notebook — on this device only.
//
// ## Why this is local, and what that costs
//
// There is no incidents API in this deployment, and no incidents contract to
// build against: no incident table, no assignment, jurisdiction or
// response-state endpoints, nothing for a "simulator action" to call. So this
// does NOT pretend to be that. An incident here is a record the operator keeps
// in this browser: they can group an alert or a detection into a case, name
// who has it, note a jurisdiction, move it through states and keep a running
// history — and it survives a reload, because localStorage does.
//
// What it is NOT, and the UI says so on every incident:
//   - not shared: no other operator, device or session can see it
//   - not authoritative: it is not an official log of anything
//   - not synced: clearing site data loses it
//   - not evidence: the evidence it *links* is real backend data (a filed
//     report's id, a cell, an alert's readings); the link is a bookmark, and
//     the notebook stores a copy of the reference, not the record.
//
// If a real incident API ever lands, this module is the seam to replace: the
// shapes below already separate what a backend would own (status, assignee,
// history) from what is inherently local (the notebook itself).

import { isIncidentStatus } from './responseTypes'
import type { IncidentStatus, ResponseKind } from './responseTypes'

const STORAGE_KEY = 'air-health:incidents'

/** Emitted on window after every write, so each mounted notebook re-reads. */
export const INCIDENTS_CHANGED_EVENT = 'air-health:incidents-changed'

/** Where a piece of linked evidence came from. Every one of these points at
 *  something that exists in the backend, not at something invented here. */
export type EvidenceSource =
  | 'alert'
  | 'citizen_report'
  | 'thermal_anomaly'
  | 'cell_reading'

export interface IncidentEvidence {
  source: EvidenceSource
  /** What it points at: an H3 cell, a report id, a detection id. */
  ref: string
  /** One sentence an operator can read without opening anything. */
  summary: string
  /** When the underlying record is from, ISO — null when it has no time. */
  at: string | null
}

export interface IncidentHistoryEntry {
  at: string
  text: string
}

export interface LocalIncident {
  id: string
  kind: ResponseKind
  h3Cell: string
  title: string
  status: IncidentStatus
  /** Free text. There is no personnel directory, so this is whoever the
   *  operator names — not a user id from anywhere. */
  assignee: string
  /** Free text. No jurisdiction boundary dataset exists in this project, so
   *  this is the operator's own words, never a looked-up boundary. */
  jurisdiction: string
  createdAt: string
  updatedAt: string
  history: IncidentHistoryEntry[]
  evidence: IncidentEvidence[]
}

const EVIDENCE_SOURCES: readonly EvidenceSource[] = [
  'alert',
  'citizen_report',
  'thermal_anomaly',
  'cell_reading',
]

function isEvidenceSource(value: unknown): value is EvidenceSource {
  return typeof value === 'string' && (EVIDENCE_SOURCES as readonly string[]).includes(value)
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

/** Parse one stored record, dropping anything that is not shaped like an
 *  incident rather than throwing — a corrupt entry must not break the panel. */
function parseIncident(raw: unknown): LocalIncident | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const record = raw as Record<string, unknown>
  const kind = record.kind
  if (kind !== 'fire' && kind !== 'pollution') return null
  if (!isIncidentStatus(record.status)) return null
  const id = str(record.id)
  const h3Cell = str(record.h3Cell)
  if (id === '' || h3Cell === '') return null

  const history = Array.isArray(record.history)
    ? record.history
        .map((entry): IncidentHistoryEntry | null => {
          if (typeof entry !== 'object' || entry === null) return null
          const item = entry as Record<string, unknown>
          const at = str(item.at)
          const text = str(item.text)
          return at === '' || text === '' ? null : { at, text }
        })
        .filter((entry): entry is IncidentHistoryEntry => entry !== null)
    : []

  const evidence = Array.isArray(record.evidence)
    ? record.evidence
        .map((entry): IncidentEvidence | null => {
          if (typeof entry !== 'object' || entry === null) return null
          const item = entry as Record<string, unknown>
          if (!isEvidenceSource(item.source)) return null
          const ref = str(item.ref)
          const summary = str(item.summary)
          if (ref === '' && summary === '') return null
          return {
            source: item.source,
            ref,
            summary,
            at: typeof item.at === 'string' ? item.at : null,
          }
        })
        .filter((entry): entry is IncidentEvidence => entry !== null)
    : []

  const now = new Date().toISOString()
  return {
    id,
    kind,
    h3Cell,
    title: str(record.title, h3Cell),
    status: record.status,
    assignee: str(record.assignee),
    jurisdiction: str(record.jurisdiction),
    createdAt: str(record.createdAt, now),
    updatedAt: str(record.updatedAt, now),
    history,
    evidence,
  }
}

/** Parsed-store cache.
 *
 *  Two reasons this exists rather than re-reading localStorage every call:
 *  reading and validating JSON on each render is wasteful, and the React hook
 *  reads through `useSyncExternalStore`, which needs `findIncident` to return
 *  the *same object* until something actually changed — a fresh parse every
 *  call would look like a change on every render and loop. Every write
 *  replaces the cache with the array it just stored, so a snapshot is stable
 *  exactly until a write happens. */
let cache: LocalIncident[] | null = null

/** Drop the cache so the next read re-parses. Used when another tab writes
 *  (a `storage` event this tab did not cause). */
export function invalidateIncidentCache(): void {
  cache = null
}

export function readIncidents(): LocalIncident[] {
  if (cache !== null) return cache

  let raw: unknown
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === null) {
      cache = []
      return cache
    }
    raw = JSON.parse(stored)
  } catch {
    cache = []
    return cache
  }
  cache = Array.isArray(raw)
    ? raw.map(parseIncident).filter((incident): incident is LocalIncident => incident !== null)
    : []
  return cache
}

function writeIncidents(incidents: LocalIncident[]): void {
  // The cache becomes the written array, so a snapshot taken after a write is
  // the new array and one taken before it is the old one — no re-parse, and no
  // spurious change.
  cache = incidents
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(incidents))
    window.dispatchEvent(new Event(INCIDENTS_CHANGED_EVENT))
  } catch {
    // Storage disabled or full: the notebook is best-effort by design.
  }
}

/** The incident this device holds for a subject, or null. One incident per
 *  (kind, cell) — an incident is about a subject, not a thing you collect. */
export function findIncident(kind: ResponseKind, h3Cell: string): LocalIncident | null {
  return readIncidents().find((incident) => incident.kind === kind && incident.h3Cell === h3Cell) ?? null
}

export interface NewIncident {
  kind: ResponseKind
  h3Cell: string
  title: string
  evidence: IncidentEvidence[]
}

export function createIncident(input: NewIncident): LocalIncident {
  const existing = findIncident(input.kind, input.h3Cell)
  if (existing !== null) return existing

  const now = new Date().toISOString()
  const incident: LocalIncident = {
    id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    kind: input.kind,
    h3Cell: input.h3Cell,
    title: input.title,
    status: 'open',
    assignee: '',
    jurisdiction: '',
    createdAt: now,
    updatedAt: now,
    history: [
      {
        at: now,
        text: `Opened on this device as a ${input.kind === 'fire' ? 'fire' : 'pollution-control'} incident.`,
      },
    ],
    evidence: input.evidence,
  }
  writeIncidents([...readIncidents(), incident])
  return incident
}

function patch(
  id: string,
  apply: (incident: LocalIncident) => LocalIncident,
): LocalIncident | null {
  const incidents = readIncidents()
  let updated: LocalIncident | null = null
  const next = incidents.map((incident) => {
    if (incident.id !== id) return incident
    const now = new Date().toISOString()
    updated = { ...apply(incident), updatedAt: now }
    return updated
  })
  if (updated === null) return null
  writeIncidents(next)
  return updated
}

/** Move the incident to a new state, recording why in the history. */
export function setIncidentStatus(id: string, status: IncidentStatus): LocalIncident | null {
  return patch(id, (incident) => {
    if (incident.status === status) return incident
    const now = new Date().toISOString()
    return {
      ...incident,
      status,
      history: [
        ...incident.history,
        { at: now, text: `Status: ${incident.status} → ${status}.` },
      ],
    }
  })
}

/** Name who has it and where. Both are the operator's own words; there is no
 *  personnel directory and no jurisdiction boundary dataset behind them. */
export function assignIncident(
  id: string,
  assignee: string,
  jurisdiction: string,
): LocalIncident | null {
  return patch(id, (incident) => {
    const nextAssignee = assignee.trim()
    const nextJurisdiction = jurisdiction.trim()
    if (
      nextAssignee === incident.assignee &&
      nextJurisdiction === incident.jurisdiction
    ) {
      return incident
    }
    const now = new Date().toISOString()
    const parts: string[] = []
    if (nextAssignee !== incident.assignee) {
      parts.push(nextAssignee === '' ? 'Assignee cleared.' : `Assigned to ${nextAssignee}.`)
    }
    if (nextJurisdiction !== incident.jurisdiction) {
      parts.push(
        nextJurisdiction === ''
          ? 'Jurisdiction cleared.'
          : `Jurisdiction noted as ${nextJurisdiction}.`,
      )
    }
    return {
      ...incident,
      assignee: nextAssignee,
      jurisdiction: nextJurisdiction,
      // Naming someone is an assignment, so the state follows the field unless
      // the operator has already moved it on.
      status: incident.status === 'open' && nextAssignee !== '' ? 'assigned' : incident.status,
      history: [...incident.history, { at: now, text: parts.join(' ') }],
    }
  })
}

export function addIncidentNote(id: string, text: string): LocalIncident | null {
  const trimmed = text.trim()
  if (trimmed === '') return null
  return patch(id, (incident) => ({
    ...incident,
    history: [...incident.history, { at: new Date().toISOString(), text: trimmed }],
  }))
}

/** Replace the linked evidence (e.g. after a new alert arrives for the cell). */
export function setIncidentEvidence(id: string, evidence: IncidentEvidence[]): LocalIncident | null {
  return patch(id, (incident) => ({ ...incident, evidence }))
}

export function deleteIncident(id: string): void {
  writeIncidents(readIncidents().filter((incident) => incident.id !== id))
}
