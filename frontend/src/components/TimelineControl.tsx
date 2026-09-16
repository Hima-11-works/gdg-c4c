// Forecast timeline slider (0–720 min, 15-min steps) with Play/Pause/
// Restart controls. One canonical state: forecastMinutes in the shared
// MapUiContext. A prefetch cache keeps a few upcoming frames warm so
// playback doesn't stall on network.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchGridForecast } from '../lib/api'
import { useMapUi } from '../state/MapUiContext'
import type { LodQuery } from '../lib/api'
import type { Envelope, ForecastOut } from '../lib/types'

// --- constants ---

const STEP = 15
const MAX_MINUTES = 360
const KEYFRAMES = Array.from({ length: MAX_MINUTES / STEP + 1 }, (_, i) => i * STEP) // 0,15,…,720
const PLAYBACK_INTERVAL_MS = 750
const PREFETCH_AHEAD = 5 // prefetch this many frames ahead of current position

// --- formatting ---

function formatHorizon(minutes: number): string {
  if (minutes === 0) return 'NOW'
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h === 0) return `+${m} MIN`
  if (m === 0) return `+${h} HR`
  return `+${h} HR ${m} MIN`
}

// --- prefetch cache ---

// A simple Map<minutes, Envelope<ForecastOut[]>> keyed by `${minutes}:${queryKey}`.
// Shared across instances via module scope (there's only one TimelineControl).
const forecastCache = new Map<string, Envelope<ForecastOut[]>>()
const inFlight = new Map<string, Promise<unknown>>()

function cacheKey(minutes: number, queryKey: string): string {
  return `${minutes}:${queryKey}`
}

function prefetchForecast(
  minutes: number,
  queryKey: string,
  query: LodQuery,
): void {
  const key = cacheKey(minutes, queryKey)
  if (forecastCache.has(key) || inFlight.has(key)) return
  const promise = fetchGridForecast(minutes, query)
    .then((data) => {
      forecastCache.set(key, data)
      inFlight.delete(key)
    })
    .catch(() => {
      inFlight.delete(key)
    })
  inFlight.set(key, promise)
}

/** Prefetch the next N frames after `currentMinutes`. */
function prefetchUpcoming(
  currentMinutes: number,
  queryKey: string,
  query: LodQuery,
): void {
  const currentIdx = KEYFRAMES.indexOf(currentMinutes)
  if (currentIdx < 0) return
  for (let i = 1; i <= PREFETCH_AHEAD; i++) {
    const idx = currentIdx + i
    if (idx >= KEYFRAMES.length) break
    prefetchForecast(KEYFRAMES[idx], queryKey, query)
  }
}

// --- slider helpers ---

function minutesToPct(minutes: number): number {
  return (minutes / MAX_MINUTES) * 100
}

function pctToMinutes(pct: number): number {
  const raw = Math.round((pct / 100) * MAX_MINUTES / STEP) * STEP
  return Math.max(0, Math.min(MAX_MINUTES, raw))
}

// --- component ---

export function TimelineControl() {
  const { state, dispatch } = useMapUi()
  const { forecastMinutes, lod, bbox } = state
  const trackRef = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const positionRef = useRef(forecastMinutes)

  // Keep positionRef in sync with external state changes.
  useEffect(() => {
    positionRef.current = forecastMinutes
  }, [forecastMinutes])

  // Build the query for prefetching (matches MapPage's logic).
  const query: LodQuery = useMemo(
    () => ({
      resolution: lod.resolution,
      bbox: lod.scopedToViewport ? (bbox ?? undefined) : undefined,
    }),
    [lod.resolution, lod.scopedToViewport, bbox],
  )
  const queryKey = useMemo(
    () => `${lod.resolution ?? 'default'}:${bbox ? 'vp' : 'nationwide'}`,
    [lod.resolution, bbox],
  )

  // Prefetch upcoming frames whenever position changes.
  useEffect(() => {
    if (forecastMinutes > 0) {
      prefetchUpcoming(forecastMinutes, queryKey, query)
    }
  }, [forecastMinutes, queryKey, query])

  // --- playback ---

  const stopPlayback = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const advance = useCallback(() => {
    const current = positionRef.current
    const idx = KEYFRAMES.indexOf(current)
    if (idx < 0 || idx >= KEYFRAMES.length - 1) {
      // At end — stop.
      stopPlayback()
      setPlaying(false)
      return
    }
    const next = KEYFRAMES[idx + 1]
    positionRef.current = next
    dispatch({ type: 'SELECT_FORECAST', minutes: next })
  }, [dispatch, stopPlayback])

  // Start/stop timer when `playing` changes.
  useEffect(() => {
    if (playing) {
      // If at the end, restart from 0.
      if (positionRef.current >= MAX_MINUTES) {
        positionRef.current = 0
        dispatch({ type: 'SELECT_FORECAST', minutes: 0 })
      }
      timerRef.current = setInterval(advance, PLAYBACK_INTERVAL_MS)
    } else {
      stopPlayback()
    }
    return stopPlayback
  }, [playing, advance, stopPlayback, dispatch])

  const handlePlayPause = useCallback(() => {
    setPlaying((p) => !p)
  }, [])

  const handleRestart = useCallback(() => {
    setPlaying(false)
    positionRef.current = 0
    dispatch({ type: 'SELECT_FORECAST', minutes: 0 })
  }, [dispatch])

  // --- pointer interaction ---

  const commitPct = useCallback(
    (pct: number) => {
      const next = pctToMinutes(pct)
      if (next !== forecastMinutes) {
        // Pause on manual interaction.
        setPlaying(false)
        dispatch({ type: 'SELECT_FORECAST', minutes: next })
      }
    },
    [forecastMinutes, dispatch],
  )

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const track = trackRef.current
      if (!track) return
      track.setPointerCapture(e.pointerId)

      const pctFromEvent = (ev: { clientX: number }) => {
        const rect = track.getBoundingClientRect()
        return Math.max(0, Math.min(100, ((ev.clientX - rect.left) / rect.width) * 100))
      }

      commitPct(pctFromEvent(e))

      const onMove = (ev: PointerEvent) => commitPct(pctFromEvent(ev))
      const onUp = () => {
        track.removeEventListener('pointermove', onMove)
        track.removeEventListener('pointerup', onUp)
      }
      track.addEventListener('pointermove', onMove)
      track.addEventListener('pointerup', onUp)
    },
    [commitPct],
  )

  // --- keyboard ---

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const idx = KEYFRAMES.indexOf(forecastMinutes)
      if (idx < 0) return

      let nextIdx = idx
      if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
        nextIdx = Math.min(idx + 1, KEYFRAMES.length - 1)
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
        nextIdx = Math.max(idx - 1, 0)
      } else if (e.key === 'Home') {
        nextIdx = 0
      } else if (e.key === 'End') {
        nextIdx = KEYFRAMES.length - 1
      } else {
        return
      }

      e.preventDefault()
      if (nextIdx !== idx) {
        setPlaying(false)
        dispatch({ type: 'SELECT_FORECAST', minutes: KEYFRAMES[nextIdx] })
      }
    },
    [forecastMinutes, dispatch],
  )

  // Cleanup on unmount.
  useEffect(() => () => stopPlayback(), [stopPlayback])

  // --- major ticks: every 60 min ---

  const majorTicks = KEYFRAMES.filter((m) => m % 60 === 0)

  const currentPct = minutesToPct(forecastMinutes)

  return (
    <div className="panel timeline-control" role="group" aria-label="Forecast timeline">
      {/* Playback controls + horizon display */}
      <div className="timeline-header">
        <div className="timeline-buttons">
          <button
            type="button"
            className="timeline-btn"
            onClick={handleRestart}
            aria-label="Restart timeline to Now"
            title="Restart"
          >
            ↺
          </button>
          <button
            type="button"
            className="timeline-btn timeline-btn-play"
            onClick={handlePlayPause}
            aria-label={playing ? 'Pause playback' : 'Play forecast animation'}
            aria-pressed={playing}
            title={playing ? 'Pause' : 'Play'}
          >
            {playing ? '❚❚' : '▶'}
          </button>
        </div>
        <div className="timeline-horizon" aria-live="polite" aria-atomic="true">
          {formatHorizon(forecastMinutes)}
        </div>
      </div>

      {/* Major tick labels (every 1h) */}
      <div className="timeline-ticks">
        {majorTicks.map((m) => (
          <button
            key={m}
            type="button"
            className={`timeline-tick ${m === forecastMinutes ? 'active' : ''}`}
            style={{ left: `${minutesToPct(m)}%` }}
            onClick={() => {
              setPlaying(false)
              dispatch({ type: 'SELECT_FORECAST', minutes: m })
            }}
            aria-label={m === 0 ? 'Current conditions' : `Forecast +${m} minutes`}
          >
            {m === 0 ? 'Now' : `+${m / 60}h`}
          </button>
        ))}
      </div>

      {/* Slider track */}
      <div
        ref={trackRef}
        className="timeline-track"
        role="slider"
        tabIndex={0}
        aria-label="Forecast horizon"
        aria-valuemin={0}
        aria-valuemax={MAX_MINUTES}
        aria-valuenow={forecastMinutes}
        aria-valuetext={formatHorizon(forecastMinutes)}
        aria-keyshortcuts="ArrowLeft ArrowRight Home End"
        onPointerDown={handlePointerDown}
        onKeyDown={handleKeyDown}
      >
        {/* Filled portion */}
        <div className="timeline-track-fill" style={{ width: `${currentPct}%` }} />
        {/* Minor ticks (every 15 min) */}
        {KEYFRAMES.map((m) => (
          <div
            key={m}
            className={`timeline-mark ${m % 60 === 0 ? 'major' : ''} ${m === forecastMinutes ? 'active' : ''}`}
            style={{ left: `${minutesToPct(m)}%` }}
          />
        ))}
        {/* Handle */}
        <div className="timeline-handle" style={{ left: `${currentPct}%` }} />
      </div>
    </div>
  )
}
