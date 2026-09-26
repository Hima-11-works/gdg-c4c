import type { AsyncResource } from '../hooks/useApiResource'
import type { MetaV2Out } from '../lib/types'

/**
 * F3: says out loud when the map is not showing a published run.
 *
 * The v2 envelope reports `is_fallback` and `is_stale`, but the main map view
 * showed neither. That mattered most for the fallback case: the API
 * synthesises a plausible run from stored grid rows when nothing has been
 * published, and its run id rolls over on the hour - so on the map it was
 * indistinguishable from a healthy hourly pipeline. Someone reading the map
 * had no way to know the "current" air quality was not a pipeline product.
 *
 * Ordered by what a reader most needs to know: a fallback means the data is
 * not from a run at all, so it outranks staleness, which only means the run is
 * old.
 */
export function ProvenanceBanner({
  meta,
}: {
  meta: AsyncResource<MetaV2Out>
}) {
  if (meta.status !== 'success') return null

  if (meta.data.is_fallback) {
    return (
      <p className="banner banner-demo" role="status">
        Illustrative data &mdash; not a published run. {meta.data.fallback_reason}
      </p>
    )
  }

  if (meta.data.is_stale) {
    return (
      <p className="banner banner-demo" role="status">
        Last published run is {formatAge(meta.data.age_seconds)} old &mdash; the
        hourly pipeline may not be running.
      </p>
    )
  }

  if (meta.data.is_demo) {
    return <p className="banner banner-demo">Demo data &mdash; illustrative, not measured.</p>
  }

  return null
}

function formatAge(seconds: number | null): string {
  if (seconds === null) return 'an unknown amount of time'
  const hours = seconds / 3600
  if (hours < 1) return `${Math.max(1, Math.round(seconds / 60))} min`
  if (hours < 48) return `${Math.round(hours)} h`
  return `${Math.round(hours / 24)} days`
}
