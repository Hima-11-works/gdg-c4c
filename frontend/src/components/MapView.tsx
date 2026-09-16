import { useEffect, useRef, useState } from 'react'
import { GeoJSONSource, Map as MapLibreMap, NavigationControl, setWorkerUrl } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'

// MapLibre derives its worker URL at runtime from a variable filename, which
// neither Vite's dev pre-bundler nor the production build can see — without
// this the worker 404s and the map never renders. `?worker&url` makes Vite
// bundle the worker (with its shared chunk) and hand back a real URL.
setWorkerUrl(maplibreWorkerUrl)
import { colorScaleExpression, PDI_COLOR_SCALE, PM25_COLOR_SCALE } from '../lib/colorScales'
import {
  cellsToFeatureCollection,
  EMPTY_FEATURE_COLLECTION,
  windToFeatureCollection,
} from '../lib/h3Geometry'
import { INDIA_BBOX, PDI_MIN_ZOOM } from '../lib/lod'
import { INDIA_OUTLINE_URL, STATE_BOUNDARIES_URL } from '../lib/stateBoundaries'
import { BASE_STYLE_URL, OVERLAY, WIND, BASEMAP, patchBasemapStyle } from '../lib/mapTheme'
import { useMapUi } from '../state/MapUiContext'
import type { AsyncResource } from '../hooks/useApiResource'
import type { BoundingBox, ForecastOut, GridStateOut, WeatherReadingOut } from '../lib/types'

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

const SOURCE_PM25 = 'cells-pm25'
const LAYER_PM25_FILL = 'cells-pm25-fill'
const LAYER_PM25_OUTLINE = 'cells-pm25-outline'
const SOURCE_PDI = 'cells-pdi'
const LAYER_PDI_FILL = 'cells-pdi-fill'
const SOURCE_STATE_BOUNDARIES = 'state-boundaries'
const LAYER_STATE_BOUNDARIES = 'state-boundaries-line'
const SOURCE_INDIA_OUTLINE = 'india-outline'
const LAYER_INDIA_OUTLINE_FILL = 'india-outline-fill'
const LAYER_INDIA_OUTLINE_LINE = 'india-outline-line'
const SOURCE_WIND = 'wind-points'
const LAYER_WIND = 'wind-arrows'
const WIND_ARROW_IMAGE = 'wind-arrow'

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

/** A north-pointing arrow drawn on a canvas; the layer rotates it to the
 * downwind bearing. Subdued neutral colors for dark basemap. */
function windArrowImage(): ImageData {
  const size = 32
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  ctx.beginPath()
  ctx.moveTo(16, 2)
  ctx.lineTo(27, 16)
  ctx.lineTo(20, 16)
  ctx.lineTo(20, 30)
  ctx.lineTo(12, 30)
  ctx.lineTo(12, 16)
  ctx.lineTo(5, 16)
  ctx.closePath()
  ctx.lineJoin = 'round'
  ctx.lineWidth = 2
  ctx.strokeStyle = WIND.arrowStroke
  ctx.stroke()
  ctx.fillStyle = WIND.arrowColor
  ctx.fill()
  return ctx.getImageData(0, 0, size, size)
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

          // PM2.5 cells — the primary pollution overlay.
          map!.addSource(SOURCE_PM25, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_PM25_FILL,
            type: 'fill',
            source: SOURCE_PM25,
            paint: {
              'fill-color': colorScaleExpression(PM25_COLOR_SCALE, 'value'),
              'fill-opacity': 0.75,
            },
          })
          map!.addLayer({
            id: LAYER_PM25_OUTLINE,
            type: 'line',
            source: SOURCE_PM25,
            paint: { 'line-color': OVERLAY.cellOutline, 'line-width': 1 },
          })

          // PDI — state-tier-and-finer, hidden below PDI_MIN_ZOOM.
          map!.addSource(SOURCE_PDI, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addLayer({
            id: LAYER_PDI_FILL,
            type: 'fill',
            source: SOURCE_PDI,
            minzoom: PDI_MIN_ZOOM,
            layout: { visibility: 'none' },
            paint: {
              'fill-color': colorScaleExpression(PDI_COLOR_SCALE, 'value'),
              'fill-opacity': 0.65,
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

          // Wind arrows — subdued gray, never dominant.
          map!.addSource(SOURCE_WIND, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
          map!.addImage(WIND_ARROW_IMAGE, windArrowImage())
          map!.addLayer({
            id: LAYER_WIND,
            type: 'symbol',
            source: SOURCE_WIND,
            layout: {
              'icon-image': WIND_ARROW_IMAGE,
              'icon-rotate': ['get', 'rotation'],
              'icon-rotation-alignment': 'map',
              'icon-allow-overlap': true,
              'icon-ignore-placement': true,
              'icon-size': ['interpolate', ['linear'], ['get', 'wind_speed'], 0, 0.5, 15, 1.1],
            },
          })

          const clickableLayers = [LAYER_PM25_FILL, LAYER_PDI_FILL]
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

  // PM2.5 / forecast layer data — switches source with the timeline.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const source = mapRef.current.getSource(SOURCE_PM25)
    if (!(source instanceof GeoJSONSource)) return

    if (state.horizon === 'now') {
      if (currentGrid.status !== 'success') return
      const cells = currentGrid.data.map((cell) => ({ h3Cell: cell.h3_cell, value: cell.pm25 }))
      source.setData(cellsToFeatureCollection(cells))
    } else {
      if (forecastGrid.status !== 'success') return
      const cells = forecastGrid.data.map((forecast) => ({
        h3Cell: forecast.h3_cell,
        value: forecast.predicted_pm25,
      }))
      source.setData(cellsToFeatureCollection(cells))
    }
  }, [mapReady, state.horizon, currentGrid, forecastGrid])

  // PDI layer data — always from current state; there is no forecasted PDI.
  // Skipped entirely while the layer is hidden (off by default, and below
  // PDI_MIN_ZOOM regardless): building a ~800+ cell FeatureCollection on
  // every poll tick for a layer nobody can see is pure waste.
  useEffect(() => {
    if (!mapReady || !mapRef.current || !state.showPdi) return
    if (currentGrid.status !== 'success') return
    const cells = currentGrid.data.map((cell) => ({ h3Cell: cell.h3_cell, value: cell.pdi }))
    const source = mapRef.current.getSource(SOURCE_PDI)
    if (source instanceof GeoJSONSource) source.setData(cellsToFeatureCollection(cells))
  }, [mapReady, currentGrid, state.showPdi])

  // PDI layer visibility toggle.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    mapRef.current.setLayoutProperty(
      LAYER_PDI_FILL,
      'visibility',
      state.showPdi ? 'visible' : 'none',
    )
  }, [mapReady, state.showPdi])

  // Wind arrows — thinned to at most one per cell of a fixed-size grid
  // over the current viewport (see thinBySpatialGrid), so "generalized
  // meteorological information" at the country tier reads as a sparse,
  // legible set of arrows rather than one per fetched point.
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
    const effectiveBbox = state.lod.scopedToViewport ? (state.bbox ?? INDIA_BBOX) : INDIA_BBOX
    const thinned = thinBySpatialGrid(points, effectiveBbox)
    const source = mapRef.current.getSource(SOURCE_WIND)
    if (source instanceof GeoJSONSource) source.setData(windToFeatureCollection(thinned))
  }, [mapReady, weather, state.lod, state.bbox])

  return <div ref={containerRef} className="map-canvas" />
}
