// Imagery-derived fire candidates, as a triage view over real FIRMS records.
//
// The distinction this module exists to keep, because three layers on this map
// are easily read as one thing:
//
//   measured PM2.5      the gridded pollutant field - a modelled concentration
//   confirmed fires     nothing on this map is a confirmed fire
//   FIRMS detections    real satellite thermal anomalies, stored by the backend
//   imagery candidates  THIS FILE - a *candidate* derived from those detections
//
// A candidate is not a detection with a louder colour. It is a detection the
// dashboard has put forward for a human to look at, and the only thing that
// puts it forward is the detector's own `confidence_class`. That field is
// genuine FIRMS output, so the triage decision is grounded in the API rather
// than invented here; what the API does *not* carry is named as a gap below
// instead of being filled in with a plausible value.
//
// Nothing in this file may be read as evidence of a fire on the ground. The
// backend's own words, kept in the panel: a FIRMS record is "a satellite
// thermal detection, not a confirmed ground fire".

import type { ActiveFire } from './activeFires'
import type { FeatureCollection, Point } from 'geojson'

/** How a detection stands as a candidate. Derived from the detector's own
 *  confidence class, which is the only evidence the API offers. */
export type CandidateStatus =
  /** Worth a human's time: nominal or high detector confidence. */
  | 'candidate'
  /** Below the candidate threshold: the detector itself said low confidence. */
  | 'rejected'
  /** The detector did not classify it, so no triage is possible. */
  | 'unclassifiable'

/** What the API does not carry, named explicitly rather than filled in.
 *
 *  Each of these was checked against the backend: `GET /api/v1/fires` returns
 *  `FireHotspotOut`, which has no such field, and the strings "detector",
 *  "review" and "candidate" do not appear anywhere in the hotspot code path.
 *  Rendering a plausible value for any of them would be a fabrication, so the
 *  panel prints the gap and the reason. */
export const EVIDENCE_GAPS: { field: string; gap: string }[] = [
  {
    field: 'Detector version',
    gap: 'Not reported by the API. The detection record carries no detector or processor version, so nothing is shown rather than a guessed build.',
  },
  {
    field: 'Human review state',
    gap: "Not reported by the API. No reviewer, decision or timestamp exists for a hotspot, so every candidate here is untriaged by a person — a “below candidate threshold” status is the detector's own confidence, not a human decision.",
  },
  {
    field: 'Station corroboration',
    gap: 'No station evidence is linked. GET /api/v1/fires returns no observation, monitor or corroborating measurement, so this candidate rests on one satellite thermal detection alone.',
  },
]

/** The API's own confidence vocabulary, mapped to a triage status.
 *
 *  `confidence_class` is documented as `low` | `nominal` | `high` | `unknown`.
 *  `low` becomes a rejected candidate rather than a hidden one: the record is
 *  real, the detector was simply unsure, and hiding it would lose the fact that
 *  the detector saw something and judged it weak. */
export function candidateStatus(fire: ActiveFire): CandidateStatus {
  switch (fire.confidenceClass.toLowerCase()) {
    case 'low':
      return 'rejected'
    case 'nominal':
    case 'high':
      return 'candidate'
    default:
      return 'unclassifiable'
  }
}

const STATUS_LABEL: Record<CandidateStatus, string> = {
  candidate: 'Candidate',
  rejected: 'Below candidate threshold',
  unclassifiable: 'Not classifiable',
}

export function candidateStatusLabel(status: CandidateStatus): string {
  return STATUS_LABEL[status]
}

export type HotspotSourceCategory = 'unclassified'

export interface HotspotSourceInfo {
  category: HotspotSourceCategory
  label: string
  badgeClass: string
  icon: string
  description: string
}

/** FIRMS thermal detections do not identify the emitting source. */
export function unclassifiedHotspotSource(): HotspotSourceInfo {
  return {
    category: 'unclassified',
    label: 'Source unconfirmed',
    badgeClass: 'hotspot-source-unknown',
    icon: '🔎',
    description:
      'Satellite thermal anomaly only. Location, brightness, fire radiative power, and overpass time do not establish its source.',
  }
}

export interface HotspotCandidate {
  detectionId: string
  h3Cell: string
  latitude: number
  longitude: number
  status: CandidateStatus
  sourceInfo: HotspotSourceInfo
  /** The detector's normalised class, verbatim. */
  confidenceClass: string
  /** The raw FIRMS token (`l`/`n`/`h`, or a 0-100 string). */
  confidenceRaw: string
  acquiredAt: string
  /** True when `acquired_at` parsed; a bad timestamp is shown as unknown, not
   *  silently rendered as "now". */
  acquiredAtValid: boolean
  satellite: string
  daynight: string
  frpMw: number | null
  brightnessTi4K: number | null
}

export function hotspotCandidateFromRow(fire: ActiveFire): HotspotCandidate {
  const acquired = new Date(fire.acquiredAt)
  return {
    detectionId: fire.id,
    h3Cell: fire.h3Cell,
    latitude: fire.latitude,
    longitude: fire.longitude,
    status: candidateStatus(fire),
    sourceInfo: unclassifiedHotspotSource(),
    confidenceClass: fire.confidenceClass,
    confidenceRaw: fire.confidenceLabel,
    acquiredAt: fire.acquiredAt,
    acquiredAtValid: !Number.isNaN(acquired.getTime()),
    satellite: fire.satellite,
    daynight: fire.daynight,
    frpMw: fire.frp,
    brightnessTi4K: fire.brightness,
  }
}

/** The reason no candidate is drawn, kept apart by cause.
 *
 *  "The service is unreachable" and "the service answered and there is nothing
 *  there" are different facts, and only the first is an error. Collapsing them
 *  would turn a connection problem into a claim about the satellite feed. */
export type CandidatesUnavailableReason = 'unreachable' | 'no-detections' | 'none-passing'

export function candidatesUnavailable(
  reason: CandidatesUnavailableReason,
  totals: { detected: number; candidates: number; rejected: number },
): string {
  switch (reason) {
    case 'unreachable':
      return 'The satellite detection service could not be read, so this dashboard cannot say whether there are candidates. That is not the same as there being none.'
    case 'no-detections':
      return 'The service answered and reported no thermal detections in the window. There are no candidates because nothing was detected — not because candidates were reviewed and cleared.'
    case 'none-passing':
      return `${totals.detected} detection(s) were returned and ${totals.rejected} fell below the candidate threshold on the detector's own confidence. None is put forward as a candidate.`
  }
}

export interface CandidateTotals {
  detected: number
  candidates: number
  rejected: number
  unclassifiable: number
}

export function candidateTotals(candidates: HotspotCandidate[]): CandidateTotals {
  return {
    detected: candidates.length,
    candidates: candidates.filter((c) => c.status === 'candidate').length,
    rejected: candidates.filter((c) => c.status === 'rejected').length,
    unclassifiable: candidates.filter((c) => c.status === 'unclassifiable').length,
  }
}

/** GeoJSON for the candidate layer. `candidate_status` travels in the
 *  properties so the paint can distinguish a candidate from a rejected one
 *  without a second source. */
export function hotspotCandidatesFeatureCollection(
  candidates: HotspotCandidate[],
): FeatureCollection<Point, Record<string, string | number | null | boolean>> {
  return {
    type: 'FeatureCollection',
    features: candidates.map((candidate) => ({
      type: 'Feature',
      properties: {
        detection_id: candidate.detectionId,
        candidate_status: candidate.status,
        confidence_class: candidate.confidenceClass,
        confidence_raw: candidate.confidenceRaw,
        acquired_at: candidate.acquiredAt,
        satellite: candidate.satellite,
        daynight: candidate.daynight,
        frp_mw: candidate.frpMw,
        brightness_ti4_k: candidate.brightnessTi4K,
      },
      geometry: {
        type: 'Point',
        coordinates: [candidate.longitude, candidate.latitude],
      },
    })),
  }
}

function escape(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** Popup body for a clicked candidate. Leads with what the record is, then the
 *  evidence, then the gaps — in that order, so nobody reads a confidence
 *  percentage as a detection. */
export function hotspotCandidatePopupHtml(props: Record<string, unknown>): string {
  const status = String(props.candidate_status ?? 'unclassifiable') as CandidateStatus
  const frp = typeof props.frp_mw === 'number' ? `${props.frp_mw.toFixed(1)} MW` : 'not reported'
  const brightness =
    typeof props.brightness_ti4_k === 'number'
      ? `${props.brightness_ti4_k.toFixed(1)} K`
      : 'not reported'
  const overpass = props.daynight === 'N' ? 'night' : props.daynight === 'D' ? 'day' : 'unknown'

  return (
    `<strong>Imagery-derived candidate</strong>` +
    `<span class="fire-popup-source">Not a confirmed fire. One satellite thermal detection, put forward for review.</span>` +
    `<span>Status: <b>${escape(STATUS_LABEL[status])}</b> (detector confidence <b>${escape(props.confidence_class)}</b>, raw <b>${escape(props.confidence_raw)}</b>)</span>` +
    `<span>Acquired: <b>${escape(props.acquired_at)}</b> · ${escape(props.satellite)} · ${overpass} overpass</span>` +
    `<span>FRP: <b>${escape(frp)}</b> · Brightness: <b>${escape(brightness)}</b></span>` +
    `<span class="fire-popup-gap">Detector version, human review state and station corroboration are not reported by the API.</span>`
  )
}
