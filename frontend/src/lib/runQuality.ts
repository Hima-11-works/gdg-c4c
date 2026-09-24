// What a published run can honestly be told is missing, in words.
//
// The backend reports two lists per cell and the run aggregates them:
// `quality.missing_fields` (a feature the run does not have) and
// `quality.warnings` (something the run had but had to flag). Both are
// collected in lib/runFacts.ts and neither was displayed, which made a run
// built from a failed fire feed look exactly like a complete one.
//
// The field names are the backend's own vocabulary (see FeatureBuilder's
// `missing` set), so this module only maps them to something a reader can act
// on. A name it does not recognise is passed through unchanged rather than
// hidden, because an unrecognised field is still a real missing input.

import { formatCount } from './format'
import type { RunFacts } from './runFacts'

/** `missing_fields` as the backend names them, in plain words. */
const MISSING_FIELD_LABELS: Record<string, string> = {
  pollution: 'PM2.5 observations',
  weather: 'weather',
  traffic: 'traffic',
  population: 'population',
  roads: 'road network',
  land_cover: 'land cover',
  fires: 'fire detections',
  observed_station_count: 'station observations',
}

export function missingFieldLabel(field: string): string {
  return MISSING_FIELD_LABELS[field] ?? field
}

/** Machine warning tokens, in words. A warning that is already a sentence is
 *  shown verbatim — the backend writes some of them that way deliberately. */
const WARNING_LABELS: Record<string, string> = {
  pollution_stale: 'PM2.5 observations are past their freshness window',
  weather_stale: 'weather observations are past their freshness window',
  weather_forecast_gap: 'forecast weather for some horizons is older than the horizon it describes',
  rain_history_incomplete: 'rain history is incomplete, so no 24-hour total',
}

/** True for a machine token rather than a sentence the backend wrote. */
function isWarningToken(warning: string): boolean {
  return /^[a-z0-9_]+$/.test(warning)
}

export function warningLabel(warning: string): string {
  return isWarningToken(warning)
    ? (WARNING_LABELS[warning] ?? `flagged by the backend: ${warning}`)
    : warning
}

export interface MissingInput {
  field: string
  label: string
}

/** The run's missing inputs, deduplicated, in a stable order. */
export function missingInputs(facts: RunFacts): MissingInput[] {
  return [...new Set(facts.missingFields)]
    .sort()
    .map((field) => ({ field, label: missingFieldLabel(field) }))
}

export function warningLines(facts: RunFacts): string[] {
  return [...new Set(facts.warnings)].sort().map(warningLabel)
}

/** Whether there is anything to report at all. */
export function hasInputGaps(facts: RunFacts): boolean {
  return missingInputs(facts).length > 0 || warningLines(facts).length > 0
}

/**
 * Spatial coverage in one sentence, naming what is *outside* the run.
 *
 * The count matters because of what is not in the response: a run covering a
 * fraction of the country simply has no rows for the rest, so that area is bare
 * basemap on screen. Saying how many cells are outside the run is what stops
 * that being read as clean air.
 */
export function coverageSentence(facts: RunFacts): string | null {
  const coverage = facts.coverage
  if (coverage === null) return null
  const outside = coverage.requested_cells - coverage.returned_cells
  if (outside <= 0) {
    return `All ${formatCount(coverage.requested_cells)} cells in this view are inside the run.`
  }
  return (
    `${formatCount(outside)} of ${formatCount(coverage.requested_cells)} cells in this view are ` +
    `outside this run and are not drawn — that area is basemap, not clean air.`
  )
}

/** How many cells the run returned carry no estimate at all. */
export function cellsWithoutEstimate(facts: RunFacts): number {
  return Math.max(0, facts.population.cellsTotal - facts.population.cellsWithExposure)
}

/**
 * Why the source age may be absent.
 *
 * "Not reported" alone is a dead end for a reader: it says the dashboard does
 * not know, without saying whether that is a gap in the run or a run that never
 * had live observations at all. The two are different, and the run's own inputs
 * say which.
 */
export function sourceAgeNote(facts: RunFacts): string {
  if (facts.sourceAgeHours !== null) {
    return facts.sourceAgeHours === 0
      ? 'Observations in this run are current to within the hour.'
      : ''
  }
  if (facts.missingFields.includes('pollution')) {
    return 'No PM2.5 observation feeds this run, so it has no source age to report.'
  }
  if (facts.inputKind === 'synthetic') {
    return 'This run is synthetic: its values come from a scenario, not from observations, so there is no source age.'
  }
  return 'The backend reported no source age for this run.'
}

/** Unavailable horizons, from what the run actually published. */
export function unavailableHorizonNote(publishedHours: number[] | undefined): string | null {
  if (publishedHours === undefined || publishedHours.length === 0) return null
  const hours = [...publishedHours].sort((a, b) => a - b)
  const last = hours[hours.length - 1]
  return (
    `This run published +${hours.map((h) => `${h}h`).join(', +')} and nothing beyond ` +
    `+${last}h, so the timeline stops there.`
  )
}
