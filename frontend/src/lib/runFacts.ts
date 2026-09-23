// Everything the dashboard can honestly say about the published run it is
// currently showing, derived from that run's own response envelope and cells.
//
// The point of keeping this in one pure function is that "which run am I
// looking at, and how fresh is it" has to be answered the same way by the
// status panel, the empty states and the banner. Reading the envelope's
// coverage and each cell's metadata in several places is how those three
// drift apart.
//
// Nothing here computes a pollution value. Every number is a field the
// backend already sent: run time is the envelope's `generated_at`, source age
// is `metadata.quality.max_observation_age_hours`, coverage is the envelope's
// `coverage`, and the prediction method is `metadata.prediction_method`. The
// only arithmetic is picking the oldest observation age across cells and
// counting how many cells carry a population-weighted value.

import { STALE_RUN_HOURS, STALE_SOURCE_HOURS, ageInHours } from './format'
import type { CoverageOut, DataMode, DatasetRefOut, GridStateOut, InputKind } from './types'

/** What the current read says about population, which is what decides whether
 *  the exposure map mode has anything to draw. */
export interface PopulationFact {
  /** Cells in this read carrying a population-weighted concentration. */
  cellsWithExposure: number
  /** Cells in this read in total (including cells with no estimate). */
  cellsTotal: number
  /** True when at least one cell carries a population-weighted value. */
  available: boolean
  /** The population dataset the backend attributed, when it named one. */
  datasetVersion: string | null
  /** Residents inside cells that have both a population and a prediction. */
  coveredPopulation: number
  /** Residents in cells with a population but no prediction for this frame. */
  unknownPopulation: number | null
}

export interface RunFacts {
  runId?: string
  mode?: DataMode
  generatedAt?: string
  coverage: CoverageOut | null
  /** Distinct prediction methods across the read — usually one, listed in full
   *  when a read mixes them rather than picking one and implying it is all. */
  predictionMethods: string[]
  modelVersion: string | null
  inputKind: InputKind | null
  featureSchemaVersion: string | null
  /** Age of the newest observation behind the run, in hours. Null when the
   *  backend reported none — which is "unknown", not "fresh". */
  sourceAgeHours: number | null
  observedStationCount: number | null
  missingFields: string[]
  warnings: string[]
  /** The datasets the backend attributed this run to. */
  attribution: DatasetRefOut[]
  population: PopulationFact
}

const EMPTY_POPULATION: PopulationFact = {
  cellsWithExposure: 0,
  cellsTotal: 0,
  available: false,
  datasetVersion: null,
  coveredPopulation: 0,
  unknownPopulation: null,
}

export const EMPTY_RUN_FACTS: RunFacts = {
  coverage: null,
  predictionMethods: [],
  modelVersion: null,
  inputKind: null,
  featureSchemaVersion: null,
  sourceAgeHours: null,
  observedStationCount: null,
  missingFields: [],
  warnings: [],
  attribution: [],
  population: EMPTY_POPULATION,
}

interface RunEnvelopeFacts {
  runId?: string
  mode?: DataMode
  generatedAt?: string
  coverage?: CoverageOut | null
  attribution?: DatasetRefOut[]
}

export function runFactsFromGrid(
  cells: GridStateOut[],
  envelope: RunEnvelopeFacts,
): RunFacts {
  const methods = new Set<string>()
  const models = new Set<string>()
  const missing = new Set<string>()
  const warnings = new Set<string>()
  let schema: string | null = null
  let inputKind: InputKind | null = null
  let sourceAgeHours: number | null = null
  let stations = 0

  let cellsWithExposure = 0
  let coveredPopulation = 0
  let unknownPopulation: number | null = null
  let datasetVersion: string | null = null

  for (const cell of cells) {
    const metadata = cell.metadata
    if (metadata !== undefined) {
      methods.add(metadata.prediction_method)
      if (metadata.model_version !== null) models.add(metadata.model_version)
      schema ??= metadata.feature_schema_version
      inputKind ??= metadata.input_kind
      for (const field of metadata.quality.missing_fields) missing.add(field)
      for (const warning of metadata.quality.warnings) warnings.add(warning)
      stations += metadata.quality.observed_station_count
      const age = metadata.quality.max_observation_age_hours
      // The oldest observation is what bounds the run's freshness, so keep the
      // maximum rather than the newest cell's (a single stale station still
      // means part of the field is stale).
      if (age !== null && (sourceAgeHours === null || age > sourceAgeHours)) {
        sourceAgeHours = age
      }
    }

    const exposure = cell.exposure
    if (exposure !== null && exposure !== undefined) {
      if (exposure.population_weighted_pm25 !== null) cellsWithExposure += 1
      coveredPopulation += exposure.covered_population
      if (exposure.unknown_population !== null) {
        unknownPopulation = (unknownPopulation ?? 0) + exposure.unknown_population
      }
      datasetVersion ??= exposure.population_dataset_version
    }
  }

  return {
    runId: envelope.runId,
    mode: envelope.mode,
    generatedAt: envelope.generatedAt,
    coverage: envelope.coverage ?? null,
    predictionMethods: [...methods].sort(),
    modelVersion: models.size === 0 ? null : [...models].sort().join(', '),
    inputKind,
    featureSchemaVersion: schema,
    sourceAgeHours,
    observedStationCount: cells.length === 0 ? null : stations,
    missingFields: [...missing].sort(),
    warnings: [...warnings].sort(),
    attribution: envelope.attribution ?? [],
    population: {
      cellsWithExposure,
      cellsTotal: cells.length,
      available: cellsWithExposure > 0,
      datasetVersion,
      coveredPopulation,
      unknownPopulation,
    },
  }
}

export interface Staleness {
  stale: boolean
  /** Why it was called stale, in the order the UI should read them out. */
  reasons: string[]
  runAgeHours: number | null
}

/** Whether the run (or the observations behind it) is old enough that the
 *  dashboard should say so. Two independent signals, because they fail
 *  separately: a run can be published minutes ago from week-old observations,
 *  and a fresh observation can sit in a run published days ago. */
export function staleness(facts: RunFacts, now: Date = new Date()): Staleness {
  const reasons: string[] = []
  const runAgeHours = ageInHours(facts.generatedAt, now)

  if (runAgeHours !== null && runAgeHours > STALE_RUN_HOURS) {
    reasons.push(
      `This run was published ${Math.round(runAgeHours)}h ago (over ${STALE_RUN_HOURS}h old).`,
    )
  }
  if (facts.sourceAgeHours !== null && facts.sourceAgeHours > STALE_SOURCE_HOURS) {
    reasons.push(
      `The newest observation behind it is ${Math.round(facts.sourceAgeHours)}h old (over ${STALE_SOURCE_HOURS}h).`,
    )
  }

  return { stale: reasons.length > 0, reasons, runAgeHours }
}

/** The sentence the exposure empty state shows. Kept here, next to the fact it
 *  explains, so the wording can't claim population is missing when the real
 *  problem is that this particular frame has no predictions. */
export function missingPopulationMessage(facts: RunFacts): string {
  const { population } = facts
  if (population.cellsTotal === 0) {
    return 'No cells were returned for this view, so there is nothing to weight by population.'
  }
  if (population.datasetVersion === null && population.coveredPopulation === 0) {
    return (
      'This published run carries no population estimates, so no cell has a ' +
      'population-weighted concentration to show. The PM2.5 layer is unaffected — ' +
      'switch back to it to see the run’s concentrations.'
    )
  }
  return (
    'None of the cells in this view have a population-weighted concentration for this ' +
    'frame. Population is known for some cells, but only cells with both a population ' +
    'and a prediction can be weighted.'
  )
}
