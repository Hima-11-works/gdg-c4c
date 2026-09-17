// Forecast timeline slider (0–360 min, 15-min steps) with Play/Pause/
// Restart controls. One canonical state: forecastMinutes in the shared
// MapUiContext. Upcoming keyframes are prefetched into the shared frame
// store (lib/forecastFrames.ts) so playback never stalls on the network.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { prefetchUpcoming, useForecastWarming, warmForecastWindow } from '../lib/forecastFrames'
import { INDIA_BBOX, lodKey } from '../lib/lod'
import { useMapUi } from '../state/MapUiContext'
import type { LodQuery } from '../lib/api'

// --- constants ---

const STEP = 15
const MAX_MINUTES = 360
const KEYFRAMES = Array.from({ length: MAX_MINUTES / STEP + 1 }, (_, i) => i * STEP) // 0,15,…,360
const PLAYBACK_INTERVAL_MS = 750

// --- formatting ---

function formatHorizon(minutes: number): string {
  if (minutes === 0) return 'NOW'
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h === 0) return `+${m} MIN`
  if (m === 0) return `+${h} HR`
  return `+${h} HR ${m} MIN`
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

  // Build the query for prefetching — must match MapPage's exactly
  // (same bbox/resolution), so the shared cache key aligns and the
  // prefetched frame is the same data MapPage would have fetched.
  const query: LodQuery = useMemo(
    () => ({
      resolution: lod.resolution,
      bbox: lod.scopedToViewport ? (bbox ?? undefined) : INDIA_BBOX,
    }),
    [lod.resolution, lod.scopedToViewport, bbox],
  )
  const queryKey = useMemo(
    () => lodKey(query),
    [query],
  )

  // True while the current view's frames are being fetched into the cache
  // (a fresh map view, or a manual jump). Play/Restart are disabled until
  // it clears, so playback never starts on a cold cache and stutters.
  const warming = useForecastWarming(queryKey)

  // Manual selection (slider drag, keyboard, tick click): pause playback and
  // warm the new position's window so a subsequent Play is smooth. Playback's
  // own auto-advance deliberately does NOT warm — it keeps the moving
  // prefetch buffer instead, so Play stays enabled once a view is warm.
  const selectManually = useCallback(
    (minutes: number) => {
      setPlaying(false)
      dispatch({ type: 'SELECT_FORECAST', minutes })
      warmForecastWindow(queryKey, query, minutes)
    },
    [dispatch, queryKey, query],
  )

  // Prefetch upcoming frames whenever position changes.
  useEffect(() => {
    if (forecastMinutes > 0) {
      prefetchUpcoming(forecastMinutes, queryKey, query)
    }
  }, [forecastMinutes, queryKey, query])

  // --- playback ---

  // A view change while playing starts a warm-up; stop playback so it can't
  // advance onto uncached frames (the pause button is disabled meanwhile, so
  // leaving it running would just stutter with no way to stop it). This is a
  // legitimate sync-with-external-state effect: it clears the interval timer.
  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect
    if (warming) setPlaying(false)
  }, [warming])

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
    // Warm the frames a Play from Now would advance through.
    warmForecastWindow(queryKey, query, 0)
  }, [dispatch, queryKey, query])

  // --- pointer interaction (draggable slider) ---

  // The commit handler is kept in a ref so the pointer handlers never close
  // over a stale forecastMinutes (they're attached for the whole drag).
  const commitRef = useRef<(minutes: number) => void>(() => {})
  useEffect(() => {
    commitRef.current = (minutes: number) => {
      if (minutes !== positionRef.current) selectManually(minutes)
    }
  })

  const draggingRef = useRef(false)

  const commitFromClientX = (clientX: number) => {
    const track = trackRef.current
    if (!track) return
    const rect = track.getBoundingClientRect()
    const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100))
    commitRef.current(pctToMinutes(pct))
  }

  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    draggingRef.current = true
    e.currentTarget.setPointerCapture(e.pointerId)
    commitFromClientX(e.clientX)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return
    commitFromClientX(e.clientX)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handlePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return
    draggingRef.current = false
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }, [])

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
        selectManually(KEYFRAMES[nextIdx])
      }
    },
    [forecastMinutes, selectManually],
  )

  // Cleanup on unmount.
  useEffect(() => () => stopPlayback(), [stopPlayback])

  // --- major ticks: every 60 min ---

  const majorTicks = KEYFRAMES.filter((m) => m % 60 === 0)

  const currentPct = minutesToPct(forecastMinutes)

  return (
    <div className="panel timeline-control" role="group" aria-label="Forecast timeline">
      {/* Header: horizon on the left, play/pause centered, reset on the right */}
      <div className="timeline-header">
        <div className="timeline-horizon" aria-live="polite" aria-atomic="true">
          {formatHorizon(forecastMinutes)}
        </div>

        <button
          type="button"
          className="timeline-playbtn"
          onClick={handlePlayPause}
          disabled={warming}
          aria-label={
            warming
              ? 'Playback disabled while frames are cached'
              : playing
                ? 'Pause playback'
                : 'Play forecast animation'
          }
          aria-pressed={playing}
          title={warming ? 'Caching frames…' : playing ? 'Pause' : 'Play'}
        >
          {playing ? (
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" />
              <rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 5.5v13l11-6.5z" fill="currentColor" />
            </svg>
          )}
        </button>

        <button
          type="button"
          className="timeline-resetbtn"
          onClick={handleRestart}
          disabled={warming}
          aria-label="Restart timeline to Now"
          title={warming ? 'Caching frames…' : 'Restart'}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M12 5a7 7 0 1 1-6.3 3.9"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
            <path d="M5 3v4h4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      {/* Major tick labels (every 1h) */}
      <div className="timeline-ticks">
        {majorTicks.map((m) => (
          <button
            key={m}
            type="button"
            className={`timeline-tick ${m === forecastMinutes ? 'active' : ''}`}
            style={{ left: `${minutesToPct(m)}%` }}
            onClick={() => selectManually(m)}
            aria-label={m === 0 ? 'Current conditions' : `Forecast +${m} minutes`}
          >
            {m === 0 ? 'Now' : `+${m / 60}h`}
          </button>
        ))}
      </div>

      {/* Draggable slider */}
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
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onLostPointerCapture={() => {
          draggingRef.current = false
        }}
        onKeyDown={handleKeyDown}
      >
        <div className="timeline-track-rail">
          <div className="timeline-track-fill" style={{ width: `${currentPct}%` }} />
        </div>

        {/* Minor ticks (every 15 min) */}
        {KEYFRAMES.map((m) => (
          <div
            key={m}
            className={`timeline-mark ${m % 60 === 0 ? 'major' : ''} ${m === forecastMinutes ? 'active' : ''}`}
            style={{ left: `${minutesToPct(m)}%` }}
          />
        ))}

        {/* Draggable handle */}
        <div className="timeline-handle" style={{ left: `${currentPct}%` }} />
      </div>
    </div>
  )
}
