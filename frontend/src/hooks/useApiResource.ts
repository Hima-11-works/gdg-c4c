// One small hook for every backend fetch in the app (grid, forecast,
// weather, alerts, cell detail) instead of a data-fetching library —
// the app only ever needs "loading / success+isDemo / error" plus
// optional polling and manual retry, which this covers in ~40 lines.

import { useCallback, useEffect, useRef, useState } from 'react'
import type { DependencyList } from 'react'
import { ApiError } from '../lib/api'
import type { DataMode, Envelope } from '../lib/types'

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
    }
  | { status: 'error'; message: string }

export type ApiResourceProgress = (loaded: number, total: number) => void

interface UseApiResourceOptions {
  /** Re-fetch on this interval while the tab/component is mounted. */
  pollIntervalMs?: number
  /** Skip fetching entirely (e.g. no cell selected yet). */
  enabled?: boolean
  /** Report foreground streaming progress; background refreshes stay quiet. */
  onProgress?: ApiResourceProgress
}

export interface ApiResource<T> {
  resource: AsyncResource<T>
  refetch: () => void
  cancel: () => void
}

export function useApiResource<T>(
  fetcher: (signal: AbortSignal, onProgress?: ApiResourceProgress) => Promise<Envelope<T>>,
  deps: DependencyList,
  { pollIntervalMs, enabled = true, onProgress }: UseApiResourceOptions = {},
): ApiResource<T> {
  const [resource, setResource] = useState<AsyncResource<T>>({ status: 'idle' })
  const [reloadToken, setReloadToken] = useState(0)
  const fetcherRef = useRef(fetcher)
  const progressRef = useRef(onProgress)
  const activeControllerRef = useRef<AbortController | null>(null)

  // Keep the latest fetcher without making it a dependency of the effect
  // below (a new inline `() => fetchX(...)` every render must not
  // re-trigger a fetch) — assigned in an effect, not during render, so it
  // never runs while React is rendering.
  useEffect(() => {
    fetcherRef.current = fetcher
  })
  useEffect(() => {
    progressRef.current = onProgress
  })

  const refetch = useCallback(() => setReloadToken((token) => token + 1), [])
  const cancel = useCallback(() => {
    const controller = activeControllerRef.current
    if (!controller) return
    activeControllerRef.current = null
    controller.abort()
    setResource((current) => (current.status === 'loading' ? { status: 'idle' } : current))
  }, [])

  useEffect(() => {
    if (!enabled) return

    let cancelled = false
    let activeController: AbortController | null = null
    const load = (isBackgroundRefresh: boolean) => {
      // A slow poll must not overlap the next poll or a newer viewport load.
      activeController?.abort()
      const controller = new AbortController()
      activeController = controller
      activeControllerRef.current = controller
      // Only a background poll tick skips this: a fresh run (mount, or
      // any dependency change — e.g. a different cell selected) always
      // shows loading, rather than risking the *previous* dependency's
      // stale data being mistaken for the new one's while it's in flight.
      if (!isBackgroundRefresh) setResource({ status: 'loading' })
      fetcherRef
        .current(controller.signal, isBackgroundRefresh ? undefined : progressRef.current)
        .then((envelope) => {
          if (!cancelled && !controller.signal.aborted)
            setResource({
              status: 'success',
              data: envelope.data,
              isDemo: envelope.is_demo,
              runId: envelope.run_id,
              mode: envelope.mode,
              generatedAt: envelope.generated_at,
            })
        })
        .catch((error: unknown) => {
          // A failed background refresh keeps showing the last good data
          // rather than replacing it with an error banner.
          if (cancelled || controller.signal.aborted || isBackgroundRefresh) return
          const message = error instanceof ApiError ? error.message : 'Unknown error'
          setResource({ status: 'error', message })
        })
        .finally(() => {
          if (activeController === controller) activeController = null
          if (activeControllerRef.current === controller) activeControllerRef.current = null
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
      activeController?.abort()
      if (activeControllerRef.current === activeController) activeControllerRef.current = null
      if (interval) clearInterval(interval)
    }
    // `deps` is caller-supplied (mirrors useEffect's own API) so this
    // hook can re-fetch when e.g. a selected cell id changes — a linter
    // can't statically verify a spread of an arbitrary-length caller-
    // supplied array, but the array itself is still recomputed fresh
    // every render like any other dependency list, so this is safe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, pollIntervalMs, reloadToken, ...deps])

  return { resource, refetch, cancel }
}
