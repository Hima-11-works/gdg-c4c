// Corridor pollution events and how their forecasts were scored.
//
// The rule this module exists to keep: a forecast may only be called accurate
// against real, withheld station observations. When those observations are
// absent the answer is an explicit insufficient-data conclusion naming the gap —
// never a number presented as accuracy. That is why `verdict` is read before any
// metric is touched, and why a metric is only ever shown when its own slice says
// `sufficient`.
//
// Three kinds of thing are kept visibly apart, because conflating any two of
// them would be the exact error the contract exists to prevent:
//
//   measured evidence  station observations the forecaster could not have seen
//   model predictions  the published run's forecast PM2.5 over the cells
//   illustrative       the geometry: a straight line between two city points,
//                      explicitly NOT a road route
//
// See docs/api/corridor-evaluation.md.

import type {
  CorridorCatalogEntry,
  CorridorEventBundle,
  CorridorEvaluation,
  CorridorSlice,
} from './types'

/** The geometry is a straight-line sampling until a route dataset is supplied.
 *  `routed` exists in the contract so a sourced route can arrive without
 *  breaking consumers — and until it does, it must keep reading as a caution. */
export const GEOMETRY_LABEL: Record<string, string> = {
  illustrative: 'Illustrative geometry',
  routed: 'Routed geometry',
}

export function isIllustrativeGeometry(source: string | null | undefined): boolean {
  return source !== 'routed'
}

/**
 * What the evaluation concluded, in words.
 *
 * `insufficient_data` is a *result*, not a failure to compute: the service
 * looked, found the labels were not there, and says so. Rendering it as an
 * error or as a zero would both be wrong — a zero would be a performance claim.
 */
export function verdictLabel(verdict: CorridorEvaluation['verdict']): string {
  return verdict === 'evaluated'
    ? 'Evaluated against real station observations'
    : 'Insufficient data — no performance claim can be made'
}

export function verdictExplanation(evaluation: CorridorEvaluation): string {
  if (evaluation.verdict === 'evaluated') {
    return (
      `Every requested horizon and geography slice had at least ${evaluation.min_labels} ` +
      'real station observations, so the metrics below are quotable.'
    )
  }
  return (
    'The forecast could not be scored against real observations. That is a finding, not a ' +
    'gap in the calculation: no metric is shown, because any number here would describe the ' +
    'scenario rather than the world.'
  )
}

/** Slices that may be quoted, and the ones that must not be. */
export function quotableSlices(evaluation: CorridorEvaluation): CorridorSlice[] {
  return evaluation.slices.filter((slice) => slice.sufficient)
}

export function unquotableSlices(evaluation: CorridorEvaluation): CorridorSlice[] {
  return evaluation.slices.filter((slice) => !slice.sufficient)
}

/** Whether any metric at all is quotable. */
export function hasQuotableMetrics(evaluation: CorridorEvaluation): boolean {
  return evaluation.verdict === 'evaluated' && quotableSlices(evaluation).length > 0
}

/** A coverage fraction as a short phrase, or null when nothing was scored. */
export function coveragePhrase(evaluation: CorridorEvaluation): string {
  const coverage = evaluation.coverage
  if (coverage.cells_scored === 0) {
    return `No corridor cell could be scored (${coverage.cells_with_forecast} had a forecast, ` +
      `${coverage.cells_with_labels} had an observation).`
  }
  return (
    `${coverage.cells_scored} of ${coverage.corridor_cells} corridor cells scored, ` +
    `${coverage.horizons_scored} of ${coverage.requested_horizons} horizons.`
  )
}

/** Peak forecast, always attributed to the model and to a horizon. */
export function peakPhrase(event: CorridorEventBundle['event']): string {
  if (event.peak_predicted_ugm3 === null) return 'No forecast peak in this run.'
  const at = event.peak_horizon_hours === null ? '' : ` at +${event.peak_horizon_hours}h`
  return `${event.peak_predicted_ugm3.toFixed(1)} µg/m³${at} (modelled)`
}

/** The geography an event affects, in the words the catalog uses. */
export function geographyPhrase(
  catalog: CorridorCatalogEntry,
  event: CorridorEventBundle['event'] | null,
): string {
  const from = catalog.endpoints[0]
  const to = catalog.endpoints[catalog.endpoints.length - 1]
  const base = `${from.label} → ${to.label}, ${catalog.cell_count} H3 res-${catalog.h3_resolution} cells`
  return event === null ? base : `${base} · ${event.cell_count} cells in this run`
}

/** A compact, sortable-friendly horizon label: "+3h", "+90m". */
export function horizonLabel(hours: number): string {
  if (Number.isInteger(hours)) return `+${hours}h`
  const minutes = Math.round(hours * 60)
  return minutes >= 60 ? `+${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ''}` : `+${minutes}m`
}

/**
 * Which kind of evidence a label came from, as far as the contract can tell.
 *
 * The service refuses to score a synthetic station, so a label source the
 * backend counted but did not score is worth naming rather than counting quietly.
 */
export function labelProvenanceLabel(provenance: string): string {
  if (provenance === 'none' || provenance === '') return 'No observations found'
  return provenance
}

/** A type guard kept local so the shape lives with the module that reads it. */
export function isCorridorEventBundle(value: unknown): value is CorridorEventBundle {
  return (
    typeof value === 'object' &&
    value !== null &&
    'event' in value &&
    'evaluation' in value
  )
}
