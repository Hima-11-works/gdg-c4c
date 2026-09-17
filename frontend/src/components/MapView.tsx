import { useEffect, useRef, useState } from 'react'
import { GeoJSONSource, ImageSource, Map as MapLibreMap, NavigationControl, setWorkerUrl } from 'maplibre-gl'
import { cellToBoundary } from 'h3-js'
import 'maplibre-gl/dist/maplibre-gl.css'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'

// MapLibre derives its worker URL at runtime from a variable filename, which
// neither Vite's dev pre-bundler nor the production build can see — without
// this the worker 404s and the map never renders. `?worker&url` makes Vite
// bundle the worker (with its shared chunk) and hand back a real URL.
setWorkerUrl(maplibreWorkerUrl)
import { colorScaleExpression, PDI_COLOR_SCALE, PM25_COLOR_SCALE } from '../lib/colorScales'
import {
  cellCenter,
  cellsToFeatureCollection,
  EMPTY_FEATURE_COLLECTION,
  hexEdgeKm,
  windToFeatureCollection,
} from '../lib/h3Geometry'
import { renderSmoothField } from '../lib/smoothField'
import { INDIA_BBOX, lodBbox, MAX_ZOOM, PDI_MIN_ZOOM } from '../lib/lod'
import { INDIA_OUTLINE_URL, STATE_BOUNDARIES_URL } from '../lib/stateBoundaries'
import { BASE_STYLE_URL, OVERLAY, WIND, BASEMAP, patchBasemapStyle } from '../lib/mapTheme'
import {
  CELL_BORDER_COLOR,
  CELL_BORDER_WIDTH,
  PM25_DISSOLVE_DURATION_MS,
  LAYER_CROSSFADE_DURATION_MS,
  PDI_FILL_OPACITY,
  PM25_FILL_OPACITY,
  prefersReducedMotion,
  SELECTED_CELL_BORDER_COLOR,
  SELECTED_CELL_BORDER_WIDTH,
} from '../lib/visualConfig'
import { useMapUi } from '../state/MapUiContext'
import type { MapViewMode } from '../state/mapUiReducer'
import type { AsyncResource } from '../hooks/useApiResource'
import type { BoundingBox, ForecastOut, GridStateOut, WeatherReadingOut } from '../lib/types'
import type { Position } from 'geojson'

// The app is scoped to India: on load, fit the whole country in view
// rather than centering on one city. Level of detail (which resolution
// and viewport MapPage fetches) is driven by zoom — see lib/lod.ts; this
// component only reports the viewport and renders whatever comes back.
// Derived from the same INDIA_BBOX MapPage requests for the country
// tier, in MapLibre's own [[west, south], [east, north]] tuple shape,
// so the two can't drift apart into showing a different area than they
// actually fetch.
const INDIA_BOUNDS: [[number, number], [number, number]] = [
  [INDIA_BBOX.minLon, INDIA_BBOX.minLat],
  [INDIA_BBOX.maxLon, INDIA_BBOX.maxLat],
]

// Padded well beyond INDIA_BOUNDS (enough to still show neighboring
// countries for context) but nowhere near a full 360° span — passed as
// `maxBounds` below so MapLibre can neither pan far enough away from
// India to fetch/show a meaningless viewport, nor zoom out past the
// point where it would render more than one copy of the world (and
// therefore more than one copy of India) side by side. `renderWorldCopies:
// false` is a second, independent guard against that same multi-copy
// wraparound in case maxBounds' own zoom clamping ever leaves any slack.
const MAX_PAN_BOUNDS: [[number, number], [number, number]] = [
  [INDIA_BBOX.minLon - 20, Math.max(-85, INDIA_BBOX.minLat - 15)],
  [INDIA_BBOX.maxLon + 20, Math.min(85, INDIA_BBOX.maxLat + 15)],
]

// Debounces the moveend -> SET_VIEWPORT dispatch so a fast flick of the
// scroll wheel (several moveend events in quick succession) triggers one
// re-fetch, not one per tick.
const VIEWPORT_DEBOUNCE_MS = 300

// The PM2.5 layer is DOUBLE-BUFFERED: two complete source+layer pairs (A and
// B). A new frame is written to the hidden pair while the visible pair keeps
// rendering the previous frame, then the two dissolve into each other by
// opacity. This is what keeps playback smooth: MapLibre's setData re-
// tessellates a layer asynchronously and can blank it mid-swap, so a visible
// source must never be rewritten. A hidden source can be rewritten freely.
type Pm25Set = 'a' | 'b'
const PM25_SETS: Pm25Set[] = ['a', 'b']
const SOURCE_PM25: Record<Pm25Set, string> = { a: 'cells-pm25-a', b: 'cells-pm25-b' }
const LAYER_PM25_FILL: Record<Pm25Set, string> = {
  a: 'cells-pm25-fill-a',
  b: 'cells-pm25-fill-b',
}
const LAYER_PM25_OUTLINE: Record<Pm25Set, string> = {
  a: 'cells-pm25-outline-a',
  b: 'cells-pm25-outline-b',
}
const otherSet = (set: Pm25Set): Pm25Set => (set === 'a' ? 'b' : 'a')

// The smooth view double-buffer: each set is a MapLibre `image` source (a
// client-rendered raster of the smoothed field) with a raster layer. Raster
// layers have no outline; the image itself carries the field.
const SOURCE_PM25_RASTER: Record<Pm25Set, string> = {
  a: 'cells-pm25-raster-a',
  b: 'cells-pm25-raster-b',
}
const LAYER_PM25_RASTER: Record<Pm25Set, string> = {
  a: 'cells-pm25-raster-fill-a',
  b: 'cells-pm25-raster-fill-b',
}

// 1x1 transparent PNG — the image sources start empty; the first smooth frame
// replaces it via updateImage.
const TRANSPARENT_PIXEL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='

// Image-source corner order: top-left, top-right, bottom-right, bottom-left.
type ImageCoords = [[number, number], [number, number], [number, number], [number, number]]
const imageCoords = (bbox: BoundingBox): ImageCoords => [
  [bbox.minLon, bbox.maxLat],
  [bbox.maxLon, bbox.maxLat],
  [bbox.maxLon, bbox.minLat],
  [bbox.minLon, bbox.minLat],
]
const INDIA_IMAGE_COORDS = imageCoords(INDIA_BBOX)

/** Layers that carry the PM2.5 value for a given view + buffer set, with the
 *  opacity each sits at when fully shown. Drives both the frame dissolve and
 *  the PDI mode fade. */
interface Pm25PaintLayer {
  layer: string
  property: 'fill-opacity' | 'line-opacity' | 'raster-opacity'
  base: number
}

function pm25PaintLayers(viewMode: MapViewMode, set: Pm25Set): Pm25PaintLayer[] {
  if (viewMode === 'smooth') {
    return [{ layer: LAYER_PM25_RASTER[set], property: 'raster-opacity', base: PM25_FILL_OPACITY }]
  }
  return [
    { layer: LAYER_PM25_FILL[set], property: 'fill-opacity', base: PM25_FILL_OPACITY },
    { layer: LAYER_PM25_OUTLINE[set], property: 'line-opacity', base: 1 },
  ]
}

const SOURCE_PDI = 'cells-pdi'
const LAYER_PDI_FILL = 'cells-pdi-fill'
const SOURCE_STATE_BOUNDARIES = 'state-boundaries'
const LAYER_STATE_BOUNDARIES = 'state-boundaries-line'
const SOURCE_INDIA_OUTLINE = 'india-outline'
const LAYER_INDIA_OUTLINE_FILL = 'india-outline-fill'
const LAYER_INDIA_OUTLINE_LINE = 'india-outline-line'
const SOURCE_WIND = 'wind-points'
const LAYER_WIND = 'wind-arrows'

// Wind currents are drawn as a symbol layer whose icon cycles through a few
// pre-rendered frames of a "streak": a short line pointing downwind with a
// bright pulse that travels from tail to head. Cycling the frames on a timer
// makes the streaks flow — the animated-current look — and it's just one
// layout-property swap per animation frame over ~100 thinned symbols.
const WIND_STREAK_FRAME_COUNT = 8
const WIND_STREAK_FRAME_MS = 110
const WIND_STREAK_IMAGE_PREFIX = 'wind-streak'
const windStreakImageName = (frame: number): string => `${WIND_STREAK_IMAGE_PREFIX}-${frame}`
const WIND_STREAK_IMAGES = Array.from({ length: WIND_STREAK_FRAME_COUNT }, (_, i) =>
  windStreakImageName(i),
)

const SOURCE_SELECTED = 'selected-cell'
const LAYER_SELECTED_OUTLINE = 'selected-cell-outline'

// Wind arrows are supplementary/decorative ("generalized meteorological
// information"), not the primary data layer the way the PM2.5/PDI cells
// are — so rather than rendering one arrow per fetched weather point
// (hundreds nationwide at the country tier, now that demo_data covers
// all of India rather than ~19 cities), thinBySpatialGrid below keeps at
// most one per grid cell of the current viewport, divided into a fixed
// WIND_ARROW_GRID x WIND_ARROW_GRID grid. The grid is sized to the
// viewport, not to zoom directly, so it naturally reveals more arrows as
// the user zooms into a smaller area — the same "progressively reveal
// more detail" behavior as the PM2.5/PDI cells, without a second set of
// zoom thresholds to keep in sync with lib/lod.ts's.
const WIND_ARROW_GRID = 10

function thinBySpatialGrid<T extends { latitude: number; longitude: number }>(
  points: T[],
  bbox: BoundingBox,
): T[] {
  const latSpan = bbox.maxLat - bbox.minLat || 1
  const lonSpan = bbox.maxLon - bbox.minLon || 1
  const seen = new Set<string>()
  const kept: T[] = []
  for (const point of points) {
    const row = Math.floor(((point.latitude - bbox.minLat) / latSpan) * WIND_ARROW_GRID)
    const col = Math.floor(((point.longitude - bbox.minLon) / lonSpan) * WIND_ARROW_GRID)
    const key = `${row}:${col}`
    if (!seen.has(key)) {
      seen.add(key)
      kept.push(point)
    }
  }
  return kept
}

/** One frame of the wind-streak icon, north-up: a dim line along the wind
 * with a bright pulse at position `frame / WIND_STREAK_FRAME_COUNT`, so
 * cycling the frames animates a pulse flowing from tail to head. */
function windStreakFrame(frame: number): ImageData {
  const size = 32
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  const x = size / 2
  const headY = 4
  const tailY = 28
  const span = tailY - headY
  ctx.lineCap = 'round'

  // Dim base so each sample still reads as a current between pulses.
  ctx.globalAlpha = 0.35
  ctx.strokeStyle = WIND.arrowColor
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(x, tailY)
  ctx.lineTo(x, headY)
  ctx.stroke()
  ctx.globalAlpha = 1

  // Travelling pulse: a gradient segment, transparent at its tail and bright
  // at its head, whose position sweeps from the tail (frame 0) to past the
  // head (last frame), then wraps — the repeating flow.
  const t = frame / WIND_STREAK_FRAME_COUNT
  const pulseLen = span * 0.6
  const headPos = tailY - span * (t * 1.2)
  const tailPos = headPos + pulseLen
  const gradient = ctx.createLinearGradient(0, tailPos, 0, headPos)
  gradient.addColorStop(0, 'rgba(229, 231, 235, 0)')
  gradient.addColorStop(1, WIND.pulse)
  ctx.strokeStyle = gradient
  ctx.lineWidth = 2.4
  ctx.beginPath()
  ctx.moveTo(x, tailPos)
  ctx.lineTo(x, headPos)
  ctx.stroke()

  return ctx.getImageData(0, 0, size, size)
}

// ---------------------------------------------------------------------------
// Opacity animation — the ONLY per-frame work during a transition. No source
// data is touched, so there is nothing to re-tessellate: the browser just
// composites two already-loaded layers against each other.
// ---------------------------------------------------------------------------

interface PaintStep {
  layer: string
  property: 'fill-opacity' | 'line-opacity' | 'raster-opacity'
  from: number
  to: number
}

interface PaintAnimation {
  /** Snap to the end state immediately and run onDone (cancels the raf). */
  finish: () => void
  /** Stop the raf without touching any paint property — for unmount, when
   *  the map is about to be removed. */
  cancel: () => void
}

/**
 * Run `callback` once `sourceId` has actually finished processing its data.
 * MapLibre applies GeoJSON `setData` on a worker thread, so a dissolve started
 * immediately after `setData` would begin fading in a layer that is still
 * empty/stale — exactly the "loading" artifact we're eliminating. Waiting for
 * the source's `sourcedata`/`isSourceLoaded` signal means the new frame is
 * fully tessellated before any of it becomes visible. The timeout is a safety
 * net in case the event is missed.
 */
function whenSourceLoaded(
  map: MapLibreMap,
  sourceId: string,
  callback: () => void,
): { cancel: () => void } {
  let done = false
  const start = () => {
    if (done) return
    done = true
    map.off('sourcedata', onSourceData)
    clearTimeout(fallback)
    callback()
  }
  const onSourceData = (event: { sourceId?: string; isSourceLoaded?: boolean }) => {
    if (event.sourceId === sourceId && event.isSourceLoaded) start()
  }
  map.on('sourcedata', onSourceData)
  const fallback = setTimeout(start, 250)
  return {
    cancel: () => {
      done = true
      map.off('sourcedata', onSourceData)
      clearTimeout(fallback)
    },
  }
}

/**
 * Animates a set of paint numbers over `duration` ms, eased. Returns a
 * handle so a caller can finalize an in-flight animation before starting the
 * next one (which keeps the double-buffer's layer roles consistent under
 * rapid input) or cancel it on unmount.
 */
function animatePaintValues(
  map: MapLibreMap,
  steps: PaintStep[],
  duration: number,
  onDone?: () => void,
): PaintAnimation {
  let raf = 0
  let finished = false

  const finish = () => {
    if (finished) return
    finished = true
    cancelAnimationFrame(raf)
    for (const step of steps) map.setPaintProperty(step.layer, step.property, step.to)
    onDone?.()
  }

  const cancel = () => {
    if (finished) return
    finished = true
    cancelAnimationFrame(raf)
  }

  if (duration <= 0 || prefersReducedMotion()) {
    finish()
    return { finish, cancel }
  }

  const start = performance.now()
  const tick = (now: number) => {
    const t = Math.min(1, (now - start) / duration)
    const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
    for (const step of steps) {
      map.setPaintProperty(step.layer, step.property, step.from + (step.to - step.from) * eased)
    }
    if (t < 1) raf = requestAnimationFrame(tick)
    else finish()
  }
  raf = requestAnimationFrame(tick)
  return { finish, cancel }
}

/**
 * Dissolves between the two PM2.5 buffer sets while holding the grid's TOTAL
 * opacity constant, so only the cell colors shift — an in-place interpolation
 * rather than a fade. A plain crossfade lets both layers sit at partial
 * opacity mid-transition, momentarily washing the grid out (a "pulse" that
 * makes small frame-to-frame changes hard to read); compensating the outgoing
 * layer's opacity avoids that entirely.
 *
 * With base opacity `a` (the primary layer's), keeping the composited
 * coverage at `a` needs these normalized curves:
 *   incoming on top:    in = t,          out = (1−t)/(1−a·t)
 *   incoming on bottom: out = 1−t,       in  = t/(1−a·(1−t))
 * Each layer's own base then scales its curve (fills use a, outlines use 1,
 * the smooth raster uses a). Buffer 'b' sits above 'a' in layer order.
 */
function animatePm25Dissolve(
  map: MapLibreMap,
  fromLayers: Pm25PaintLayer[],
  toLayers: Pm25PaintLayer[],
  incomingOnTop: boolean,
  duration: number,
  onDone: () => void,
): PaintAnimation {
  const a = PM25_FILL_OPACITY

  const apply = (outNorm: number, inNorm: number) => {
    for (const layer of fromLayers) map.setPaintProperty(layer.layer, layer.property, layer.base * outNorm)
    for (const layer of toLayers) map.setPaintProperty(layer.layer, layer.property, layer.base * inNorm)
  }

  const applyAt = (t: number) => {
    if (incomingOnTop) apply((1 - t) / (1 - a * t), t)
    else apply(1 - t, t / (1 - a * (1 - t)))
  }

  let raf = 0
  let finished = false
  const finish = () => {
    if (finished) return
    finished = true
    cancelAnimationFrame(raf)
    applyAt(1)
    onDone()
  }
  const cancel = () => {
    if (finished) return
    finished = true
    cancelAnimationFrame(raf)
  }

  if (duration <= 0 || prefersReducedMotion()) {
    finish()
    return { finish, cancel }
  }

  const start = performance.now()
  const tick = (now: number) => {
    const t = Math.min(1, (now - start) / duration)
    const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
    applyAt(eased)
    if (t < 1) raf = requestAnimationFrame(tick)
    else finish()
  }
  raf = requestAnimationFrame(tick)
  return { finish, cancel }
}

interface MapViewProps {
  currentGrid: AsyncResource<GridStateOut[]>
  forecastGrid: AsyncResource<ForecastOut[]>
  weather: AsyncResource<WeatherReadingOut[]>
}

/** Full-screen MapLibre map. Owns the map instance imperatively (MapLibre
 * isn't a React-first library); React only decides which GeoJSON feeds
 * each source and which layers are visible, driven by props and the
 * shared UI context (horizon, PDI toggle, click -> selected cell,
 * viewport -> level of detail). No pollution math happens here, and no
 * fetching either — every value rendered is exactly what MapPage's
 * level-of-detail-scoped fetch returned for the current viewport. */
export function MapView({ currentGrid, forecastGrid, weather }: MapViewProps) {
  const { state, dispatch } = useMapUi()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [mapReady, setMapReady] = useState(false)

  // The map-creation effect below runs once (on mount) and registers the
  // click handler then, so it closes over whatever `state.lod` was at
  // that instant unless read through a ref kept fresh every render —
  // needed so a click always reports the resolution actually on screen,
  // not the one active when the map was first created.
  const lodResolutionRef = useRef(state.lod.resolution)
  useEffect(() => {
    lodResolutionRef.current = state.lod.resolution
  }, [state.lod.resolution])

  // Which double-buffer set is currently visible.
  const visibleSetRef = useRef<Pm25Set>('a')
  // The exact data array (currentGrid.data / forecastGrid.data) painted on
  // the visible set — so a stale-while-revalidate frame (same array) is a
  // no-op instead of a pointless dissolve.
  const paintedDataRef = useRef<unknown>(null)
  // Handle of any in-flight layer animation (dissolve or PDI fade), so a new
  // one can finalize the previous before it starts.
  const animationFinishRef = useRef<PaintAnimation | null>(null)
  // A dissolve waiting for its new source to finish loading. Cancelled if a
  // newer frame supersedes it before it starts.
  const pendingDissolveRef = useRef<{ cancel: () => void } | null>(null)
  // Same, for the PDI fade-in waiting on the PDI source to load.
  const pendingPdiRef = useRef<{ cancel: () => void } | null>(null)
  // Last PDI toggle state acted on, so the toggle effect skips its mount run.
  const showPdiRef = useRef(state.showPdi)

  const reducedMotion = prefersReducedMotion()

  // Stop any running animation on unmount without touching the map, which is
  // removed by the map-creation effect's own cleanup.
  useEffect(() => {
    return () => {
      pendingDissolveRef.current?.cancel()
      pendingDissolveRef.current = null
      pendingPdiRef.current?.cancel()
      pendingPdiRef.current = null
      animationFinishRef.current?.cancel()
      animationFinishRef.current = null
    }
  }, [])

  // Create the map once: fetch the base style, patch it to a dark
  // monochrome palette, then initialize MapLibre with the patched style.
  useEffect(() => {
    if (!containerRef.current) return
    const container = containerRef.current

    let cancelled = false
    let map: MapLibreMap | null = null
    let debounceHandle: ReturnType<typeof setTimeout> | undefined

    fetch(BASE_STYLE_URL)
      .then((r) => r.json())
      .then((style) => {
        if (cancelled) return
        patchBasemapStyle(style)

        map = new MapLibreMap({
          container,
          style,
          bounds: INDIA_BOUNDS,
          fitBoundsOptions: { padding: 20 },
          maxBounds: MAX_PAN_BOUNDS,
          maxZoom: MAX_ZOOM,
          renderWorldCopies: false,
        })
        mapRef.current = map
        map.addControl(new NavigationControl({ showCompass: false }), 'bottom-right')

        // Reports the current viewport (zoom + bounds) to MapUiContext,
        // which derives the level-of-detail tier from it.
        const reportViewport = () => {
          clearTimeout(debounceHandle)
          debounceHandle = setTimeout(() => {
            const bounds = map!.getBounds()
            dispatch({
              type: 'SET_VIEWPORT',
              zoom: map!.getZoom(),
              bbox: {
                minLat: bounds.getSouth(),
                minLon: bounds.getWest(),
                maxLat: bounds.getNorth(),
                maxLon: bounds.getEast(),
              },
            })
          }, VIEWPORT_DEBOUNCE_MS)
        }
        map.on('moveend', reportViewport)

        map.on('load', () => {
          // India country outline — dissolved from geoBoundaries ADM1.
          map!.addSource(SOURCE_INDIA_OUTLINE, { type: 'geojson', data: INDIA_OUTLINE_URL })
          map!.addLayer({
            id: LAYER_INDIA_OUTLINE_FILL,
            type: 'fill',
            source: SOURCE_INDIA_OUTLINE,
            paint: { 'fill-color': OVERLAY.indiaFill, 'fill-opacity': 1 },
          })

          // PM2.5 double buffer: set 'a' starts visible, 'b' starts empty and
          // transparent. Each fill uses a CONSTANT color expression — the
          // only thing that ever changes per frame is opacity.
          for (const set of PM25_SETS) {
            map!.addSource(SOURCE_PM25[set], { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
            map!.addLayer({
              id: LAYER_PM25_FILL[set],
              type: 'fill',
              source: SOURCE_PM25[set],
              paint: {
                'fill-color': colorScaleExpression(PM25_COLOR_SCALE, 'value'),
                'fill-opacity': set === 'a' ? PM25_FILL_OPACITY : 0,
              },
            })
            map!.addLayer({
              id: LAYER_PM25_OUTLINE[set],
              type: 'line',
              source: SOURCE_PM25[set],
              paint: {
                'line-color': CELL_BORDER_COLOR,
                'line-width': CELL_BORDER_WIDTH,
                'line-opacity': set === 'a' ? 1 : 0,
              },
            })
          }

          // Smooth view double buffer: client-rendered rasters of the same
          // field, georeferenced to the current view bbox. Placed above the
          // hex layers (only one view is ever visible). raster-fade-duration
          // 0 disables MapLibre's own fade so our dissolve owns the blending.
          for (const set of PM25_SETS) {
            map!.addSource(SOURCE_PM25_RASTER[set], {
              type: 'image',
              url: TRANSPARENT_PIXEL,
              coordinates: INDIA_IMAGE_COORDS,
            })
            map!.addLayer({
              id: LAYER_PM25_RASTER[set],
              type: 'raster',
              source: SOURCE_PM25_RASTER[set],
              paint: {
                'raster-opacity': 0,
                'raster-fade-duration': 0,
                'raster-resampling': 'linear',
              },
            })
          }

          // PDI — state-tier-and-finer, hidden below PDI_MIN_ZOOM. Starts
          // fully transparent; the toggle effect drives its opacity.
          map!.addSource(SOURCE_PDI, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_PDI_FILL,
            type: 'fill',
            source: SOURCE_PDI,
            minzoom: PDI_MIN_ZOOM,
            paint: {
              'fill-color': colorScaleExpression(PDI_COLOR_SCALE, 'value'),
              'fill-opacity': 0,
            },
          })

          // State/UT borders — subtle dashed lines.
          map!.addSource(SOURCE_STATE_BOUNDARIES, { type: 'geojson', data: STATE_BOUNDARIES_URL })
          map!.addLayer({
            id: LAYER_STATE_BOUNDARIES,
            type: 'line',
            source: SOURCE_STATE_BOUNDARIES,
            paint: {
              'line-color': BASEMAP.stateBorder,
              'line-width': 1,
              'line-opacity': 0.7,
              'line-dasharray': [3, 2],
            },
          })

          // India outer boundary — solid line above all polygon layers.
          map!.addLayer({
            id: LAYER_INDIA_OUTLINE_LINE,
            type: 'line',
            source: SOURCE_INDIA_OUTLINE,
            paint: {
              'line-color': OVERLAY.indiaBorder,
              'line-width': 1.5,
              'line-opacity': 0.8,
            },
          })

          // Selected cell highlight — drawn above the data fill layers
          // so the selection border is always visible. Only the selected
          // cell's hex appears here; the highlight stays stable during
          // timeline transitions (the data layers animate underneath).
          map!.addSource(SOURCE_SELECTED, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_SELECTED_OUTLINE,
            type: 'line',
            source: SOURCE_SELECTED,
            paint: {
              'line-color': SELECTED_CELL_BORDER_COLOR,
              'line-width': SELECTED_CELL_BORDER_WIDTH,
            },
          })

          // Wind currents — animated streaks, subdued gray, never dominant.
          map!.addSource(SOURCE_WIND, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          for (let frame = 0; frame < WIND_STREAK_FRAME_COUNT; frame++) {
            map!.addImage(windStreakImageName(frame), windStreakFrame(frame))
          }
          map!.addLayer({
            id: LAYER_WIND,
            type: 'symbol',
            source: SOURCE_WIND,
            layout: {
              'icon-image': WIND_STREAK_IMAGES[0],
              'icon-rotate': ['get', 'rotation'],
              'icon-rotation-alignment': 'map',
              'icon-allow-overlap': true,
              'icon-ignore-placement': true,
              'icon-size': ['interpolate', ['linear'], ['get', 'wind_speed'], 0, 0.5, 15, 1.1],
            },
          })

          const clickableLayers = [LAYER_PM25_FILL.a, LAYER_PM25_FILL.b, LAYER_PDI_FILL]
          map!.on('click', clickableLayers, (event) => {
            const h3Cell = event.features?.[0]?.properties?.h3_cell
            if (typeof h3Cell === 'string') {
              dispatch({ type: 'SELECT_CELL', cell: h3Cell, resolution: lodResolutionRef.current })
            }
          })
          map!.on('mouseenter', clickableLayers, () => {
            map!.getCanvas().style.cursor = 'pointer'
          })
          map!.on('mouseleave', clickableLayers, () => {
            map!.getCanvas().style.cursor = ''
          })

          setMapReady(true)
        })
      })

    return () => {
      cancelled = true
      clearTimeout(debounceHandle)
      if (map) {
        map.remove()
        mapRef.current = null
      }
      setMapReady(false)
    }
  }, [dispatch])

  // Fly to a location selected from the search bar. `focus` is always a
  // fresh object per dispatch, so picking the same place twice re-flies.
  // `moveend` then reports the new viewport, which drives level-of-detail
  // fetching exactly as a manual zoom would.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const focus = state.focus
    if (focus === null) return
    mapRef.current.flyTo({
      center: [focus.longitude, focus.latitude],
      zoom: focus.zoom,
      duration: reducedMotion ? 0 : 1400,
      essential: true,
    })
  }, [mapReady, state.focus, reducedMotion])

  // Selected cell highlight — updates independently of the data layers
  // so it stays stable during timeline transitions. The outline is drawn
  // on a separate source/layer above the fill; clicking a cell updates
  // it immediately; the fill color beneath animates on its own schedule.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const source = mapRef.current.getSource(SOURCE_SELECTED)
    if (!(source instanceof GeoJSONSource)) return

    const cell = state.selectedCell
    if (cell === null) {
      source.setData(EMPTY_FEATURE_COLLECTION)
    } else {
      try {
        const coords = cellToBoundaryCoords(cell)
        source.setData({
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              properties: { h3_cell: cell },
              geometry: { type: 'Polygon', coordinates: [coords] },
            },
          ],
        })
      } catch {
        source.setData(EMPTY_FEATURE_COLLECTION)
      }
    }
  }, [mapReady, state.selectedCell])

  // PDI layer data — always from current state; there is no forecasted PDI.
  // Declared BEFORE the toggle effect below so the source is populated before
  // that effect waits on it to load. Skipped entirely while the layer is
  // hidden (transparent by default, and below PDI_MIN_ZOOM regardless):
  // building a ~800+ cell FeatureCollection on every poll tick for a layer
  // nobody can see is pure waste.
  useEffect(() => {
    if (!mapReady || !mapRef.current || !state.showPdi) return
    if (currentGrid.status !== 'success') return
    const cells = currentGrid.data.map((cell) => ({ h3Cell: cell.h3_cell, value: cell.pdi }))
    const source = mapRef.current.getSource(SOURCE_PDI)
    if (source instanceof GeoJSONSource) source.setData(cellsToFeatureCollection(cells))
  }, [mapReady, currentGrid, state.showPdi])

  // PDI toggle — fade the currently-visible PM2.5 set out and PDI in (or the
  // reverse). Opacity only, so nothing re-tessellates. The inactive PM2.5 set
  // stays at 0 throughout.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    // Only animate on an actual toggle — the layer opacities are already
    // correct at creation, so a mount run would be a spurious fade.
    if (showPdiRef.current === state.showPdi) return
    showPdiRef.current = state.showPdi

    const map = mapRef.current
    pendingDissolveRef.current?.cancel()
    pendingDissolveRef.current = null
    pendingPdiRef.current?.cancel()
    pendingPdiRef.current = null
    animationFinishRef.current?.finish()
    animationFinishRef.current = null

    const shown = visibleSetRef.current
    const hidden = otherSet(shown)

    // Fade whichever PM2.5 layers the active view uses (hex fill+outline or
    // the smooth raster) — plus force the inactive set to 0 — against PDI.
    const shownLayers = pm25PaintLayers(state.viewMode, shown)
    const hiddenLayers = pm25PaintLayers(state.viewMode, hidden)

    const steps: PaintStep[] = state.showPdi
      ? [
          ...shownLayers.map((l) => ({ layer: l.layer, property: l.property, from: l.base, to: 0 })),
          ...hiddenLayers.map((l) => ({ layer: l.layer, property: l.property, from: 0, to: 0 })),
          { layer: LAYER_PDI_FILL, property: 'fill-opacity', from: 0, to: PDI_FILL_OPACITY },
        ]
      : [
          ...shownLayers.map((l) => ({ layer: l.layer, property: l.property, from: 0, to: l.base })),
          ...hiddenLayers.map((l) => ({ layer: l.layer, property: l.property, from: 0, to: 0 })),
          { layer: LAYER_PDI_FILL, property: 'fill-opacity', from: PDI_FILL_OPACITY, to: 0 },
        ]

    const run = () => {
      animationFinishRef.current = animatePaintValues(map, steps, LAYER_CROSSFADE_DURATION_MS)
    }

    if (state.showPdi) {
      // Fade PDI in only once its data is tessellated (see whenSourceLoaded),
      // so the fade never reveals an empty layer.
      pendingPdiRef.current = whenSourceLoaded(map, SOURCE_PDI, () => {
        pendingPdiRef.current = null
        run()
      })
    } else {
      run()
    }
    // viewMode included so the effect is re-created when the active view's
    // layers change; the showPdi guard above makes that a no-op unless PDI
    // actually toggled.
  }, [mapReady, state.showPdi, state.viewMode])

  // Switching between the hex and smooth views resets the double buffer to a
  // known state (set 'a' shown, 'b' hidden, buffer roles reset) so the frame
  // effect below repaints the current data into the newly-active layers.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current

    pendingDissolveRef.current?.cancel()
    pendingDissolveRef.current = null
    pendingPdiRef.current?.cancel()
    pendingPdiRef.current = null
    animationFinishRef.current?.finish()
    animationFinishRef.current = null

    visibleSetRef.current = 'a'
    paintedDataRef.current = null

    const showHex = state.viewMode === 'hex'
    const pdiOn = showPdiRef.current
    for (const set of PM25_SETS) {
      const on = set === 'a' && !pdiOn
      map.setPaintProperty(LAYER_PM25_FILL[set], 'fill-opacity', showHex && on ? PM25_FILL_OPACITY : 0)
      map.setPaintProperty(LAYER_PM25_OUTLINE[set], 'line-opacity', showHex && on ? 1 : 0)
      map.setPaintProperty(LAYER_PM25_RASTER[set], 'raster-opacity', !showHex && on ? PM25_FILL_OPACITY : 0)
    }
    // The frame effect keys off viewMode and paintedDataRef; resetting the
    // ref is not a dep, so nudge it by depending on viewMode alone.
  }, [mapReady, state.viewMode])

  // PM2.5 frames — double-buffered dissolve (see the PM25_SETS comment).
  //
  // Gated on the underlying data array, not the minute number: a stale-while-
  // revalidate frame (same array) is a no-op, so the map never dissolves to
  // identical data. When the array does change, the new frame is written to
  // the hidden set and the two sets dissolve by opacity — the visible set is
  // never rewritten, so playback has no blank/re-tessellation frames.
  //
  // The SAME pipeline drives both views: the hidden buffer receives a hex
  // FeatureCollection (hex view) or a freshly rasterized image (smooth view),
  // and the dissolve is identical opacity math over whichever layers the
  // active view uses.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current

    if (state.forecastMinutes === 0 && currentGrid.status !== 'success') return
    if (state.forecastMinutes !== 0 && forecastGrid.status !== 'success') return

    // Guards above guarantee the active resource is in 'success' state; a
    // plain ternary on state.forecastMinutes can't be narrowed by TS, so
    // grab the arrays via the success-only union members.
    const cellsData =
      state.forecastMinutes === 0
        ? (currentGrid as Extract<typeof currentGrid, { status: 'success' }>).data
        : (forecastGrid as Extract<typeof forecastGrid, { status: 'success' }>).data

    if (cellsData === paintedDataRef.current) return

    // One source of truth for the frame's (cell, value) pairs, shared by both
    // renderings.
    const cellValues =
      state.forecastMinutes === 0
        ? (currentGrid as Extract<typeof currentGrid, { status: 'success' }>).data.map((cell) => ({
            h3Cell: cell.h3_cell,
            value: cell.pm25,
          }))
        : (forecastGrid as Extract<typeof forecastGrid, { status: 'success' }>).data.map((forecast) => ({
            h3Cell: forecast.h3_cell,
            value: forecast.predicted_pm25,
          }))

    const hasPainted = paintedDataRef.current !== null

    // Finalize any in-flight dissolve/PDI fade and drop any dissolve still
    // waiting on a source load, so buffer roles and opacities are at a known
    // steady state before starting the next.
    pendingDissolveRef.current?.cancel()
    pendingDissolveRef.current = null
    animationFinishRef.current?.finish()
    animationFinishRef.current = null

    const viewMode = state.viewMode
    const from = visibleSetRef.current
    const to = otherSet(from)

    // Write this frame's data to a buffer set. The hex source is kept current
    // in BOTH views: its layers are transparent in smooth mode, but they still
    // back click-to-detail (the smooth raster carries no per-cell identity).
    const writeFrame = (set: Pm25Set): boolean => {
      const hexSource = map.getSource(SOURCE_PM25[set])
      if (hexSource instanceof GeoJSONSource) {
        hexSource.setData(cellsToFeatureCollection(cellValues))
      }

      if (viewMode === 'smooth') {
        const rasterSource = map.getSource(SOURCE_PM25_RASTER[set])
        if (!(rasterSource instanceof ImageSource)) return false
        const bbox = lodBbox(state.lod, state.bbox) ?? INDIA_BBOX
        const points = cellValues
          .filter((cell) => cell.value !== null)
          .map((cell) => {
            const [latitude, longitude] = cellCenter(cell.h3Cell)
            return { latitude, longitude, value: cell.value as number }
          })
        const image = renderSmoothField(points, bbox, PM25_COLOR_SCALE, hexEdgeKm(state.lod.resolution))
        rasterSource.updateImage({ image, coordinates: imageCoords(bbox) })
      }
      return true
    }

    if (!hasPainted || reducedMotion || state.showPdi) {
      // First paint, reduced motion, or PM2.5 hidden under PDI: write
      // straight to the visible set, no dissolve. When PDI is on the set is
      // transparent, so this is invisible anyway.
      if (!writeFrame(from)) return
      if (!hasPainted) {
        const full = !state.showPdi
        for (const layer of pm25PaintLayers(viewMode, from)) {
          map.setPaintProperty(layer.layer, layer.property, full ? layer.base : 0)
        }
      }
    } else if (viewMode === 'smooth') {
      // Image sources swap synchronously (no worker tessellation), so the new
      // raster is on the hidden layer the moment updateImage runs.
      if (!writeFrame(to)) return
      animationFinishRef.current = animatePm25Dissolve(
        map,
        pm25PaintLayers(viewMode, from),
        pm25PaintLayers(viewMode, to),
        to === 'b',
        PM25_DISSOLVE_DURATION_MS,
        () => {
          visibleSetRef.current = to
          animationFinishRef.current = null
        },
      )
    } else {
      // Hex: wait for the hidden GeoJSON source to finish tessellating before
      // dissolving, so the fade never reveals an empty layer.
      if (!writeFrame(to)) return
      pendingDissolveRef.current = whenSourceLoaded(map, SOURCE_PM25[to], () => {
        pendingDissolveRef.current = null
        animationFinishRef.current = animatePm25Dissolve(
          map,
          pm25PaintLayers(viewMode, from),
          pm25PaintLayers(viewMode, to),
          to === 'b',
          PM25_DISSOLVE_DURATION_MS,
          () => {
            visibleSetRef.current = to
            animationFinishRef.current = null
          },
        )
      })
    }

    paintedDataRef.current = cellsData
  }, [
    mapReady,
    state.forecastMinutes,
    state.viewMode,
    state.lod,
    state.bbox,
    currentGrid,
    forecastGrid,
    state.showPdi,
    reducedMotion,
  ])

  // Wind currents — thinned to at most one per cell of a fixed-size grid
  // over the current viewport (see thinBySpatialGrid), so "generalized
  // meteorological information" at the country tier reads as a sparse,
  // legible set of streaks rather than one per fetched point.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    if (weather.status !== 'success') return
    const points = weather.data.map((reading) => ({
      h3Cell: reading.h3_cell,
      latitude: reading.latitude,
      longitude: reading.longitude,
      windSpeed: reading.wind_speed,
      windDirection: reading.wind_direction,
    }))
    const effectiveBbox = lodBbox(state.lod, state.bbox) ?? INDIA_BBOX
    const thinned = thinBySpatialGrid(points, effectiveBbox)
    const source = mapRef.current.getSource(SOURCE_WIND)
    if (source instanceof GeoJSONSource) source.setData(windToFeatureCollection(thinned))
  }, [mapReady, weather, state.lod, state.bbox])

  // Animate the wind currents by cycling the streak icon frames. Only swaps
  // the layer's `icon-image` (a layout property) when the frame index
  // actually changes, and only while the tab is visible. Static under
  // prefers-reduced-motion.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current

    if (reducedMotion) {
      map.setLayoutProperty(LAYER_WIND, 'icon-image', WIND_STREAK_IMAGES[0])
      return
    }

    let raf = 0
    let lastFrame = -1
    const tick = (now: number) => {
      if (document.visibilityState === 'visible') {
        const frame = Math.floor(now / WIND_STREAK_FRAME_MS) % WIND_STREAK_FRAME_COUNT
        if (frame !== lastFrame) {
          lastFrame = frame
          map.setLayoutProperty(LAYER_WIND, 'icon-image', WIND_STREAK_IMAGES[frame])
        }
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [mapReady, reducedMotion])

  return <div ref={containerRef} className="map-canvas" />
}

function cellToBoundaryCoords(h3Cell: string): Position[] {
  return cellToBoundary(h3Cell, true) as unknown as Position[]
}
