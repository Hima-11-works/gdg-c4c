// Candidate hotspots, read from the detector's recorded scans.
//
// The contract (docs/api/hotspots.md) is unusually explicit about what a
// candidate is not, and this module exists to keep the UI inside those limits:
//
//   imagery is the only trigger  a FIRMS detection or a station reading raises
//                                confidence and is recorded as evidence, but
//                                neither can create a candidate
//   pm25_ugm3 is always null      a candidate is a location, not a concentration
//   unattributed                 imagery cannot say who or what caused it
//   pending_human_review         nothing here confirms, dismisses or notifies
//   confidence is a bounded sum  a triage ordering, not a probability
//
// The detector's own `confidence` band and `confidence_basis` are shown, because
// hiding the arithmetic would leave a number looking like a probability.

import type {
  HotspotCandidateOut,
  HotspotScanOut,
  HotspotScanRowOut,
} from './types'
import type { FeatureCollection, Point } from 'geojson'

/** A scan that could not look, as opposed to one that looked and found nothing.
 *  The contract keeps these apart and so must the panel. */
export const INSUFFICIENT_VERDICT = 'insufficient_evidence'

export function isInsufficientScan(scan: HotspotScanRowOut): boolean {
  return scan.verdict === INSUFFICIENT_VERDICT
}

const CONFIDENCE_ORDER: Record<string, number> = { low: 0, medium: 1, high: 2 }

export function confidenceRank(candidate: HotspotCandidateOut): number {
  return CONFIDENCE_ORDER[candidate.confidence.toLowerCase()] ?? -1
}

/** The confidence band with its score, as a phrase that cannot be misread as a
 *  probability: the band and the bounded total, never "75% likely". */
export function confidencePhrase(candidate: HotspotCandidateOut): string {
  return `${candidate.confidence} (${candidate.confidence_score.toFixed(2)} of a 0.90 ceiling)`
}

/** What the review state actually is. The contract has three values and only
 *  ever emits the first, so anything else - or a candidate that claims a
 *  non-pending state without a reviewer - is called out rather than trusted. */
export function reviewPhrase(candidate: HotspotCandidateOut): string {
  if (candidate.review_status === 'pending_human_review') {
    return 'Pending human review — no reviewer has looked at this yet.'
  }
  if (candidate.reviewed_by === null || candidate.reviewed_at === null) {
    return `Claims review state "${candidate.review_status}" but names no reviewer or time — not trustworthy.`
  }
  return `${candidate.review_status} by ${candidate.reviewed_by} at ${candidate.reviewed_at}.`
}

/** Whether a scan's evaluation may be quoted as anything about performance.
 *  The contract is unambiguous: `usable_as_real_world_evidence` is always
 *  false, and precision/recall here describe the detector against authored
 *  labels. This returns the sentence the panel prints, not a judgement. */
export function evaluationCaveat(scan: HotspotScanOut): string {
  const e = scan.evaluation
  const where = e.label_provenance === 'authored-fixture'
    ? 'authored fixture labels, which measure this detector against a known answer'
    : `labels of provenance "${e.label_provenance}"`
  return `Measured against ${where}. Not evidence of real-world performance.`
}

export function metricOrNotComputed(value: number | null): string {
  return value === null ? 'not computed' : value.toFixed(2)
}

/** The GeoJSON the map draws. Hollow, dashed and cyan in MapView; the
 *  properties here carry everything the popup and panel quote, so all three
 *  read from one record. */
export function hotspotCandidatesFeatureCollection(
  candidates: HotspotCandidateOut[],
): FeatureCollection<Point, Record<string, string | number | null | string[]>> {
  return {
    type: 'FeatureCollection',
    features: candidates.map((candidate) => ({
      type: 'Feature',
      properties: {
        candidate_id: candidate.candidate_id,
        h3_cell: candidate.h3_cell,
        confidence: candidate.confidence,
        confidence_score: candidate.confidence_score,
        detector_version: candidate.detector_version,
        acquired_at: candidate.acquired_at,
        review_status: candidate.review_status,
        supporting_sources: candidate.supporting_sources,
        evidence_count: candidate.evidence.length,
        index_value: candidate.index_value,
        pm25_ugm3: candidate.pm25_ugm3,
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

/** Popup for a clicked candidate. Leads with what the record is not, because a
 *  confidence band next to a map is the easiest thing on this screen to
 *  misread as a measurement. */
export function hotspotCandidatePopupHtml(props: Record<string, unknown>): string {
  const sources = Array.isArray(props.supporting_sources)
    ? (props.supporting_sources as string[]).join(' + ')
    : 'not reported'
  return (
    `<strong>Candidate hotspot — for human review</strong>` +
    `<span class="fire-popup-source">Not a PM2.5 value, not a confirmed fire, no source attributed.</span>` +
    `<span>Confidence: <b>${escape(props.confidence)}</b> (bounded ${escape(props.confidence_score)})</span>` +
    `<span>Review: <b>${escape(props.review_status)}</b></span>` +
    `<span>Acquired: <b>${escape(props.acquired_at)}</b> · detector <b>${escape(props.detector_version)}</b></span>` +
    `<span>Supporting: <b>${escape(sources)}</b> · ${escape(props.evidence_count)} evidence item(s)</span>`
  )
}
