// Reading the federation demonstration's status honestly.
//
// The endpoint (GET /api/v1/federation/status) reports a *demonstration*: two
// disjoint partitions of one synthetic dataset, trained locally, exchanging
// model updates. The payload carries its own caveats — `region_scope`,
// `synthetic_only`, `evaluation.usable_as_real_world_evidence` and a
// `limitations` block — and those are the reason this module exists: every
// value the dashboard shows is read from the payload rather than summarised
// into a friendlier claim, so the screen cannot say "federated across India"
// when the server said "two partitions of one synthetic dataset".
//
// Four states, and the UI keeps them apart:
//   - no run recorded yet (`status: "no_federation_run"`)
//   - a completed run (`succeeded`)
//   - a recorded run that did not complete (`failed`) — a run that is *not* a
//     result, so it must not be presented with an aggregate or metrics
//   - the endpoint could not be read at all (handled by the caller, as an
//     error — a missing endpoint must never render as "no run").

import type {
  FederationEvaluationOut,
  FederationLimitationsOut,
  FederationModelIdsOut,
  FederationModelVersionOut,
  FederationParticipantOut,
  FederationStatusOut,
} from './types'

export const NO_RUN_STATUS = 'no_federation_run'

/** True when a demonstration run has actually been recorded. A type predicate
 *  so callers can narrow a possibly-null status to a recorded one. */
export function hasRun(
  status: FederationStatusOut | null | undefined,
): status is FederationStatusOut {
  return status !== null && status !== undefined && status.status !== NO_RUN_STATUS
}

export function participants(status: FederationStatusOut | null): FederationParticipantOut[] {
  return status?.participants ?? []
}

/** One model version, however much the payload said about it. */
export interface ModelVersionEntry {
  modelId: string
  /** The full record when the payload carried one; null when it carried only
   *  an id (a run read back from the database does), in which case the console
   *  shows the id and says the rest was not reported rather than inventing a
   *  status. */
  detail: FederationModelVersionOut | null
}

/** Model versions from either wire shape. The endpoint returns a list of
 *  objects for a freshly demonstrated run and `{"model_ids": [...]}` for a run
 *  read back from the database; this is the one place that difference is
 *  absorbed. */
export function modelVersions(status: FederationStatusOut | null): ModelVersionEntry[] {
  const raw = status?.model_versions
  if (raw === null || raw === undefined) return []
  if (Array.isArray(raw)) {
    return raw.map((entry) => ({ modelId: entry.model_id, detail: entry }))
  }
  const ids = (raw as FederationModelIdsOut).model_ids
  return Array.isArray(ids) ? ids.map((modelId) => ({ modelId, detail: null })) : []
}

export function evaluation(status: FederationStatusOut | null): FederationEvaluationOut | null {
  return status?.evaluation ?? null
}

export function limitations(status: FederationStatusOut | null): { key: string; text: string }[] {
  const block: FederationLimitationsOut | null | undefined = status?.limitations
  if (block === null || block === undefined) return []
  const entries: { key: string; text: string }[] = []
  if (block.privacy !== undefined) entries.push({ key: 'Privacy', text: block.privacy })
  if (block.geography !== undefined) entries.push({ key: 'Geography', text: block.geography })
  if (block.accuracy !== undefined) entries.push({ key: 'Accuracy', text: block.accuracy })
  return entries
}

/** When the aggregation last completed. `finished_at` is the aggregation time;
 *  a payload that only carries `started_at` reports that instead rather than
 *  inventing one. Null when neither is present. */
export function latestAggregationTime(status: FederationStatusOut | null): string | null {
  return status?.finished_at ?? status?.started_at ?? null
}

/** The one-line summary the pill carries. Deliberately flat: it states the
 *  count and the run status, and nothing that could be read as a coverage or
 *  privacy claim. */
export function headline(status: FederationStatusOut | null): string {
  if (status === null) return 'Federation: status unavailable'
  if (!hasRun(status)) return 'Federation: no demo run recorded'
  const count = status.participant_count ?? participants(status).length
  const regions = count === 1 ? '1 region' : `${count} regions`
  return `Federation demo: ${regions} · ${status.status}`
}

/** What the demonstration is, in its own words. */
export function scopeLine(status: FederationStatusOut | null): string {
  return status?.region_scope ?? 'two-partition-synthetic-demonstration'
}

/** Whether the aggregate is synthetic-only. Every run this demonstration can
 *  produce is, so `undefined` is reported as unknown rather than as false. */
export function syntheticOnly(status: FederationStatusOut | null): boolean | null {
  return status?.aggregate?.synthetic_only ?? null
}

/** The "is this real evidence?" answer, kept as the payload's own boolean. */
export function usableAsEvidence(status: FederationStatusOut | null): boolean | null {
  return evaluation(status)?.usable_as_real_world_evidence ?? null
}

// --- separate-client workflow: what the run actually was --------------------
//
// The demonstration is a *separate-client* workflow: each participant trained
// on its own partition and sent fitted parameters, never rows. Everything below
// is read from the payload so the panel reports the run that happened rather
// than the run the feature is about.

/** Whether the recorded run completed. `FederationRunStatus` is exactly
 *  `succeeded | failed`, so anything else is reported as unknown rather than
 *  being folded into success. */
export type FederationRunOutcome = 'succeeded' | 'failed' | 'unknown'

export function runOutcome(status: FederationStatusOut | null): FederationRunOutcome {
  if (status === null || !hasRun(status)) return 'unknown'
  if (status.status === 'succeeded') return 'succeeded'
  if (status.status === 'failed') return 'failed'
  return 'unknown'
}

/**
 * The participant count, kept as two numbers that are allowed to disagree.
 *
 * `participant_count` is what the run recorded; `participants.length` is how
 * many it actually listed. Silently preferring one would hide a real
 * inconsistency, so both are reported and the panel says when they differ.
 */
export interface ParticipantCount {
  /** The count the payload recorded, or null when it recorded none. */
  reported: number | null
  /** How many participants the payload actually listed. */
  listed: number
  /** False when a recorded count and the list contradict each other. */
  agrees: boolean
}

export function participantCount(status: FederationStatusOut | null): ParticipantCount {
  const listed = participants(status).length
  const reported =
    status?.participant_count === null || status?.participant_count === undefined
      ? null
      : status.participant_count
  return { reported, listed, agrees: reported === null || reported === listed }
}

/** Run timing, straight from the two timestamps the payload carries. */
export interface RunTiming {
  startedAt: string | null
  finishedAt: string | null
  /** Elapsed milliseconds, or null when either endpoint is missing/unparseable. */
  durationMs: number | null
}

export function runTiming(status: FederationStatusOut | null): RunTiming {
  const startedAt = status?.started_at ?? null
  const finishedAt = status?.finished_at ?? null
  const started = startedAt === null ? NaN : Date.parse(startedAt)
  const finished = finishedAt === null ? NaN : Date.parse(finishedAt)
  const durationMs =
    Number.isNaN(started) || Number.isNaN(finished) ? null : Math.max(0, finished - started)
  return { startedAt, finishedAt, durationMs }
}

export function formatDuration(durationMs: number | null): string {
  if (durationMs === null) return 'not recorded'
  const seconds = Math.round(durationMs / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

/**
 * Whether the run's labels were synthetic or observed.
 *
 * The payload states this in up to three places, and this demonstration is
 * synthetic-only, so they should agree. When they do not, the disagreement is
 * itself the finding and is reported as such rather than resolved in favour of
 * whichever source happens to be checked first.
 */
export type LabelBasis = 'synthetic' | 'observed' | 'unknown' | 'conflicting'

export interface LabelBasisReport {
  basis: LabelBasis
  /** The individual signals, for showing which one said what. */
  signals: { source: string; value: string }[]
}

export function labelBasis(status: FederationStatusOut | null): LabelBasisReport {
  const signals: { source: string; value: string }[] = []
  if (status === null) return { basis: 'unknown', signals }

  const aggregateFlag = status.aggregate?.synthetic_only
  if (typeof aggregateFlag === 'boolean') {
    signals.push({
      source: 'aggregate.synthetic_only',
      value: String(aggregateFlag),
    })
  }

  const evaluationStatus = evaluation(status)?.status
  if (typeof evaluationStatus === 'string' && evaluationStatus !== '') {
    signals.push({ source: 'evaluation.status', value: evaluationStatus })
  }

  const models = status.model_versions
  if (Array.isArray(models)) {
    const flagged = models.filter((model) => model.synthetic_only === true).length
    if (models.length > 0) {
      signals.push({
        source: 'model_versions.synthetic_only',
        value: `${flagged} of ${models.length} flagged synthetic`,
      })
    }
  }

  if (signals.length === 0) return { basis: 'unknown', signals }

  const saysSynthetic = signals.some(
    (signal) =>
      signal.value === 'true' ||
      signal.value === 'synthetic_evaluation_only' ||
      / of \d+ flagged synthetic/.test(signal.value),
  )
  const saysObserved = signals.some((signal) => signal.value === 'false')
  if (saysSynthetic && saysObserved) return { basis: 'conflicting', signals }
  if (saysSynthetic) return { basis: 'synthetic', signals }
  // Only an explicit `false` counts as observed. A signal that merely exists —
  // an evaluation status of `unavailable`, say — says nothing either way, and
  // must not be read as evidence that observed labels took part.
  if (saysObserved) return { basis: 'observed', signals }
  return { basis: 'unknown', signals }
}

/** The feature schema, read from wherever the payload put it. The persisted
 *  reader returns it at the top level, while the contract's post-run example
 *  carries it inside the aggregate block; a client that checked only one of the
 *  two would report a real value as "not recorded". */
export function featureSchemaVersion(status: FederationStatusOut | null): string | null {
  const top = status?.feature_schema_version
  if (typeof top === 'string' && top !== '') return top
  const nested = (status?.aggregate as { feature_schema_version?: unknown } | null | undefined)
    ?.feature_schema_version
  return typeof nested === 'string' && nested !== '' ? nested : null
}

/** The one-line statement of what the labels were, in the panel's own words. */
export function labelBasisSentence(report: LabelBasisReport): string {
  switch (report.basis) {
    case 'synthetic':
      return 'Synthetic labels only — no observed station labels took part in this run.'
    case 'observed':
      return 'Not marked synthetic by the payload; it does not claim real-world accuracy either way.'
    case 'conflicting':
      return 'The payload disagrees with itself about whether the labels were synthetic; see the signals below.'
    case 'unknown':
      return 'Not reported — the payload does not say whether the labels were synthetic or observed.'
  }
}

/** The model version ids, plus whether the payload described them. A run read
 *  back from the database carries ids only, and the panel says that rather than
 *  implying a status the payload never had. */
export function modelVersionSummary(status: FederationStatusOut | null): {
  ids: string[]
  described: boolean
} {
  const models = modelVersions(status)
  return {
    ids: models.map((model) => model.modelId),
    described: models.length > 0 && models.every((model) => model.detail !== null),
  }
}
