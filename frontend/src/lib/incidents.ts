// The persistent incident workflow, as /api/v1/incidents defines it.
//
// This replaces the browser-local notebook that used to stand in for it. Every
// fact below comes from the server: the status, the role allowed to act, the
// assignee, and the append-only history. Nothing here is stored on the device,
// which is the point — a local note was never an operational record and must
// never be shown as if it were one.
//
// Three states are kept apart on purpose, because conflating them would each be
// a different lie:
//   - **no incident** — the service answered, and this source has none yet;
//   - **cannot reach the service** — no answer at all, which says nothing about
//     whether an incident exists;
//   - **write rejected** — the service answered and refused, with its own code.

import type {
  IncidentCreate,
  IncidentEventOut,
  IncidentOut,
  IncidentSourceType,
  IncidentStatus,
  InboxItemOut,
  ResponderRole,
} from './types'
import { ApiError } from './api'

export const RESPONDER_ROLE_LABEL: Record<ResponderRole, string> = {
  fire_department: 'Fire department',
  pollution_control: 'Pollution control',
}

/** The status names the server uses, in the order it allows them. */
export const STATUS_LABEL: Record<IncidentStatus, string> = {
  reported: 'Reported',
  assigned: 'Assigned',
  acknowledged: 'Acknowledged',
  en_route: 'En route',
  on_scene: 'On scene',
  resolved: 'Resolved',
  cancelled: 'Cancelled',
}

export const STATUS_DESCRIPTION: Record<IncidentStatus, string> = {
  reported: 'Opened. Nobody is on it yet.',
  assigned: 'Handed to a named unit — a simulated hand-off, nothing was sent to anyone.',
  acknowledged: 'The responder has acknowledged it.',
  en_route: 'The responder is on the way.',
  on_scene: 'The responder is at the location.',
  resolved: 'Closed by the responder.',
  cancelled: 'Cancelled. The record stays; incidents are never deleted.',
}

/** The transition table the server enforces, mirrored so the UI only ever
 *  offers a step that can succeed. `assigned` is absent on purpose: reaching it
 *  requires the assign endpoint, and a generic transition to it is refused. */
const ALLOWED_TRANSITIONS: Record<IncidentStatus, readonly IncidentStatus[]> = {
  reported: ['cancelled'],
  assigned: ['acknowledged', 'cancelled'],
  acknowledged: ['en_route', 'cancelled'],
  en_route: ['on_scene', 'cancelled'],
  on_scene: ['resolved', 'cancelled'],
  resolved: [],
  cancelled: [],
}

export function allowedNextStatuses(status: IncidentStatus): readonly IncidentStatus[] {
  return ALLOWED_TRANSITIONS[status]
}

export function isTerminalStatus(status: IncidentStatus): boolean {
  return ALLOWED_TRANSITIONS[status].length === 0
}

export const EVENT_LABEL: Record<IncidentEventOut['event_type'], string> = {
  created: 'Opened',
  assigned: 'Assigned',
  reassigned: 'Reassigned',
  delivered: 'Delivered (simulated)',
  transition: 'Status',
}

/** What the hand-off is, stated so it cannot be read as a dispatch. */
export const DELIVERY_DISCLAIMER =
  'Simulated hand-off: the incident appears in that role’s inbox. No email, SMS, ' +
  'webhook or push is sent by this system.'

/** A source the operator can open an incident from. Exactly one of `ref`/`id`
 *  is set, matching the type — the same rule the server enforces. */
export type IncidentSource =
  | { type: 'published_alert'; ref: string; cell: string | null; title: string }
  | { type: 'report'; id: number; cell: string | null; title: string }

/** The create body for a source, with the evidence links the caller holds. */
export function createBodyFor(
  source: IncidentSource,
  evidenceReportIds: number[] = [],
): IncidentCreate {
  if (source.type === 'published_alert') {
    return {
      source_type: 'published_alert',
      source_ref: source.ref,
      ...(evidenceReportIds.length > 0 ? { evidence_report_ids: evidenceReportIds } : {}),
    }
  }
  return {
    source_type: 'report',
    source_id: source.id,
    ...(evidenceReportIds.length > 0 ? { evidence_report_ids: evidenceReportIds } : {}),
  }
}

/** Does this stored incident belong to this source? The server guarantees one
 *  incident per `(source_type, source_id|source_ref)`, so this is an exact
 *  match on the key — not a containment test like the map's cell lookup. */
export function incidentMatchesSource(incident: IncidentOut, source: IncidentSource): boolean {
  if (incident.source_type !== source.type) return false
  if (source.type === 'published_alert') {
    return incident.source_ref === source.ref
  }
  return incident.source_id === source.id
}

export function findIncidentForSource(
  incidents: IncidentOut[],
  source: IncidentSource,
): IncidentOut | null {
  return incidents.find((incident) => incidentMatchesSource(incident, source)) ?? null
}

/** How a failure should be read. The three cases are genuinely different and
 *  the UI must not blur them. */
export type IncidentFailureKind = 'unreachable' | 'rejected'

export interface IncidentFailure {
  kind: IncidentFailureKind
  /** The server's machine code, or null for a transport failure — which never
   *  gets an invented status. */
  code: string | null
  status: number | null
  message: string
  /** Whether trying the same write again could plausibly work. */
  retryable: boolean
}

/** Map any thrown value onto the two failure kinds. */
export function classifyIncidentError(error: unknown): IncidentFailure {
  if (error instanceof ApiError) {
    if (error.status === 0) {
      return {
        kind: 'unreachable',
        code: null,
        status: null,
        message:
          'Could not reach the incident service. Nothing is known about whether an incident ' +
          'exists, and nothing was changed.',
        retryable: true,
      }
    }
    return {
      kind: 'rejected',
      code: error.code,
      status: error.status,
      message: error.message,
      retryable: error.status >= 500,
    }
  }
  return {
    kind: 'unreachable',
    code: null,
    status: null,
    message: 'Could not reach the incident service.',
    retryable: true,
  }
}

/** The refusal a reader needs, in words. The server's message is always shown
 *  too; this explains what class of refusal it is. */
export function explainIncidentRejection(failure: IncidentFailure): string {
  if (failure.kind === 'unreachable') return failure.message
  switch (failure.code) {
    case 'unauthorized':
      return 'The write was refused: this browser is not sending credentials the service accepts. ' +
        'It is not a statement about the incident.'
    case 'role_mismatch':
      return 'The write was refused: the actor you are acting as does not hold the role this ' +
        'incident is handled by. The server decides the role; a request cannot widen it.'
    case 'jurisdiction_mismatch':
      return 'The write was refused: this actor is scoped to a different jurisdiction than the ' +
        "incident's."
    case 'invalid_transition':
      return 'The write was refused: that step is not allowed from the current status. Nothing changed.'
    case 'use_assign_endpoint':
      return 'The write was refused: assignment has its own endpoint, which is what was used.'
    case 'conflict':
      return 'The write was refused: an incident already exists for this source with different ' +
        'attributes. The stored record was left untouched.'
    case 'validation_error':
      return 'The write was refused: the source is not eligible for an incident, or the payload ' +
        'was not valid.'
    case 'not_found':
      return 'The write was refused: the service does not know that source.'
    case 'simulator_disabled':
      return 'The write was refused: the deployment has no simulator key or actor registry ' +
        'configured, so every write is disabled.'
    default:
      return failure.status !== null && failure.status >= 500
        ? 'The service failed to apply the write. Nothing is known about whether it took effect.'
        : 'The service refused the write.'
  }
}

/** One line naming an event, for the history list. */
export function describeEvent(event: IncidentEventOut): string {
  const who = event.actor === null ? 'the service' : event.actor
  switch (event.event_type) {
    case 'created':
      return `Opened by ${who}.`
    case 'assigned':
      return `Assigned to ${event.note ?? 'a unit'} by ${who}.`
    case 'reassigned':
      return `Reassigned to ${event.note ?? 'a unit'} by ${who}.`
    case 'delivered':
      return `Appeared in the ${event.role === null ? 'responder' : RESPONDER_ROLE_LABEL[event.role]} inbox — simulated, nothing was sent.`
    case 'transition':
      return event.note !== null && event.note !== ''
        ? `${STATUS_LABEL[event.from_status ?? 'reported']} → ${STATUS_LABEL[event.to_status ?? 'reported']} by ${who}: ${event.note}`
        : `${STATUS_LABEL[event.from_status ?? 'reported']} → ${STATUS_LABEL[event.to_status ?? 'reported']} by ${who}.`
  }
}

/** The actor that acted, with its role and jurisdiction — which authority, not
 *  merely which role. */
export function actorSummary(event: IncidentEventOut): string {
  if (event.actor === null) return 'service'
  const parts = [event.actor]
  if (event.role !== null) parts.push(RESPONDER_ROLE_LABEL[event.role])
  if (event.actor_jurisdiction !== null && event.actor_jurisdiction !== '') {
    parts.push(event.actor_jurisdiction)
  }
  return parts.join(' · ')
}

/** Re-exported so callers do not have to reach into types for the shape. */
export type { IncidentEventOut, IncidentOut, IncidentSourceType, IncidentStatus, InboxItemOut }
