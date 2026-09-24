// The two response tracks this dashboard can talk about, and the words for
// them.
//
// There is no incidents API in this deployment (see lib/incidentNotebook.ts
// for what that means in practice), so nothing here is a workflow object the
// backend knows about. What it is instead is a vocabulary, kept in one place
// so a fire response and a pollution-control response can never be described
// in each other's terms — a mistake the app used to make, addressing a
// burning-detection escalation to a pollution board.
//
// A fire response is about a *source*: something burning at a point. A
// pollution-control response is about an *area*: PM2.5 over a cell. They have
// different evidence (a detection or a filed report vs. readings and a
// forecast), different judgements, and different bodies that would act on
// them. Keeping the labels separate is what stops an operator reading one as
// the other.

export type ResponseKind = 'fire' | 'pollution'

/** Full label, used as a heading. */
export const RESPONSE_LABEL: Record<ResponseKind, string> = {
  fire: 'Fire response',
  pollution: 'Pollution-control response',
}

/** Short label, for a chip or a row where the full one is too long. */
export const RESPONSE_SHORT: Record<ResponseKind, string> = {
  fire: 'Fire / burning',
  pollution: 'Air quality',
}

/** What a response of this kind is actually about. */
export const RESPONSE_SCOPE: Record<ResponseKind, string> = {
  fire: 'A source: burning, smoke or a thermal detection at a place, as reported or detected.',
  pollution: 'An area: ambient PM2.5 over a cell, as measured or forecast for a published run.',
}

/** Who typically handles this kind of incident in India. A reference for the
 *  operator, NOT an address list this app uses: nothing here contacts anyone.
 *  Kept per response kind so a fire incident never points at an air-quality
 *  body, and vice versa. */
export const RESPONSE_HANDLED_BY: Record<ResponseKind, string> = {
  fire: 'Municipal fire service (the forest department for a forest fire)',
  pollution: 'State Pollution Control Board (CPCB for a trans-state episode)',
}

export const RESPONSE_REFERENCE_NOTE =
  'For reference only — this dashboard has no authority-routing backend, so it contacts nobody.'

/** Local prompts for the operator's own checklist, per response kind. These
 *  are things to think about, not a workflow: ticking one records nothing
 *  anywhere and notifies no one. */
export const RESPONSE_STEPS: Record<ResponseKind, string[]> = {
  fire: [
    'Note the burning type and how much smoke',
    'Check whether a citizen report was filed in this cell',
    'Decide whether the site needs a visit',
  ],
  pollution: [
    'Compare with the same hour yesterday',
    'Check whether the forecast makes it worse',
    'Note how many residents the cell covers',
  ],
}

export const RESPONSE_STEPS_NOTE =
  'Prompts for you, kept on this device. Nothing here is dispatched, notified or recorded.'

// --- incident state, as the local notebook stores it ---

export const INCIDENT_STATUSES = ['open', 'assigned', 'responding', 'resolved'] as const

export type IncidentStatus = (typeof INCIDENT_STATUSES)[number]

export const STATUS_LABEL: Record<IncidentStatus, string> = {
  open: 'Open',
  assigned: 'Assigned',
  responding: 'Responding',
  resolved: 'Resolved',
}

export const STATUS_DESCRIPTION: Record<IncidentStatus, string> = {
  open: 'Noted, nobody on it yet.',
  assigned: 'Someone is named as responsible.',
  responding: 'Work is under way.',
  resolved: 'Closed by the operator.',
}

export function isIncidentStatus(value: unknown): value is IncidentStatus {
  return typeof value === 'string' && (INCIDENT_STATUSES as readonly string[]).includes(value)
}
