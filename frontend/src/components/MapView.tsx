import { useEffect, useRef, useState } from 'react'
import {
  GeoJSONSource,
  ImageSource,
  Map as MapLibreMap,
  NavigationControl,
  Popup,
  setWorkerUrl,
} from 'maplibre-gl'
import type { FilterSpecification } from 'maplibre-gl'
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
  freightLinesFeatureCollection,
  freightNodesFeatureCollection,
  normalizeCorridorProperties,
} from '../lib/freightCorridors'
import { EMPTY_REPORTS, cameraPinImage, reportsFeatureCollection } from '../lib/citizenReports'
import { anomalyById, anomalyPopupHtml, fireAnomalyFeatureCollection } from '../lib/fireAnomalies'
import { activeFirePopupHtml, activeFiresFeatureCollection } from '../lib/activeFires'
import type { ActiveFire } from '../lib/activeFires'
import { localHotspotsFeatureCollection } from '../lib/localHotspots'
import type { LocalPollutionHotspot } from '../lib/localHotspots'
import {
  hotspotCandidatePopupHtml,
  hotspotCandidatesFeatureCollection,
  hotspotCandidateFromRow,
} from '../lib/hotspotCandidates'
import {
  GIBS_ATTRIBUTION,
  GIBS_AOD_ATTRIBUTION,
  GIBS_AOD_MAX_ZOOM,
  GIBS_AOD_OPACITY,
  GIBS_MAX_ZOOM,
  GIBS_TILE_SIZE,
  NO2_ATTRIBUTION,
  gibsAerosolTileUrl,
  gibsTrueColorTileUrl,
  no2Available,
  no2TileUrl,
} from '../lib/satelliteImagery'
import {
  cellCenter,
  cellsToFeatureCollection,
  EMPTY_FEATURE_COLLECTION,
  hexEdgeKm,
  inheritMissingCellValues,
  windToFeatureCollection,
} from '../lib/h3Geometry'
import { buildSmoothFieldGrid, renderSmoothFieldFromGrid } from '../lib/smoothField'
import { loadIndiaOutline, type IndiaOutline } from '../lib/indiaOutline'
import { buildRangeContours, buildSmoothRangeContours } from '../lib/pm25Contours'
import { INDIA_BBOX, lodBbox, MAX_SEARCH_ZOOM, MAX_UNSCOPED_ZOOM } from '../lib/lod'
import { scopeContains, scopeMask } from '../lib/scope'
import { INDIA_OUTLINE_URL, STATE_BOUNDARIES_URL } from '../lib/stateBoundaries'
import { DISTRICT_BOUNDARIES_URL, MAJOR_HIGHWAYS_URL, MAJOR_ROADS_URL } from '../lib/staticLayers'
import { BASE_STYLE_URL, OVERLAY, WIND, BASEMAP, patchBasemapStyle } from '../lib/mapTheme'
import {
  CELL_BORDER_COLOR,
  CELL_BORDER_WIDTH,
  CONTRAST_LINE_COLOR,
  CONTRAST_LINE_OPACITY,
  CONTRAST_LINE_WIDTH,
  DISTRICT_BOUNDARY_COLOR,
  HIGHWAY_CASING_COLOR,
  HIGHWAY_CORE_COLOR,
  LAYER_CROSSFADE_DURATION_MS,
  MAJOR_ROAD_COLOR,
  PDI_FILL_OPACITY,
  PM25_DISSOLVE_DURATION_MS,
  PM25_FILL_OPACITY,
  prefersReducedMotion,
  SELECTED_CELL_BORDER_COLOR,
  SELECTED_CELL_BORDER_WIDTH,
} from '../lib/visualConfig'
import { useMapUi } from '../state/MapUiContext'
import type { MapViewMode } from '../state/mapUiReducer'
import type { AsyncResource } from '../hooks/useApiResource'
import { useStateBoundaries } from '../hooks/useStateBoundaries'
import { useDistrictBoundaries } from '../hooks/useDistrictBoundaries'
import type { ScopeBoundaries } from '../lib/scope'
import { loadLocations } from '../lib/locations'
import { placeLabelsFeatureCollection } from '../lib/placeLabels'
import type { IndiaLocation } from '../lib/locations'
import type {
  BoundingBox,
  FireReportWithStatus,
  ForecastOut,
  GridStateOut,
  WeatherReadingOut,
} from '../lib/types'
import type { MultiLineString, Position } from 'geojson'

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
const LAYER_PM25_GENERALIZED: Record<Pm25Set, string> = {
  a: 'pm25-generalized-a',
  b: 'pm25-generalized-b',
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

// Contrast-mode range contours — double-buffered like the fills so they
// dissolve in step with the frame they describe.
const SOURCE_PM25_CONTOUR: Record<Pm25Set, string> = {
  a: 'cells-pm25-contour-a',
  b: 'cells-pm25-contour-b',
}
const LAYER_PM25_CONTOUR: Record<Pm25Set, string> = {
  a: 'cells-pm25-contour-line-a',
  b: 'cells-pm25-contour-line-b',
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

function pm25PaintLayers(viewMode: MapViewMode, contrast: boolean, set: Pm25Set): Pm25PaintLayer[] {
  const layers: Pm25PaintLayer[] =
    viewMode === 'smooth'
      ? [{ layer: LAYER_PM25_RASTER[set], property: 'raster-opacity', base: PM25_FILL_OPACITY }]
      : [
          { layer: LAYER_PM25_FILL[set], property: 'fill-opacity', base: PM25_FILL_OPACITY },
          { layer: LAYER_PM25_OUTLINE[set], property: 'line-opacity', base: 1 },
          { layer: LAYER_PM25_GENERALIZED[set], property: 'line-opacity', base: 1 },
        ]
  // Contrast mode adds the range boundary as part of this set, so it dissolves
  // with the field rather than popping between frames. Both views have one:
  // hex edges between the hexagons, iso-lines across the smoothed surface.
  if (contrast) {
    layers.push({
      layer: LAYER_PM25_CONTOUR[set],
      property: 'line-opacity',
      base: CONTRAST_LINE_OPACITY,
    })
  }
  return layers
}

/** Write one contour geometry into a set's contour source, or clear it. The
 *  geometry comes from whichever builder matches the active view. */
function setContourData(source: GeoJSONSource, contours: MultiLineString | null): void {
  source.setData(
    contours
      ? {
          type: 'FeatureCollection',
          features: [{ type: 'Feature', properties: {}, geometry: contours }],
        }
      : EMPTY_FEATURE_COLLECTION,
  )
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

// Economic freight corridors — a glowing polyline overlay plus clickable
// congestion node markers. Pure context for the intervention picture.
const SOURCE_FREIGHT_LINES = 'freight-lines'
const LAYER_FREIGHT_GLOW = 'corridor-layer-glow'
const LAYER_FREIGHT_LINE = 'corridor-layer'
const SOURCE_FREIGHT_NODES = 'freight-nodes'
const LAYER_FREIGHT_NODES = 'freight-nodes-markers'
const FREIGHT_NODE_COLOR = '#00F5D4'
const FREIGHT_GLOW_OPACITY = 0.38
const FREIGHT_LINE_OPACITY = 0.95

/** Color coding for major economic freight corridors based on predictive risk status */
const CORRIDOR_COLOR_EXPRESSION = [
  'match',
  ['get', 'status'],
  'Critical',
  '#EF4444', // Red for Critical / AKIC
  'Warning',
  '#F59E0B', // Amber for Warning
  'Normal',
  '#00F5D4', // Cyan for Normal
  '#00F5D4',
]

// Citizen report camera pins — amber, wrapped in white pill badges, above
// corridors but below the selection outline, hidden until toggled on.
const SOURCE_CITIZEN = 'citizen-reports'
const LAYER_CITIZEN_PINS = 'citizen-report-pins'
const CITIZEN_IMAGE = 'citizen-camera-pin'
// The same pin for a report that currently affects modeled air quality, which
// today means `status = corroborated`. Two registered images rather than a
// paint change, because the signal has to survive on top of the hot landed
// palette - a tint over red/amber would not.
const CITIZEN_IMAGE_COUNTED = 'citizen-camera-pin-counted'

// VIIRS 375m active-fire detections — the spec-named `satellite-fires-layer`
// is the blurred glowing halo; under it sit an animated pulse ring and a
// hot core dot, so the points read as satellite thermal detections.
const SOURCE_FIRE = 'satellite-fires'
/** The spec-named thermal-anomaly halo layer. */
const LAYER_FIRE_HEATMAP = 'satellite-fires-layer'
const LAYER_FIRE_PULSE = 'satellite-fires-pulse'
const LAYER_FIRE_CORE = 'satellite-fires-core'
// Thermal anomalies are triaged by severity (lib/fireAnomalies): minor
// detections stay small and amber, elevated ones orange, critical ones
// large deep-red. The glow halo and the pulse are filtered to severity 3,
// so the animation marks what is actually urgent rather than animating
// every detection — the triage the spec asks for, expressed in the paint.
const FIRE_MINOR_COLOR = '#FBBF24'
const FIRE_ELEVATED_COLOR = '#F97316'
const FIRE_CRITICAL_COLOR = '#E11D48'
const FIRE_GLOW_COLOR = '#E11D48'
const FIRE_GLOW_OPACITY = 0.5
const FIRE_CORE_OPACITY = 0.95
/** Only critical detections get bloom + pulse. */
const FIRE_CRITICAL_ONLY: FilterSpecification = ['==', ['get', 'severity'], 3]
const FIRE_PULSE_FRAME_COUNT = 6
const FIRE_PULSE_FRAME_MS = 380
const FIRE_PULSE_IMAGE_PREFIX = 'fire-pulse'
const firePulseImageName = (frame: number): string => `${FIRE_PULSE_IMAGE_PREFIX}-${frame}`
const FIRE_PULSE_IMAGES = Array.from({ length: FIRE_PULSE_FRAME_COUNT }, (_, i) =>
  firePulseImageName(i),
)

// NASA GIBS True Color satellite raster — a real daily VIIRS composite laid
// under the hex grid but above the vector basemap, so the pollution fill
// still reads on top of the imagery. Hidden until its toggle turns it on.
const SOURCE_GIBS = 'gibs-true-color'
const LAYER_GIBS = 'gibs-true-color-raster'

// NASA FIRMS active thermal anomalies — a real near-real-time feed, kept
// separate from the illustrative `satellite-fires-*` layers above so the
// two can never be mistaken for one another. Deep red/magenta with a
// blurred halo reads as glowing heat.
const SOURCE_ACTIVE_FIRES = 'active-fires'
const LAYER_ACTIVE_FIRES_GLOW = 'active-fires-glow'
const LAYER_ACTIVE_FIRES_CORE = 'active-fires-core'
const ACTIVE_FIRE_COLOR = '#FF0055'
const ACTIVE_FIRE_GLOW_OPACITY = 0.45
const ACTIVE_FIRE_CORE_OPACITY = 0.9
const SOURCE_HOTSPOT_CANDIDATES = 'hotspot-candidates'
const LAYER_HOTSPOT_CANDIDATES = 'hotspot-candidate-rings'
const SOURCE_LOCAL_HOTSPOTS = 'local-pm25-hotspots'
const LAYER_LOCAL_HOTSPOTS = 'local-pm25-hotspot-rings'

// Place labels — city and district names from the state tier up (level 2),
// then localities at the local tier (level 3), so the map gains named detail
// as it zooms. The dataset is points, not polygons: it can name where a
// district is, not outline it. Same font stack the basemap style already
// loads, so no extra glyph source.
const SOURCE_PLACES = 'place-labels'
const LAYER_PLACE_CITY = 'place-labels-city'
const LAYER_PLACE_LOCALITY = 'place-labels-locality'
const PLACE_LABEL_FONT = ['Open Sans Semibold']
const CITY_LABEL_MIN_ZOOM = 6
const LOCALITY_LABEL_MIN_ZOOM = 7

// Zoom-dependent reference geometry, both from bundled static files
// (lib/staticLayers.ts): district borders from the state tier up, major
// highways from the local tier up. Sources start empty and are filled with
// their URL the first time the map reaches the tier that draws them, so a
// country-only session never downloads either file.
const SOURCE_DISTRICTS = 'district-boundaries'
const LAYER_DISTRICTS = 'district-boundaries-line'
const SOURCE_HIGHWAYS = 'major-highways'
const LAYER_HIGHWAYS = 'major-highways-line'
const LAYER_HIGHWAYS_CASING = 'major-highways-casing'
const SOURCE_ROADS = 'major-roads'
const LAYER_ROADS = 'major-roads-line'
const DISTRICT_BOUNDARY_MIN_ZOOM = 6
const HIGHWAY_MIN_ZOOM = 7
const MAJOR_ROAD_MIN_ZOOM = 9

// Seasonal smog (GIBS Deep Blue AOD) and industrial emissions (Sentinel-5P
// NO2 WMS) — two more raster overlays, both added in the same early block as
// True Color so the whole raster group stays under the hex grid. See
// lib/satelliteImagery.ts for the tile templates.
const SOURCE_AOD = 'gibs-aerosol-aod'
const LAYER_AOD = 'gibs-aerosol-aod-raster'
const SOURCE_NO2 = 'sentinel5p-no2'
const LAYER_NO2 = 'sentinel5p-no2-raster'
const NO2_OPACITY = 0.6

// Place-scope mask: the veil drawn over everything outside a searched place.
// Near-background rather than pure grey so it reads as "not in scope" instead
// of as a data value on the dark base style.
const SOURCE_SCOPE_MASK = 'scope-mask'
const LAYER_SCOPE_MASK = 'scope-mask-fill'
const SCOPE_MASK_COLOR = '#0d0f14'
const SCOPE_MASK_OPACITY = 0.86

// Wind currents are supplementary/decorative ("generalized meteorological
// information"), not the primary data layer the way the PM2.5/PDI cells
// are — so rather than rendering one streak per fetched weather point
// (hundreds nationwide at the country tier, now that demo_data covers
// all of India rather than ~19 cities), thinBySpatialGrid below keeps at
// most one per grid cell of the current viewport, divided into a fixed
// WIND_ARROW_GRID x WIND_ARROW_GRID grid. The grid is sized to the
// viewport, not to zoom directly, so it naturally reveals more of them as
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

/** One frame of the thermal-anomaly pulse ring: an expanding orange ring
 *  that fades out — frame 0 tight and bright, the last frame wide and
 *  nearly gone. Cycling the frames animates the satellite-detection pulse. */
function firePulseFrame(frame: number): ImageData {
  const size = 44
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  const t = frame / FIRE_PULSE_FRAME_COUNT
  const radius = 7 + t * 10
  ctx.beginPath()
  ctx.arc(size / 2, size / 2, radius, 0, Math.PI * 2)
  ctx.globalAlpha = (1 - t) * 0.55
  ctx.fillStyle = FIRE_GLOW_COLOR
  ctx.fill()
  return ctx.getImageData(0, 0, size, size)
}

// How far the smooth field is allowed to run past India's coastline, in
// pixels of the rendered raster (which is capped at MAX_PIXELS square). A few
// pixels is enough to soften the edge without reading as the field extending
// meaningfully into another country.
const SMOOTH_BORDER_MARGIN_PX = 3

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
    for (const layer of fromLayers)
      map.setPaintProperty(layer.layer, layer.property, layer.base * outNorm)
    for (const layer of toLayers)
      map.setPaintProperty(layer.layer, layer.property, layer.base * inNorm)
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
  overviewGrid: AsyncResource<GridStateOut[]>
  parentGrid: AsyncResource<GridStateOut[]>
  overviewForecastGrid: AsyncResource<ForecastOut[]>
  parentForecastGrid: AsyncResource<ForecastOut[]>
  weather: AsyncResource<WeatherReadingOut[]>
  /** Citizen reports, read from GET /api/v2/reports so each row carries its
   *  lifecycle status. The pin image is chosen per report from that status -
   *  see the citizen-report-pins layer. */
  citizenReports: AsyncResource<FireReportWithStatus[]>
  /** Real NASA FIRMS detections, fetched by MapPage. */
  activeFires: AsyncResource<ActiveFire[]>
  /** Local PM2.5 outliers, derived only from the current fine-resolution grid. */
  localHotspots: LocalPollutionHotspot[]
  /** Stop viewport-specific reads immediately when the camera begins moving. */
  onViewportMoveStart: () => void
  /** Resume viewport reads after the settled bounds have been published. */
  onViewportSettled: () => void
}

/** Full-screen MapLibre map. Owns the map instance imperatively (MapLibre
 * isn't a React-first library); React only decides which GeoJSON feeds
 * each source and which layers are visible, driven by props and the
 * shared UI context (horizon, PDI toggle, click -> selected cell,
 * viewport -> level of detail). It does no pollution math or fetching;
 * demo cells with missing local estimates may carry a clearly marked
 * display value from their fetched parent cell. */
export function MapView({
  currentGrid,
  forecastGrid,
  overviewGrid,
  parentGrid,
  overviewForecastGrid,
  parentForecastGrid,
  weather,
  citizenReports,
  activeFires,
  localHotspots,
  onViewportMoveStart,
  onViewportSettled,
}: MapViewProps) {
  const { state, dispatch } = useMapUi()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const viewportCallbacksRef = useRef({ onViewportMoveStart, onViewportSettled })
  useEffect(() => {
    viewportCallbacksRef.current = { onViewportMoveStart, onViewportSettled }
  }, [onViewportMoveStart, onViewportSettled])

  // Which double-buffer set is currently visible.
  const visibleSetRef = useRef<Pm25Set>('a')
  // The exact data array (currentGrid.data / forecastGrid.data) painted on
  // the visible set — so a stale-while-revalidate frame (same array) is a
  // no-op instead of a pointless dissolve.
  const paintedDataRef = useRef<{ primary: unknown; parent: unknown; overview: unknown } | null>(
    null,
  )
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
  // State/UT polygons, already loaded for the map's own boundary layers and
  // reused here to clip the place-scope mask to a real border. District
  // polygons are loaded on demand - only a district-scoped search needs them
  // parsed - and both are handed to lib/scope as one bag, since which one a
  // boundary scope needs is decided by the scope's own kind.
  const stateBoundaries = useStateBoundaries()
  const districtBoundaries = useDistrictBoundaries(
    state.scope?.area.type === 'boundary' && state.scope.area.dataset === 'district',
  )
  const scopeBoundaries: ScopeBoundaries = {
    states: stateBoundaries,
    districts: districtBoundaries,
  }

  // India's country outline, indexed for point queries. Null until it loads,
  // and the paint effect treats null as "don't clip" so the first frames
  // render unclipped rather than briefly empty.
  const [indiaOutline, setIndiaOutline] = useState<IndiaOutline | null>(null)
  useEffect(() => {
    let cancelled = false
    loadIndiaOutline()
      .then((outline) => {
        if (!cancelled) setIndiaOutline(outline)
      })
      .catch(() => {
        // A failed outline fetch leaves clipping off rather than blanking the
        // map: showing more than we should beats showing nothing.
      })
    return () => {
      cancelled = true
    }
  }, [])
  const scopeBoundariesRef = useRef(scopeBoundaries)
  // Live mirrors of the place scope for the map's click handlers. Those are
  // registered once when the map is created, so reading the state directly
  // would freeze whatever it was at creation (no scope at all) and the
  // out-of-scope click guard would never fire.
  const scopeRef = useRef(state.scope)
  // Same idea for the NO2 layer's initial visibility: it is added
  // asynchronously (only once the backend confirms an endpoint is
  // configured), by which time the toggle effect has already run.
  const emissionsRef = useRef(state.showIndustrialEmissions)
  // The info popup currently on the map (a thermal anomaly or a freight node)
  // together with the key identifying its feature, so clicking that same
  // feature again closes it rather than stacking an identical popup, and so
  // Escape can close whatever is open. At most one is ever open.
  const popupRef = useRef<{ key: string; popup: Popup } | null>(null)

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
      popupRef.current = null
    }
  }, [])

  // Keep the click handlers' mirrors of the scope current (see scopeRef).
  useEffect(() => {
    scopeRef.current = state.scope
    scopeBoundariesRef.current = { states: stateBoundaries, districts: districtBoundaries }
    emissionsRef.current = state.showIndustrialEmissions
  }, [state.scope, stateBoundaries, districtBoundaries, state.showIndustrialEmissions])

  // Escape closes whatever is open on the map: the info popup and the cell
  // drawer. On window rather than the canvas so it works wherever focus is,
  // matching the search box, which already closes its own dropdown on Escape.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      popupRef.current?.popup.remove()
      popupRef.current = null
      dispatch({ type: 'SELECT_CELL', cell: null })
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [dispatch])

  // Create the map once: fetch the base style, patch it to a dark
  // monochrome palette, then initialize MapLibre with the patched style.
  useEffect(() => {
    if (!containerRef.current) return
    const container = containerRef.current

    let cancelled = false
    let map: MapLibreMap | null = null
    let debounceHandle: ReturnType<typeof setTimeout> | undefined
    let resizeObserver: ResizeObserver | null = null
    let initialRenderFrame = 0

    const refreshMapLayout = () => {
      if (cancelled || !map) return
      map.resize()
      map.triggerRepaint()
    }

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
          maxZoom: MAX_UNSCOPED_ZOOM,
          renderWorldCopies: false,
        })
        mapRef.current = map
        if (typeof ResizeObserver !== 'undefined') {
          resizeObserver = new ResizeObserver(refreshMapLayout)
          resizeObserver.observe(container)
        }
        map.addControl(new NavigationControl({ showCompass: false }), 'bottom-right')

        // Reports the current viewport (zoom + bounds) to MapUiContext,
        // which derives the level-of-detail tier from it.
        const onMoveStart = () => viewportCallbacksRef.current.onViewportMoveStart()
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
            viewportCallbacksRef.current.onViewportSettled()
          }, VIEWPORT_DEBOUNCE_MS)
        }
        map.on('movestart', onMoveStart)
        map.on('moveend', reportViewport)

        // Install product layers as soon as the style is ready. Waiting for
        // MapLibre's full `load` event also waits for remote basemap tiles;
        // if those tiles stall, the pollution grid stays empty until the user
        // moves the map and prompts another tile pass.
        map.once('style.load', () => {
          // India country base fill — dissolved from geoBoundaries ADM1. Added
          // first of all, *under* the satellite rasters: it is an opaque fill,
          // so anywhere above them it would hide the imagery over India (which
          // is exactly what it did — the raster showed everywhere except the
          // country it is about). Its job is only to give India the same tint
          // the basemap gives other countries when no raster is on, and it
          // still does that from underneath. The border line stays up top.
          map!.addSource(SOURCE_INDIA_OUTLINE, { type: 'geojson', data: INDIA_OUTLINE_URL })
          map!.addLayer({
            id: LAYER_INDIA_OUTLINE_FILL,
            type: 'fill',
            source: SOURCE_INDIA_OUTLINE,
            paint: { 'fill-color': OVERLAY.indiaFill, 'fill-opacity': 1 },
          })

          // NASA GIBS True Color satellite imagery — the bottom raster, above
          // the India fill and under every data layer. `maxzoom` caps requests
          // at the deepest GIBS tile matrix; MapLibre overzooms level-9 tiles
          // past it rather than requesting tiles GIBS doesn't have.
          // `raster-fade-duration: 0` keeps a freshly toggled tile from fading
          // in half-drawn.
          map!.addSource(SOURCE_GIBS, {
            type: 'raster',
            tiles: [gibsTrueColorTileUrl()],
            tileSize: GIBS_TILE_SIZE,
            maxzoom: GIBS_MAX_ZOOM,
            attribution: GIBS_ATTRIBUTION,
          })
          map!.addLayer({
            id: LAYER_GIBS,
            type: 'raster',
            source: SOURCE_GIBS,
            layout: { visibility: 'none' },
            paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 },
          })

          // Seasonal smog — VIIRS Deep Blue AOD at 0.6 opacity, so the smog
          // reads but the hex grid underneath stays legible. Same proxy
          // conventions as True Color, but its own (coarser) zoom ceiling:
          // this product only publishes a level-6 pyramid, and asking for
          // more is a 400 upstream.
          map!.addSource(SOURCE_AOD, {
            type: 'raster',
            tiles: [gibsAerosolTileUrl()],
            tileSize: GIBS_TILE_SIZE,
            maxzoom: GIBS_AOD_MAX_ZOOM,
            attribution: GIBS_AOD_ATTRIBUTION,
          })
          map!.addLayer({
            id: LAYER_AOD,
            type: 'raster',
            source: SOURCE_AOD,
            layout: { visibility: 'none' },
            paint: { 'raster-opacity': GIBS_AOD_OPACITY, 'raster-fade-duration': 0 },
          })

          // Industrial emissions - Sentinel-5P NO2, proxied through the
          // backend (GET /api/v1/tiles/no2/*) so the WMS endpoint and its token
          // stay server-side. The proxy answers 404 while NO2_WMS_URL is unset,
          // so ask once whether it exists and only then add the source - an
          // unconfigured deployment keeps the toggle inert instead of firing a
          // screenful of 404s. This resolves after the synchronous block below,
          // so the layer is inserted by id rather than appended: *before* the
          // PM2.5 field, so it lands under the H3 grid rather than on top of
          // it, and above the India base fill, so it isn't hidden over India
          // the way the other rasters were.
          const no2Tiles = no2TileUrl()
          void no2Available().then((available) => {
            if (!available) return
            map!.addSource(SOURCE_NO2, {
              type: 'raster',
              tiles: [no2Tiles],
              tileSize: GIBS_TILE_SIZE,
              attribution: NO2_ATTRIBUTION,
            })
            map!.addLayer(
              {
                id: LAYER_NO2,
                type: 'raster',
                source: SOURCE_NO2,
                layout: { visibility: 'none' },
                paint: { 'raster-opacity': NO2_OPACITY, 'raster-fade-duration': 0 },
              },
              LAYER_PM25_FILL.a,
            )
            // The toggle effect has already run by now (it skips a layer that
            // does not exist yet), so apply the current setting here - the ref
            // holds the live value, not the one this closure captured at load.
            map!.setLayoutProperty(
              LAYER_NO2,
              'visibility',
              emissionsRef.current ? 'visible' : 'none',
            )
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
            map!.addLayer({
              id: LAYER_PM25_GENERALIZED[set],
              type: 'line',
              source: SOURCE_PM25[set],
              filter: ['==', ['get', 'generalized'], true],
              paint: {
                'line-color': '#f8fafc',
                'line-width': 1.1,
                'line-dasharray': [2, 2],
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

          // Contrast-mode contours: boundaries between PM2.5 bands, drawn
          // above the fills. Same double buffer as the fills.
          for (const set of PM25_SETS) {
            map!.addSource(SOURCE_PM25_CONTOUR[set], {
              type: 'geojson',
              data: EMPTY_FEATURE_COLLECTION,
            })
            map!.addLayer({
              id: LAYER_PM25_CONTOUR[set],
              type: 'line',
              source: SOURCE_PM25_CONTOUR[set],
              paint: {
                'line-color': CONTRAST_LINE_COLOR,
                'line-width': CONTRAST_LINE_WIDTH,
                'line-opacity': 0,
              },
            })
          }

          // PDI — available at every tier, including the country view: the
          // backend area-weights the per-cell score for coarser reads, so a
          // level-1 cell carries an aggregated PDI rather than nothing. This
          // layer used to carry `minzoom: PDI_MIN_ZOOM`, which hid it below
          // zoom 6 — a gate that predated that aggregation. Starts fully
          // transparent; the toggle effect drives its opacity.
          map!.addSource(SOURCE_PDI, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_PDI_FILL,
            type: 'fill',
            source: SOURCE_PDI,
            paint: {
              'fill-color': colorScaleExpression(PDI_COLOR_SCALE, 'value'),
              'fill-opacity': 0,
            },
          })

          // District borders and major highways — reference geometry that
          // appears as the map deepens. Added above the pollution field so it
          // reads over the hexes, and below the state/UT dashes and the
          // country border so those stay the dominant lines. Both sources
          // start empty (see the lazy-load effects below) and each layer is
          // gated by zoom, so nothing is drawn — or fetched — before its tier.
          map!.addSource(SOURCE_DISTRICTS, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_DISTRICTS,
            type: 'line',
            source: SOURCE_DISTRICTS,
            minzoom: DISTRICT_BOUNDARY_MIN_ZOOM,
            paint: {
              'line-color': DISTRICT_BOUNDARY_COLOR,
              'line-width': 0.6,
              'line-opacity': 0.7,
              'line-dasharray': [2, 2],
            },
          })
          // Major roads — the highways' quieter companion, and the one layer
          // that waits for level 4. Added before the highways so their cased
          // line reads on top of the road mesh, and before the state borders
          // like everything else here.
          map!.addSource(SOURCE_ROADS, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_ROADS,
            type: 'line',
            source: SOURCE_ROADS,
            minzoom: MAJOR_ROAD_MIN_ZOOM,
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: {
              'line-color': MAJOR_ROAD_COLOR,
              'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1, 12, 1.8],
              'line-opacity': 0.6,
            },
          })
          map!.addSource(SOURCE_HIGHWAYS, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_HIGHWAYS_CASING,
            type: 'line',
            source: SOURCE_HIGHWAYS,
            minzoom: HIGHWAY_MIN_ZOOM,
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: {
              'line-color': HIGHWAY_CASING_COLOR,
              'line-width': ['interpolate', ['linear'], ['zoom'], 7, 2.8, 10, 4.4],
              'line-opacity': 0.6,
            },
          })
          map!.addLayer({
            id: LAYER_HIGHWAYS,
            type: 'line',
            source: SOURCE_HIGHWAYS,
            minzoom: HIGHWAY_MIN_ZOOM,
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: {
              'line-color': HIGHWAY_CORE_COLOR,
              'line-width': ['interpolate', ['linear'], ['zoom'], 7, 1.2, 10, 2.6],
              'line-opacity': 0.9,
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

          // Freight corridors — glow under a bright core stroke, hidden
          // until the toggle effect fades them in. Node markers sit above.
          map!.addSource(SOURCE_FREIGHT_LINES, {
            type: 'geojson',
            data: freightLinesFeatureCollection(),
          })
          map!.addLayer({
            id: LAYER_FREIGHT_GLOW,
            type: 'line',
            source: SOURCE_FREIGHT_LINES,
            layout: { 'line-cap': 'round', 'line-join': 'round', visibility: 'none' },
            paint: {
              'line-color': CORRIDOR_COLOR_EXPRESSION as never,
              'line-width': ['interpolate', ['linear'], ['zoom'], 3, 8, 8, 14, 12, 18],
              'line-opacity': FREIGHT_GLOW_OPACITY,
              'line-blur': 3,
            },
          })
          map!.addLayer({
            id: LAYER_FREIGHT_LINE,
            type: 'line',
            source: SOURCE_FREIGHT_LINES,
            layout: { 'line-cap': 'round', 'line-join': 'round', visibility: 'none' },
            paint: {
              'line-color': CORRIDOR_COLOR_EXPRESSION as never,
              'line-width': ['interpolate', ['linear'], ['zoom'], 3, 3.5, 8, 5.5, 12, 7.5],
              'line-opacity': FREIGHT_LINE_OPACITY,
            },
          })
          map!.addSource(SOURCE_FREIGHT_NODES, {
            type: 'geojson',
            data: freightNodesFeatureCollection(),
          })
          map!.addLayer({
            id: LAYER_FREIGHT_NODES,
            type: 'circle',
            source: SOURCE_FREIGHT_NODES,
            layout: { visibility: 'none' },
            paint: {
              'circle-radius': 7,
              'circle-color': FREIGHT_NODE_COLOR,
              'circle-stroke-color': '#0b1f1c',
              'circle-stroke-width': 2,
              'circle-opacity': 0.95,
            },
          })

          // Citizen report camera pins — amber, above corridors but below
          // the selection outline, hidden until the toggle turns them on.
          map!.addSource(SOURCE_CITIZEN, {
            type: 'geojson',
            data: EMPTY_REPORTS as never,
          })
          map!.addImage(CITIZEN_IMAGE, cameraPinImage(false))
          map!.addImage(CITIZEN_IMAGE_COUNTED, cameraPinImage(true))
          map!.addLayer({
            id: LAYER_CITIZEN_PINS,
            type: 'symbol',
            source: SOURCE_CITIZEN,
            layout: {
              // Two pins, chosen per feature: an unreviewed claim is a
              // recessive slate-ringed pin, a corroborated one is green. F1
              // reads the v2 shape precisely so this distinction is possible -
              // a pin that looks identical either way is the misreading the
              // lifecycle exists to prevent.
              'icon-image': [
                'case',
                ['==', ['get', 'counts'], true],
                CITIZEN_IMAGE_COUNTED,
                CITIZEN_IMAGE,
              ],
              'icon-size': 0.72,
              'icon-allow-overlap': true,
              visibility: 'none',
            },
          })

          // Info popups (freight nodes, thermal anomalies). Only one is open
          // at a time, and clicking the feature that already has one open
          // closes it instead of stacking a duplicate - so a second click on
          // the same node reads as "close", not as "open it again". The ref
          // is what lets Escape close the popup from outside this effect.
          const togglePopup = (
            key: string,
            lngLat: [number, number],
            className: string,
            html: string,
            offset: number,
          ) => {
            const open = popupRef.current
            const isSameFeature = open !== null && open.key === key && open.popup.isOpen()
            open?.popup.remove()
            popupRef.current = null
            if (isSameFeature) return
            const popup = new Popup({ className, closeButton: false, offset })
              .setLngLat(lngLat)
              .setHTML(html)
              .addTo(map!)
            popupRef.current = { key, popup }
          }

          // Freight node popups contain illustrative sample values only.
          // Popup garbage-collects itself on close.
          map!.on('click', LAYER_FREIGHT_NODES, (event) => {
            const feature = event.features?.[0]
            const props = feature?.properties
            if (!feature || !props) return
            // Node features are authored Points (see freightCorridors.ts).
            const geometry = feature.geometry as unknown as { coordinates: [number, number] }
            togglePopup(
              `freight:${props.name}`,
              geometry.coordinates,
              'freight-node-popup',
              `<strong>${props.name}</strong>` +
                `<span class="freight-popup-demo">Illustrative sample values — not live measurements or a model forecast</span>` +
                `<span class="freight-popup-corridor">${props.corridor}</span>` +
                `<span>Example congestion: <b>${props.congestion}%</b></span>` +
                `<span>Example emissions: <b>${props.emission}</b> t CO₂e / day</span>` +
                (props.spikeRisk
                  ? `<span class="freight-popup-risk">Illustrative scenario risk (not a forecast): <b class="freight-risk-badge freight-risk-${props.spikeRisk.toLowerCase()}">${props.spikeRisk}</b></span>`
                  : '') +
                (props.advisory
                  ? `<span class="freight-popup-advisory">Example response text (not official guidance): ${props.advisory}</span>`
                  : ''),
              12,
            )
          })
          map!.on('mouseenter', LAYER_FREIGHT_NODES, () => {
            map!.getCanvas().style.cursor = 'pointer'
          })
          map!.on('mouseleave', LAYER_FREIGHT_NODES, () => {
            map!.getCanvas().style.cursor = ''
          })

          // Major economic freight corridors: click event extracts predictive
          // metadata and sets it into the app's selected corridor state.
          const handleCorridorClick = (event: { features?: unknown[] }) => {
            const feature = event.features?.[0] as { properties?: Record<string, unknown> } | undefined
            const props = feature?.properties
            if (!feature || !props) return
            const corridor = normalizeCorridorProperties(props)
            dispatch({ type: 'SELECT_CORRIDOR', corridor })
          }

          map!.on('click', 'corridor-layer', handleCorridorClick)
          map!.on('click', LAYER_FREIGHT_GLOW, handleCorridorClick)

          map!.on('mouseenter', 'corridor-layer', () => {
            map!.getCanvas().style.cursor = 'pointer'
          })
          map!.on('mouseleave', 'corridor-layer', () => {
            map!.getCanvas().style.cursor = ''
          })
          map!.on('mouseenter', LAYER_FREIGHT_GLOW, () => {
            map!.getCanvas().style.cursor = 'pointer'
          })
          map!.on('mouseleave', LAYER_FREIGHT_GLOW, () => {
            map!.getCanvas().style.cursor = ''
          })

          // VIIRS thermal anomalies — glow halo (the spec-named layer),
          // animated pulse ring, then the hot core dot. Registered after the
          // citizen pins so a fire next to a camera pin still reads hot.
          map!.addSource(SOURCE_FIRE, {
            type: 'geojson',
            data: fireAnomalyFeatureCollection(),
          })
          for (let frame = 0; frame < FIRE_PULSE_FRAME_COUNT; frame++) {
            map!.addImage(firePulseImageName(frame), firePulseFrame(frame))
          }
          map!.addLayer({
            id: LAYER_FIRE_HEATMAP,
            type: 'circle',
            source: SOURCE_FIRE,
            // Critical detections only: the bloom should draw the eye to
            // what needs a response, not to every small burn.
            filter: FIRE_CRITICAL_ONLY,
            layout: { visibility: 'none' },
            paint: {
              'circle-color': FIRE_GLOW_COLOR,
              // radius 8–12px halo, blurred to read as thermal bloom.
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 6, 8, 11, 12, 15],
              'circle-blur': 1,
              'circle-opacity': FIRE_GLOW_OPACITY,
            },
          })
          map!.addLayer({
            id: LAYER_FIRE_PULSE,
            type: 'symbol',
            source: SOURCE_FIRE,
            filter: FIRE_CRITICAL_ONLY,
            layout: {
              'icon-image': FIRE_PULSE_IMAGES[0],
              'icon-allow-overlap': true,
              'icon-ignore-placement': true,
              visibility: 'none',
            },
          })
          map!.addLayer({
            id: LAYER_FIRE_CORE,
            type: 'circle',
            source: SOURCE_FIRE,
            layout: { visibility: 'none' },
            paint: {
              // 4px for a minor burn up to 10px for a critical fire at the
              // deepest zoom tier, so size alone carries the triage.
              'circle-radius': [
                'interpolate',
                ['linear'],
                ['zoom'],
                3,
                ['match', ['get', 'severity'], 1, 2.5, 2, 4, 3, 6, 4],
                8,
                ['match', ['get', 'severity'], 1, 4, 2, 7, 3, 10, 4],
              ],
              'circle-color': [
                'match',
                ['get', 'severity'],
                1,
                FIRE_MINOR_COLOR,
                2,
                FIRE_ELEVATED_COLOR,
                3,
                FIRE_CRITICAL_COLOR,
                // Fallback for a missing severity: fail loud, not quiet.
                FIRE_CRITICAL_COLOR,
              ],
              'circle-stroke-color': ['match', ['get', 'severity'], 3, '#FECDD3', '#1A0C08'],
              'circle-stroke-width': ['match', ['get', 'severity'], 3, 1.6, 1.2],
              'circle-opacity': FIRE_CORE_OPACITY,
            },
          })

          // NASA FIRMS active fires — real near-real-time detections, drawn
          // above the illustrative thermal layer. A blurred magenta halo
          // under a solid core reads as glowing heat. Hidden until toggled.
          map!.addSource(SOURCE_ACTIVE_FIRES, {
            type: 'geojson',
            data: EMPTY_FEATURE_COLLECTION,
          })
          map!.addLayer({
            id: LAYER_ACTIVE_FIRES_GLOW,
            type: 'circle',
            source: SOURCE_ACTIVE_FIRES,
            layout: { visibility: 'none' },
            paint: {
              'circle-color': ACTIVE_FIRE_COLOR,
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 5, 8, 12, 12, 18],
              'circle-blur': 1,
              'circle-opacity': ACTIVE_FIRE_GLOW_OPACITY,
            },
          })
          map!.addLayer({
            id: LAYER_ACTIVE_FIRES_CORE,
            type: 'circle',
            source: SOURCE_ACTIVE_FIRES,
            layout: { visibility: 'none' },
            paint: {
              'circle-color': ACTIVE_FIRE_COLOR,
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 2.5, 8, 4.5, 12, 7],
              'circle-stroke-color': '#FFD1E0',
              'circle-stroke-width': 1,
              'circle-opacity': ACTIVE_FIRE_CORE_OPACITY,
            },
          })

          // A separate triage layer over the same real FIRMS rows. Hollow
          // markers distinguish detector-confidence candidates from the
          // active-fire glow; low-confidence records remain visible in slate.
          map!.addSource(SOURCE_HOTSPOT_CANDIDATES, {
            type: 'geojson',
            data: EMPTY_FEATURE_COLLECTION,
          })
          map!.addLayer({
            id: LAYER_HOTSPOT_CANDIDATES,
            type: 'circle',
            source: SOURCE_HOTSPOT_CANDIDATES,
            layout: { visibility: 'none' },
            paint: {
              'circle-color': 'rgba(0, 0, 0, 0)',
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 6, 8, 9, 12, 12],
              'circle-stroke-color': [
                'match',
                ['get', 'candidate_status'],
                'candidate',
                '#22D3EE',
                'rejected',
                '#64748B',
                '#A78BFA',
              ],
              'circle-stroke-width': 2.5,
            },
          })

          // Amber rings distinguish PM2.5 outlier triage from FIRMS thermal
          // detections. This layer is fed only by the current fine grid.
          map!.addSource(SOURCE_LOCAL_HOTSPOTS, {
            type: 'geojson',
            data: EMPTY_FEATURE_COLLECTION,
          })
          map!.addLayer({
            id: LAYER_LOCAL_HOTSPOTS,
            type: 'circle',
            source: SOURCE_LOCAL_HOTSPOTS,
            layout: { visibility: 'none' },
            paint: {
              'circle-color': 'rgba(251, 191, 36, 0.16)',
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 7, 10, 15, 12, 20],
              'circle-stroke-color': '#FBBF24',
              'circle-stroke-width': 3,
              'circle-opacity': 0.98,
            },
          })

          // Place labels — added after the data layers so names sit on top of
          // the field, but before the scope mask so a greyed-out area greys
          // its labels too. Two layers off one source, each gated by zoom:
          // cities and districts from level 2, localities from level 3.
          // Collision handling is MapLibre's (text-allow-overlap off), and
          // symbol-sort-key puts cities ahead of districts ahead of
          // localities, so the more significant name wins an overlap.
          map!.addSource(SOURCE_PLACES, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_PLACE_CITY,
            type: 'symbol',
            source: SOURCE_PLACES,
            minzoom: CITY_LABEL_MIN_ZOOM,
            filter: ['any', ['==', ['get', 'kind'], 'city'], ['==', ['get', 'kind'], 'district']],
            layout: {
              'text-field': ['get', 'name'],
              'text-font': PLACE_LABEL_FONT,
              'text-size': ['match', ['get', 'kind'], 'city', 12.5, 'district', 11, 11],
              'text-padding': 4,
              'text-allow-overlap': false,
              'symbol-sort-key': ['match', ['get', 'kind'], 'city', 0, 'district', 1, 2],
            },
            paint: {
              'text-color': '#eef1f5',
              'text-halo-color': 'rgba(8, 10, 14, 0.9)',
              'text-halo-width': 1.3,
            },
          })
          map!.addLayer({
            id: LAYER_PLACE_LOCALITY,
            type: 'symbol',
            source: SOURCE_PLACES,
            minzoom: LOCALITY_LABEL_MIN_ZOOM,
            filter: ['==', ['get', 'kind'], 'locality'],
            layout: {
              'text-field': ['get', 'name'],
              'text-font': PLACE_LABEL_FONT,
              'text-size': 10.5,
              'text-padding': 3,
              'text-allow-overlap': false,
              'symbol-sort-key': 2,
            },
            paint: {
              'text-color': '#c3c9d4',
              'text-halo-color': 'rgba(8, 10, 14, 0.9)',
              'text-halo-width': 1.2,
            },
          })

          // Place scope mask — added last so it sits over every data layer.
          // The geometry is the whole world with the scoped place punched out
          // as a hole (lib/scope.ts), so a hexagon straddling the boundary is
          // greyed only on the outside part and the scope stays clear. Hidden
          // until a place is scoped.
          map!.addSource(SOURCE_SCOPE_MASK, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_SCOPE_MASK,
            type: 'fill',
            source: SOURCE_SCOPE_MASK,
            layout: { visibility: 'none' },
            paint: {
              'fill-color': SCOPE_MASK_COLOR,
              'fill-opacity': SCOPE_MASK_OPACITY,
            },
          })

          // Fire dot popups — VIIRS metadata + the triage priority.
          map!.on('click', LAYER_FIRE_CORE, (event) => {
            const feature = event.features?.[0]
            const props = feature?.properties
            if (!feature || !props) return
            const anomaly = anomalyById(props.id)
            if (anomaly === null) return
            // Anomaly features are authored Points (see fireAnomalies.ts).
            const geometry = feature.geometry as unknown as { coordinates: [number, number] }
            togglePopup(
              `fire:${props.id}`,
              geometry.coordinates,
              'fire-anomaly-popup',
              anomalyPopupHtml(anomaly),
              10,
            )
          })
          map!.on('mouseenter', LAYER_FIRE_CORE, () => {
            map!.getCanvas().style.cursor = 'pointer'
          })
          map!.on('mouseleave', LAYER_FIRE_CORE, () => {
            map!.getCanvas().style.cursor = ''
          })

          // FIRMS active-fire popups — query the clicked feature's FIRMS
          // properties (FRP and friends), log them, and surface them in a
          // popup. The properties also ride along on a window event so the
          // inspection drawer can pick a clicked detection up.
          map!.on('click', LAYER_ACTIVE_FIRES_CORE, (event) => {
            const feature = event.features?.[0]
            const props = feature?.properties as Record<string, unknown> | undefined
            if (!feature || !props) return
            // eslint-disable-next-line no-console
            console.log('NASA FIRMS active fire:', props)
            window.dispatchEvent(
              new CustomEvent('air-health:firms-fire-selected', { detail: props }),
            )
            const geometry = feature.geometry as unknown as { coordinates: [number, number] }
            togglePopup(
              `firms:${props.id}`,
              geometry.coordinates,
              'fire-anomaly-popup firms-fire-popup',
              activeFirePopupHtml(props),
              10,
            )
          })
          map!.on('mouseenter', LAYER_ACTIVE_FIRES_CORE, () => {
            map!.getCanvas().style.cursor = 'pointer'
          })
          map!.on('mouseleave', LAYER_ACTIVE_FIRES_CORE, () => {
            map!.getCanvas().style.cursor = ''
          })

          map!.on('click', LAYER_HOTSPOT_CANDIDATES, (event) => {
            const feature = event.features?.[0]
            const props = feature?.properties as Record<string, unknown> | undefined
            if (!feature || !props) return
            window.dispatchEvent(
              new CustomEvent('air-health:hotspot-candidate-selected', { detail: props }),
            )
            const geometry = feature.geometry as unknown as { coordinates: [number, number] }
            togglePopup(
              `candidate:${props.detection_id}`,
              geometry.coordinates,
              'fire-anomaly-popup firms-fire-popup',
              hotspotCandidatePopupHtml(props),
              10,
            )
          })
          map!.on('mouseenter', LAYER_HOTSPOT_CANDIDATES, () => {
            map!.getCanvas().style.cursor = 'pointer'
          })
          map!.on('mouseleave', LAYER_HOTSPOT_CANDIDATES, () => {
            map!.getCanvas().style.cursor = ''
          })

          map!.on('click', LAYER_LOCAL_HOTSPOTS, (event) => {
            const h3Cell = event.features?.[0]?.properties?.h3_cell
            if (typeof h3Cell !== 'string') return
            dispatch({ type: 'TOGGLE_CELL', cell: h3Cell, generalized: false })
          })
          map!.on('mouseenter', LAYER_LOCAL_HOTSPOTS, () => {
            map!.getCanvas().style.cursor = 'pointer'
          })
          map!.on('mouseleave', LAYER_LOCAL_HOTSPOTS, () => {
            map!.getCanvas().style.cursor = ''
          })

          const clickableLayers = [LAYER_PM25_FILL.a, LAYER_PM25_FILL.b, LAYER_PDI_FILL]
          map!.on('click', clickableLayers, (event) => {
            const properties = event.features?.[0]?.properties
            const h3Cell = properties?.h3_cell
            if (typeof h3Cell !== 'string') return
            const sourceCell = properties?.source_h3_cell
            const generalized = properties?.generalized === true && typeof sourceCell === 'string'
            // A greyed-out cell is outside the scoped place: there is no
            // drawer for a cell the user can't see, so those clicks do
            // nothing. Judged against the same geometry the mask is drawn
            // from (lib/scope.ts), not by hit-testing the render.
            const scope = scopeRef.current
            if (
              scope !== null &&
              !scopeContains(scope, scopeBoundariesRef.current, event.lngLat.lat, event.lngLat.lng)
            ) {
              return
            }
            // Toggling: clicking the cell already in the drawer closes it.
            // No resolution is passed — the reducer derives it from the cell
            // string itself, which is the only thing that knows it (a cell
            // is valid only at its own resolution - see resolutionOfCell).
            dispatch({
              type: 'TOGGLE_CELL',
              cell: generalized ? (sourceCell as string) : h3Cell,
              generalized,
            })
          })
          map!.on('mouseenter', clickableLayers, () => {
            map!.getCanvas().style.cursor = 'pointer'
          })
          map!.on('mouseleave', clickableLayers, () => {
            map!.getCanvas().style.cursor = ''
          })

          // The map's style is fetched asynchronously, and this view can be
          // mounted from a lazy-loaded chunk. Re-measure after the style and
          // overlays are attached so the first frame uses the final layout;
          // otherwise some browsers leave the canvas blank until user input.
          refreshMapLayout()
          reportViewport()
          initialRenderFrame = requestAnimationFrame(refreshMapLayout)
          setMapReady(true)
        })
      })

    return () => {
      cancelled = true
      clearTimeout(debounceHandle)
      resizeObserver?.disconnect()
      cancelAnimationFrame(initialRenderFrame)
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
    mapRef.current.setMaxZoom(state.scope === null ? MAX_UNSCOPED_ZOOM : MAX_SEARCH_ZOOM)
  }, [mapReady, state.scope])

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
  // hidden (transparent by default):
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
    const shownLayers = pm25PaintLayers(state.viewMode, state.contrast, shown)
    const hiddenLayers = pm25PaintLayers(state.viewMode, state.contrast, hidden)

    const steps: PaintStep[] = state.showPdi
      ? [
          ...shownLayers.map((l) => ({
            layer: l.layer,
            property: l.property,
            from: l.base,
            to: 0,
          })),
          ...hiddenLayers.map((l) => ({ layer: l.layer, property: l.property, from: 0, to: 0 })),
          { layer: LAYER_PDI_FILL, property: 'fill-opacity', from: 0, to: PDI_FILL_OPACITY },
        ]
      : [
          ...shownLayers.map((l) => ({
            layer: l.layer,
            property: l.property,
            from: 0,
            to: l.base,
          })),
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
    // viewMode/contrast included so the effect is re-created when the active
    // view's layers change; the showPdi guard above makes that a no-op unless
    // PDI actually toggled.
  }, [mapReady, state.showPdi, state.viewMode, state.contrast])

  // Freight corridors toggle — visibility-based hiding on all three layers
  // (glow stroke, core stroke, node markers), same rule as the fire layer.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    const visibility: 'visible' | 'none' = state.showFreightCorridors ? 'visible' : 'none'
    map.setLayoutProperty(LAYER_FREIGHT_GLOW, 'visibility', visibility)
    map.setLayoutProperty(LAYER_FREIGHT_LINE, 'visibility', visibility)
    map.setLayoutProperty(LAYER_FREIGHT_NODES, 'visibility', visibility)
  }, [mapReady, state.showFreightCorridors])

  // Citizen report pins toggle — visibility-based hiding.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    map.setLayoutProperty(
      LAYER_CITIZEN_PINS,
      'visibility',
      state.showCitizenSensors ? 'visible' : 'none',
    )
  }, [mapReady, state.showCitizenSensors])

  // NASA GIBS True Color raster toggle — visibility only, same rule as the
  // other overlays. No data is fetched until the tiles are actually
  // requested by a visible layer, so an off toggle costs nothing.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    mapRef.current.setLayoutProperty(
      LAYER_GIBS,
      'visibility',
      state.showSatelliteImagery ? 'visible' : 'none',
    )
  }, [mapReady, state.showSatelliteImagery])

  // Seasonal smog (AOD) raster toggle — visibility only.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    mapRef.current.setLayoutProperty(
      LAYER_AOD,
      'visibility',
      state.showSeasonalSmog ? 'visible' : 'none',
    )
  }, [mapReady, state.showSeasonalSmog])

  // Industrial emissions (NO2) raster toggle. The layer only exists when a
  // WMS endpoint is configured, so the guard skips the toggle cleanly when
  // it isn't — no error, no phantom layer.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    if (map.getLayer(LAYER_NO2) === undefined) return
    map.setLayoutProperty(
      LAYER_NO2,
      'visibility',
      state.showIndustrialEmissions ? 'visible' : 'none',
    )
  }, [mapReady, state.showIndustrialEmissions])

  // NASA FIRMS active-fire toggle — visibility-based hiding on both layers.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    const visibility: 'visible' | 'none' = state.showActiveFires ? 'visible' : 'none'
    map.setLayoutProperty(LAYER_ACTIVE_FIRES_GLOW, 'visibility', visibility)
    map.setLayoutProperty(LAYER_ACTIVE_FIRES_CORE, 'visibility', visibility)
  }, [mapReady, state.showActiveFires])

  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    mapRef.current.setLayoutProperty(
      LAYER_HOTSPOT_CANDIDATES,
      'visibility',
      state.showHotspotCandidates ? 'visible' : 'none',
    )
  }, [mapReady, state.showHotspotCandidates])

  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    mapRef.current.setLayoutProperty(
      LAYER_LOCAL_HOTSPOTS,
      'visibility',
      state.showHotspotCandidates && state.lod.level >= 3 ? 'visible' : 'none',
    )
  }, [mapReady, state.showHotspotCandidates, state.lod.level])

  // Feed the map the real FIRMS detections MapPage fetched. A failed or
  // absent fetch leaves the source empty rather than falling back to
  // anything invented — the illustrative layer is a separate toggle.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const source = mapRef.current.getSource(SOURCE_ACTIVE_FIRES)
    if (!(source instanceof GeoJSONSource)) return
    const fires = activeFires.status === 'success' ? activeFires.data : []
    source.setData(activeFiresFeatureCollection(fires) as never)
  }, [mapReady, activeFires])

  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const source = mapRef.current.getSource(SOURCE_HOTSPOT_CANDIDATES)
    if (!(source instanceof GeoJSONSource)) return
    const candidates =
      activeFires.status === 'success' ? activeFires.data.map(hotspotCandidateFromRow) : []
    source.setData(hotspotCandidatesFeatureCollection(candidates) as never)
  }, [mapReady, activeFires])

  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const source = mapRef.current.getSource(SOURCE_LOCAL_HOTSPOTS)
    if (!(source instanceof GeoJSONSource)) return
    source.setData(localHotspotsFeatureCollection(localHotspots) as never)
  }, [mapReady, localHotspots])

  // Feed the map the reports the backend actually returned. Only real
  // submitted reports become pins; a failed/absent fetch leaves the source
  // empty rather than falling back to anything invented.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const source = mapRef.current.getSource(SOURCE_CITIZEN)
    if (!(source instanceof GeoJSONSource)) return
    const reports = citizenReports.status === 'success' ? citizenReports.data : []
    source.setData(reportsFeatureCollection(reports) as never)
  }, [mapReady, citizenReports])

  // Switching the render mode (or toggling contrast) resets the double buffer
  // to a known state (set 'a' shown, 'b' hidden, buffer roles reset) so the
  // frame effect below repaints the current data into the active layers.
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
      map.setPaintProperty(
        LAYER_PM25_FILL[set],
        'fill-opacity',
        showHex && on ? PM25_FILL_OPACITY : 0,
      )
      map.setPaintProperty(LAYER_PM25_OUTLINE[set], 'line-opacity', showHex && on ? 1 : 0)
      map.setPaintProperty(LAYER_PM25_GENERALIZED[set], 'line-opacity', showHex && on ? 1 : 0)
      map.setPaintProperty(
        LAYER_PM25_RASTER[set],
        'raster-opacity',
        !showHex && on ? PM25_FILL_OPACITY : 0,
      )
      map.setPaintProperty(
        LAYER_PM25_CONTOUR[set],
        'line-opacity',
        state.contrast && on ? CONTRAST_LINE_OPACITY : 0,
      )
    }
  }, [mapReady, state.viewMode, state.contrast])

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

    const parentData =
      state.forecastMinutes === 0
        ? parentGrid.status === 'success' && parentGrid.isDemo
          ? parentGrid.data
          : null
        : parentForecastGrid.status === 'success' && parentForecastGrid.isDemo
          ? parentForecastGrid.data
          : null
    const overviewData =
      state.forecastMinutes === 0
        ? overviewGrid.status === 'success' && overviewGrid.isDemo
          ? overviewGrid.data
          : null
        : overviewForecastGrid.status === 'success' && overviewForecastGrid.isDemo
          ? overviewForecastGrid.data
          : null
    const painted = paintedDataRef.current
    if (
      painted?.primary === cellsData &&
      painted.parent === parentData &&
      painted.overview === overviewData
    ) {
      return
    }

    // One source of truth for the frame's (cell, value) pairs, shared by both
    // renderings.
    //
    // A bbox query resolves a grid across the whole *requested* box (the
    // backend's resolve_cells), not across India's coastline, so a viewport
    // that reaches past the border comes back with cells over Bangladesh,
    // Nepal and the Bay of Bengal too. Those are dropped here rather than
    // drawn: a cell that doesn't touch India at all isn't a gap in our data,
    // it's somewhere the product doesn't cover, and painting it says
    // otherwise. A cell that straddles the border is kept - the overlap is
    // genuinely inside the product's area. Until the outline has loaded
    // nothing is dropped, so the first frames render unclipped rather than
    // briefly empty.
    const primaryCellValues =
      state.forecastMinutes === 0
        ? (currentGrid as Extract<typeof currentGrid, { status: 'success' }>).data.map((cell) => ({
            h3Cell: cell.h3_cell,
            value: cell.pm25,
          }))
        : (forecastGrid as Extract<typeof forecastGrid, { status: 'success' }>).data.map(
            (forecast) => ({
              h3Cell: forecast.h3_cell,
              value: forecast.predicted_pm25,
            }),
          )
    const coarseLayers = [
      ...(parentData === null
        ? []
        : [
            {
              resolution: 4,
              cells: parentData.map((cell) => ({
                h3Cell: cell.h3_cell,
                value:
                  state.forecastMinutes === 0
                    ? (cell as GridStateOut).pm25
                    : (cell as ForecastOut).predicted_pm25,
              })),
            },
          ]),
      ...(overviewData === null
        ? []
        : [
            {
              resolution: 3,
              cells: overviewData.map((cell) => ({
                h3Cell: cell.h3_cell,
                value:
                  state.forecastMinutes === 0
                    ? (cell as GridStateOut).pm25
                    : (cell as ForecastOut).predicted_pm25,
              })),
            },
          ]),
    ]
    const allCellValues = inheritMissingCellValues(primaryCellValues, coarseLayers)

    const cellValues =
      indiaOutline === null
        ? allCellValues
        : allCellValues.filter((cell) => indiaOutline.cellTouchesH3(cell.h3Cell))

    const hasPainted = paintedDataRef.current !== null

    // Finalize any in-flight dissolve/PDI fade and drop any dissolve still
    // waiting on a source load, so buffer roles and opacities are at a known
    // steady state before starting the next.
    pendingDissolveRef.current?.cancel()
    pendingDissolveRef.current = null
    animationFinishRef.current?.finish()
    animationFinishRef.current = null

    const viewMode = state.viewMode
    const contrast = state.contrast
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
        // One grid per frame, used twice: the raster colours it and contrast
        // mode traces iso-lines across it, so the lines follow exactly the
        // surface the raster shows instead of the hexagons underneath.
        const grid = buildSmoothFieldGrid(points, bbox, hexEdgeKm(state.lod.resolution))
        const image = renderSmoothFieldFromGrid(grid, bbox, PM25_COLOR_SCALE)
        // The raster is georeferenced to the viewport bbox, so it would
        // otherwise be painted across neighbouring countries and the sea.
        if (indiaOutline !== null)
          indiaOutline.clipImageToIndia(image, bbox, SMOOTH_BORDER_MARGIN_PX)
        rasterSource.updateImage({
          image,
          coordinates: imageCoords(bbox),
        })
        if (contrast) {
          const contourSource = map.getSource(SOURCE_PM25_CONTOUR[set])
          if (contourSource instanceof GeoJSONSource) {
            setContourData(contourSource, buildSmoothRangeContours(grid, bbox, PM25_COLOR_SCALE))
          }
        }
      } else if (contrast) {
        const contourSource = map.getSource(SOURCE_PM25_CONTOUR[set])
        if (contourSource instanceof GeoJSONSource) {
          setContourData(contourSource, buildRangeContours(cellValues, PM25_COLOR_SCALE))
        }
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
        for (const layer of pm25PaintLayers(viewMode, contrast, from)) {
          map.setPaintProperty(layer.layer, layer.property, full ? layer.base : 0)
        }
      }
    } else if (viewMode === 'smooth') {
      // Image sources swap synchronously (no worker tessellation), so the new
      // raster is on the hidden layer the moment updateImage runs.
      if (!writeFrame(to)) return
      animationFinishRef.current = animatePm25Dissolve(
        map,
        pm25PaintLayers(viewMode, contrast, from),
        pm25PaintLayers(viewMode, contrast, to),
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
          pm25PaintLayers(viewMode, contrast, from),
          pm25PaintLayers(viewMode, contrast, to),
          to === 'b',
          PM25_DISSOLVE_DURATION_MS,
          () => {
            visibleSetRef.current = to
            animationFinishRef.current = null
          },
        )
      })
    }

    paintedDataRef.current = { primary: cellsData, parent: parentData, overview: overviewData }
  }, [
    mapReady,
    state.forecastMinutes,
    state.viewMode,
    state.contrast,
    state.lod,
    state.bbox,
    currentGrid,
    forecastGrid,
    overviewGrid,
    parentGrid,
    overviewForecastGrid,
    parentForecastGrid,
    state.showPdi,
    indiaOutline,
    reducedMotion,
  ])

  // Wind currents — thinned to at most one per cell of a fixed-size grid
  // over the current viewport (see thinBySpatialGrid), so "generalized
  // meteorological information" at the country tier reads as a sparse,
  // legible set of streaks rather than one per fetched point.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    if (weather.status !== 'success') return
    const points = weather.data
      .filter((reading) => reading.wind_speed !== null && reading.wind_direction !== null)
      .map((reading) => ({
        h3Cell: reading.h3_cell,
        latitude: reading.latitude,
        longitude: reading.longitude,
        windSpeed: reading.wind_speed as number,
        windDirection: reading.wind_direction as number,
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

  // Fire-layer toggle — visibility-based hiding for every satellite-fire
  // layer. Opacity tricks can't be trusted here: the core dot's *stroke*
  // ring renders even at circle-opacity 0 (stroke opacity is a separate
  // paint property), which is what left the black outlines behind.
  // Layout visibility removes the layer from rendering entirely.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    const visibility: 'visible' | 'none' = state.showFireHotspots ? 'visible' : 'none'
    map.setLayoutProperty(LAYER_FIRE_HEATMAP, 'visibility', visibility)
    map.setLayoutProperty(LAYER_FIRE_PULSE, 'visibility', visibility)
    map.setLayoutProperty(LAYER_FIRE_CORE, 'visibility', visibility)
  }, [mapReady, state.showFireHotspots])

  // Place scope — swap in the mask geometry (the world minus the scoped
  // place) and show it. Boundary scopes wait for the state polygons to load;
  // until then there is no mask, which is the honest state to show rather
  // than a guessed area. Cheap enough to rebuild on every scope change: it's
  // one polygon, and the mask is static while the map pans and zooms.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const map = mapRef.current
    const source = map.getSource(SOURCE_SCOPE_MASK) as GeoJSONSource | undefined
    if (source === undefined) return

    // Built here rather than from the render-scope bag so the effect's
    // dependencies stay exactly the two collections that matter.
    const mask =
      state.scope === null
        ? null
        : scopeMask(state.scope, { states: stateBoundaries, districts: districtBoundaries })
    source.setData((mask ?? EMPTY_FEATURE_COLLECTION) as never)
    map.setLayoutProperty(LAYER_SCOPE_MASK, 'visibility', mask === null ? 'none' : 'visible')
  }, [mapReady, state.scope, stateBoundaries, districtBoundaries])

  // Place labels are loaded the first time the map reaches the state tier,
  // not on mount: the file is the same ~800 KB GeoNames list the search bar
  // reads (lib/locations.ts caches the promise, so the two share one fetch),
  // and a country-only session has no labels to draw with it anyway. Once
  // loaded it stays, so zooming back out and in again costs nothing.
  const [places, setPlaces] = useState<IndiaLocation[] | null>(null)
  useEffect(() => {
    if (places !== null || state.lod.tier === 'country') return
    let cancelled = false
    loadLocations()
      .then((loaded) => {
        if (!cancelled) setPlaces(loaded)
      })
      .catch(() => {
        // Labels are supplementary: a failed load leaves the map as it was.
      })
    return () => {
      cancelled = true
    }
  }, [places, state.lod.tier])

  useEffect(() => {
    if (!mapReady || !mapRef.current || places === null) return
    const source = mapRef.current.getSource(SOURCE_PLACES)
    if (source instanceof GeoJSONSource) {
      source.setData(placeLabelsFeatureCollection(places) as never)
    }
  }, [mapReady, places])

  // The two reference layers load the same way, and for the same reason: hand
  // the source its URL only once the map has reached the tier that draws it,
  // so the country view never downloads 543 KB of borders and roads it cannot
  // show. setData takes the URL directly, so the fetch (and the caching of it)
  // stays MapLibre's rather than a second one of ours.
  const [districtsLoaded, setDistrictsLoaded] = useState(false)
  useEffect(() => {
    if (districtsLoaded || !mapReady || !mapRef.current) return
    if (state.lod.tier === 'country') return
    const source = mapRef.current.getSource(SOURCE_DISTRICTS)
    if (source instanceof GeoJSONSource) {
      source.setData(DISTRICT_BOUNDARIES_URL)
      setDistrictsLoaded(true)
    }
  }, [districtsLoaded, mapReady, state.lod.tier])

  const [highwaysLoaded, setHighwaysLoaded] = useState(false)
  useEffect(() => {
    if (highwaysLoaded || !mapReady || !mapRef.current) return
    // Level 3 is the finest tier, so this is the most specific signal the
    // level-of-detail state gives us: resolution 5 only appears there.
    if (state.lod.resolution < 5) return
    const source = mapRef.current.getSource(SOURCE_HIGHWAYS)
    if (source instanceof GeoJSONSource) {
      source.setData(MAJOR_HIGHWAYS_URL)
      setHighwaysLoaded(true)
    }
  }, [highwaysLoaded, mapReady, state.lod.resolution])

  const [roadsLoaded, setRoadsLoaded] = useState(false)
  useEffect(() => {
    if (roadsLoaded || !mapReady || !mapRef.current) return
    if (state.lod.resolution < 6) return
    const source = mapRef.current.getSource(SOURCE_ROADS)
    if (source instanceof GeoJSONSource) {
      source.setData(MAJOR_ROADS_URL)
      setRoadsLoaded(true)
    }
  }, [roadsLoaded, mapReady, state.lod.resolution])

  // Animate the thermal-anomaly pulse ring by cycling the icon frames —
  // same pattern as the wind streaks. Static under prefers-reduced-motion.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    if (!state.showFireHotspots) return
    const map = mapRef.current

    if (reducedMotion) {
      map.setLayoutProperty(LAYER_FIRE_PULSE, 'icon-image', FIRE_PULSE_IMAGES[0])
      return
    }

    let raf = 0
    let lastFrame = -1
    const tick = (now: number) => {
      if (document.visibilityState === 'visible') {
        const frame = Math.floor(now / FIRE_PULSE_FRAME_MS) % FIRE_PULSE_FRAME_COUNT
        if (frame !== lastFrame) {
          lastFrame = frame
          map.setLayoutProperty(LAYER_FIRE_PULSE, 'icon-image', FIRE_PULSE_IMAGES[frame])
        }
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [mapReady, reducedMotion, state.showFireHotspots])

  return <div ref={containerRef} className="map-canvas" />
}

function cellToBoundaryCoords(h3Cell: string): Position[] {
  return cellToBoundary(h3Cell, true) as unknown as Position[]
}
