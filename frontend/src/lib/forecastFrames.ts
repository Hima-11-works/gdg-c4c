// Shared forecast-frame store. TimelineControl prefetches frames into it,
// MapPage reads them out of it — one cache, so playback never triggers a
// second fetch and never flips a loading state for an already-prefetched
// keyframe.
//
// `useForecastFrame` returns the *previous* frame's data (stale-while-
// revalidate) while a not-yet-cached frame loads, so the map keeps showing
// valid data during playback instead of blanking/flashing a spinner every
// 750ms keyframe change.

import { useEffect, useMemo, useState } from 'react'
import { fetchGridForecast } from './api'
import type { LodQuery } from './api'
import type { DataMode, Envelope, ForecastOut } from './types'

export interface FrameState {
  status: 'loading' | 'success' | 'error'
  /** The requested frame's data, or the most recently loaded frame's data
   * while the requested one is still loading (stale-while-revalidate).
   * Undefined only on first load or after a failure with no prior data. */
  data: ForecastOut[] | undefined
  isDemo: boolean
  runId?: string
  mode?: DataMode
  generatedAt?: string
  message?: string
}

const MAX_MINUTES = 360
const DEFAULT_HORIZONS_HOURS = [1, 3, 6]

function keyframesFor(horizonsHours: number[] = DEFAULT_HORIZONS_HOURS): number[] {
  const maxMinutes = Math.max(
    0,
    ...horizonsHours
      .filter((hours) => hours > 0 && hours <= MAX_MINUTES / 60)
      .map((hours) => Math.floor((hours * 60) / 15) * 15),
  )
  return Array.from({ length: maxMinutes / 15 + 1 }, (_, index) => index * 15)
}

/** How many keyframes ahead of the current position a view warm-up caches.
 * At the 750ms playback cadence this is ~6s of buffered frames — enough
 * that playback never advances onto an uncached frame after a warm-up. */
export const WARM_WINDOW = 8

const cache = new Map<string, Envelope<ForecastOut[]>>()
interface InFlightForecast {
  queryKey: string
  controller: AbortController
  promise: Promise<void>
}
const inFlight = new Map<string, InFlightForecast>()
const failed = new Set<string>()
const listeners = new Set<() => void>()

// Explicit warm-up operations, keyed by view (queryKey). Present only while
// a batch of frames is being fetched to make a fresh view playable — the
// loading banner and the disabled Play/Restart buttons key off this, NOT off
// any in-flight fetch, so ordinary playback prefetch never disables play.
const warmups = new Map<string, { pending: number }>()

function notify(): void {
  for (const listener of listeners) listener()
}

function cacheKey(minutes: number, queryKey: string): string {
  return `${minutes}:${queryKey}`
}

/** Start a fetch for this frame if it isn't cached or in flight. Returns a
 *  promise that settles when the frame is cached (or the fetch fails). */
export function ensureForecastFrame(
  minutes: number,
  queryKey: string,
  query: LodQuery,
  runId?: string,
): Promise<void> {
  const key = cacheKey(minutes, queryKey)
  if (cache.has(key)) return Promise.resolve()
  const existing = inFlight.get(key)
  if (existing) return existing.promise
  failed.delete(key)
  const controller = new AbortController()
  const request: InFlightForecast = {
    queryKey,
    controller,
    promise: Promise.resolve(),
  }
  request.promise = fetchGridForecast(minutes, query, runId, controller.signal)
    .then((envelope) => {
      if (controller.signal.aborted || inFlight.get(key) !== request) return
      cache.set(key, envelope)
      inFlight.delete(key)
      failed.delete(key)
      notify()
    })
    .catch(() => {
      if (inFlight.get(key) !== request) return
      inFlight.delete(key)
      if (!controller.signal.aborted) failed.add(key)
      notify()
    })
  inFlight.set(key, request)
  return request.promise
}

/** Abort active frame reads belonging to a viewport that has just changed. */
export function cancelForecastRequests(queryKey: string): void {
  let cancelledAny = false
  for (const [key, request] of inFlight) {
    if (request.queryKey !== queryKey) continue
    request.controller.abort()
    inFlight.delete(key)
    failed.delete(key)
    cancelledAny = true
  }
  const hadWarmup = warmups.delete(queryKey)
  if (cancelledAny || hadWarmup) notify()
}

/**
 * Warm the frames a fresh view needs for smooth playback: the current
 * position plus the next WARM_WINDOW keyframes. Records an explicit warm-up
 * so `useForecastWarming` reports true and the UI can show a loading state
 * and disable playback until every frame is cached.
 *
 * A no-op if the view is already warming (the queryKey changes on a view
 * change, so a new view starts its own warm-up) or the window is all cached.
 */
export function warmForecastWindow(
  queryKey: string,
  query: LodQuery,
  fromMinutes: number,
  supportedHorizonsHours: number[] = DEFAULT_HORIZONS_HOURS,
  runId?: string,
): void {
  if (warmups.has(queryKey)) return

  const keyframes = keyframesFor(supportedHorizonsHours)
  const idx = keyframes.indexOf(fromMinutes)
  if (idx < 0) return

  // minutes=0 is "Now", served by /grid/current, not a forecast frame — and
  // a 0-minute forecast is rejected by the API. Start at the next keyframe.
  const startIdx = idx === 0 ? 1 : idx

  const targets: number[] = []
  for (let i = startIdx; i <= idx + WARM_WINDOW; i++) {
    const minutes = keyframes[i]
    if (minutes === undefined) break
    targets.push(minutes)
  }

  const missing = targets.filter((minutes) => !cache.has(cacheKey(minutes, queryKey)))
  if (missing.length === 0) return

  const warmup = { pending: missing.length }
  warmups.set(queryKey, warmup)
  notify()

  for (const minutes of missing) {
    ensureForecastFrame(minutes, queryKey, query, runId).finally(() => {
      // The viewport may have changed while this batch was in flight. Its
      // completion must not clear a newer warm-up for the same query key.
      if (warmups.get(queryKey) !== warmup) return
      warmup.pending -= 1
      if (warmup.pending <= 0) {
        warmups.delete(queryKey)
        notify()
      }
    })
  }
}

/** Whether a view warm-up is currently in progress. */
export function useForecastWarming(queryKey: string): boolean {
  const [warming, setWarming] = useState(() => warmups.has(queryKey))
  useEffect(() => {
    const update = () => setWarming(warmups.has(queryKey))
    listeners.add(update)
    update()
    return () => {
      listeners.delete(update)
    }
  }, [queryKey])
  return warming
}

/** Synchronously read a cached frame, or undefined. */
export function peekForecastFrame(
  minutes: number,
  queryKey: string,
): Envelope<ForecastOut[]> | undefined {
  return cache.get(cacheKey(minutes, queryKey))
}

/** Prefetch the next `depth` frames after `currentMinutes`. */
export function prefetchUpcoming(
  currentMinutes: number,
  queryKey: string,
  query: LodQuery,
  depth = 5,
  supportedHorizonsHours: number[] = DEFAULT_HORIZONS_HOURS,
  runId?: string,
): void {
  const keyframes = keyframesFor(supportedHorizonsHours)
  const idx = keyframes.indexOf(currentMinutes)
  if (idx < 0) return
  for (let i = 1; i <= depth; i++) {
    const nextIdx = idx + i
    if (nextIdx >= keyframes.length) break
    ensureForecastFrame(keyframes[nextIdx], queryKey, query, runId)
  }
}

function stateFromEnvelope(envelope: Envelope<ForecastOut[]>): FrameState {
  return {
    status: 'success',
    data: envelope.data,
    isDemo: envelope.is_demo,
    runId: envelope.run_id,
    mode: envelope.mode,
    generatedAt: envelope.generated_at,
  }
}

/**
 * Subscribe to a forecast frame. Returns the requested frame's data when
 * cached; otherwise keeps the most recent frame's data visible and starts
 * a load. `status` is 'loading' only when nothing is available to show yet.
 */
export function useForecastFrame(
  minutes: number,
  queryKey: string,
  query: LodQuery,
  enabled: boolean,
  runId?: string,
): FrameState {
  const memoQuery = useMemo(
    () => query,
    // queryKey already encodes resolution/bbox; the memo only needs to
    // refresh when the key changes. eslint-disable for the structural dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [queryKey],
  )

  // Keep stale data while moving between frames of one view, but never carry
  // it across publication ids or spatial query keys.
  const [latest, setLatest] = useState<{ queryKey: string; frame: FrameState }>(() => ({
    queryKey,
    frame: { status: 'loading', data: undefined, isDemo: false },
  }))

  useEffect(() => {
    if (!enabled) return
    const key = cacheKey(minutes, queryKey)
    const cached = cache.get(key)
    if (cached) {
      setLatest({ queryKey, frame: stateFromEnvelope(cached) })
      return
    }
    // Not cached: keep showing whatever was last loaded (stale-while-
    // revalidate) and start the fetch.
    ensureForecastFrame(minutes, queryKey, memoQuery, runId)
  }, [minutes, queryKey, enabled, memoQuery, runId])

  // Re-read on every store change (a frame resolved).
  useEffect(() => {
    if (!enabled) return
    const onNotify = () => {
      const key = cacheKey(minutes, queryKey)
      const cached = cache.get(key)
      if (cached) {
        setLatest({ queryKey, frame: stateFromEnvelope(cached) })
      } else if (!inFlight.has(key) && failed.has(key)) {
        // A fetch failed with no prior data for this frame.
        setLatest((previous) => {
          if (previous.queryKey === queryKey && previous.frame.data) {
            return previous // keep stale data while remaining within one pinned view
          }
          return {
            queryKey,
            frame: {
              status: 'error',
              data: undefined,
              isDemo: false,
              message: 'Could not load forecast frame',
            },
          }
        })
      }
    }
    listeners.add(onNotify)
    return () => {
      listeners.delete(onNotify)
    }
  }, [minutes, queryKey, enabled])

  return latest.queryKey === queryKey
    ? latest.frame
    : { status: 'loading', data: undefined, isDemo: false }
}
