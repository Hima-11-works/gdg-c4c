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
// Three states, and the UI keeps them apart:
//   - no run recorded yet (`status: "no_federation_run"`)
//   - a recorded run (`succeeded` / `failed`)
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
