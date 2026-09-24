// One small hook for every backend fetch in the app (grid, forecast,
// weather, alerts, cell detail) instead of a data-fetching library —
// the app only ever needs "loading / success+isDemo / error" plus
// optional polling and manual retry, which this covers in ~40 lines.

import { useCallback, useEffect, useRef, useState } from 'react'
import type { DependencyList } from 'react'
import { ApiError } from '../lib/api'
import type { CoverageOut, DataMode, DatasetRefOut, Envelope } from '../lib/types'

export type AsyncResource<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | {
      status: 'success'
      data: T
      isDemo: boolean
      runId?: string
      mode?: DataMode
      generatedAt?: string
      /** The run's own coverage summary, straight off the v2 envelope — how
       *  many cells were asked for and how many came back. Null when the route
       *  doesn't send one. */
      coverage?: CoverageOut | null
      /** The datasets the backend attributed this run to. */
      attribution?: DatasetRefOut[]
    }
  | { status: 'error'; message: string }

interface UseApiResourceOptions {
  /** Re-fetch on this interval while the tab/component is mounted. */
  pollIntervalMs?: number
  /** Skip fetching entirely (e.g. no cell selected yet). */
  enabled?: boolean
}

export interface ApiResource<T> {
  resource: AsyncResource<T>
  refetch: () => void
}

export function useApiResource<T>(
  fetcher: () => Promise<Envelope<T>>,
  deps: DependencyList,
  { pollIntervalMs, enabled = true }: UseApiResourceOptions = {},
): ApiResource<T> {
  const [resource, setResource] = useState<AsyncResource<T>>({ status: 'idle' })
  const [reloadToken, setReloadToken] = useState(0)
  const fetcherRef = useRef(fetcher)

  // Keep the latest fetcher without making it a dependency of the effect
  // below (a new inline `() => fetchX(...)` every render must not
  // re-trigger a fetch) — assigned in an effect, not during render, so it
  // never runs while React is rendering.
  useEffect(() => {
    fetcherRef.current = fetcher
  })

  const refetch = useCallback(() => setReloadToken((token) => token + 1), [])

  useEffect(() => {
    if (!enabled) return

    let cancelled = false
    const load = (isBackgroundRefresh: boolean) => {
      // Only a background poll tick skips this: a fresh run (mount, or
      // any dependency change — e.g. a different cell selected) always
      // shows loading, rather than risking the *previous* dependency's
      // stale data being mistaken for the new one's while it's in flight.
      if (!isBackgroundRefresh) setResource({ status: 'loading' })
      fetcherRef
        .current()
        .then((envelope) => {
          if (!cancelled)
            setResource({
              status: 'success',
              data: envelope.data,
              isDemo: envelope.is_demo,
              runId: envelope.run_id,
              mode: envelope.mode,
              generatedAt: envelope.generated_at,
              coverage: envelope.coverage,
              attribution: envelope.attribution,
            })
        })
        .catch((error: unknown) => {
          // A failed background refresh keeps showing the last good data
          // rather than replacing it with an error banner.
          if (cancelled || isBackgroundRefresh) return
          const message = error instanceof ApiError ? error.message : 'Unknown error'
          setResource({ status: 'error', message })
        })
    }

    load(false)
    // Skipped while the tab isn't visible — a background tab has no map
    // to update, so there's no point re-fetching grid/weather/alerts
    // every pollIntervalMs while the user is elsewhere; picks back up on
    // its own next tick once the tab is visible again (no need to fetch
    // immediately on visibilitychange — the existing interval is close
    // enough, and this stays a one-line check rather than a second
    // effect/listener).
    const interval = pollIntervalMs
      ? setInterval(() => {
          if (document.visibilityState === 'visible') load(true)
        }, pollIntervalMs)
      : undefined

    return () => {
      cancelled = true
      if (interval) clearInterval(interval)
    }
    // `deps` is caller-supplied (mirrors useEffect's own API) so this
    // hook can re-fetch when e.g. a selected cell id changes — a linter
    // can't statically verify a spread of an arbitrary-length caller-
    // supplied array, but the array itself is still recomputed fresh
    // every render like any other dependency list, so this is safe.
  }, [enabled, pollIntervalMs, reloadToken, ...deps])

  return { resource, refetch }
}
