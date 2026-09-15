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
  cellsToCenterPointFeatureCollection,
  cellsToFeatureCollection,
  EMPTY_FEATURE_COLLECTION,
  windToFeatureCollection,
} from '../lib/h3Geometry'
import type { CellValue } from '../lib/h3Geometry'
import { useMapUi } from '../state/MapUiContext'
import type { AsyncResource } from '../hooks/useApiResource'
import type { ForecastOut, GridStateOut, WeatherReadingOut } from '../lib/types'

// A free, key-less MapLibre-maintained basemap — good enough for an MVP;
// swap for a hosted style later without touching anything below.
const MAP_STYLE = 'https://demotiles.maplibre.org/style.json'

// The app is scoped to India: on load, fit the whole country in view
// rather than centering on one city — see the overview/detail split below.
const INDIA_BOUNDS: [[number, number], [number, number]] = [
  [68.0, 6.5], // southwest: min lon, min lat
  [97.5, 37.5], // northeast: max lon, max lat
]

// Below this zoom, show one easy-to-read circle per city (the country-
// wide "generalized" overview); at/above it, show the real per-hex
// detail. A resolution-8 hex is under a km wide — invisible at country
// zoom — so the two need genuinely different geometry, not just
// different colors. Tune freely; nothing else depends on this number.
const OVERVIEW_MAX_ZOOM = 6

const SOURCE_OVERVIEW = 'cells-overview'
const LAYER_OVERVIEW = 'cells-overview-circle'
const SOURCE_PM25 = 'cells-pm25'
const LAYER_PM25_FILL = 'cells-pm25-fill'
const LAYER_PM25_OUTLINE = 'cells-pm25-outline'
const SOURCE_PDI = 'cells-pdi'
const LAYER_PDI_FILL = 'cells-pdi-fill'
const SOURCE_WIND = 'wind-points'
const LAYER_WIND = 'wind-arrows'
const WIND_ARROW_IMAGE = 'wind-arrow'

/** A north-pointing arrow drawn on a canvas; the layer rotates it to the
 * downwind bearing. */
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
  ctx.lineWidth = 3
  ctx.strokeStyle = '#ffffff'
  ctx.stroke()
  ctx.fillStyle = '#1d4ed8'
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
 * shared UI context (horizon, PDI toggle, click -> selected cell). No
 * pollution math happens here — every value rendered is exactly what the
 * backend returned. */
export function MapView({ currentGrid, forecastGrid, weather }: MapViewProps) {
  const { state, dispatch } = useMapUi()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [mapReady, setMapReady] = useState(false)

  // Create the map once.
  useEffect(() => {
    if (!containerRef.current) return

    const map = new MapLibreMap({
      container: containerRef.current,
      style: MAP_STYLE,
      bounds: INDIA_BOUNDS,
      fitBoundsOptions: { padding: 20 },
    })
    mapRef.current = map
    map.addControl(new NavigationControl({ showCompass: false }), 'bottom-right')

    map.on('load', () => {
      // Country-wide overview: one circle per city, visible only when
      // zoomed out past OVERVIEW_MAX_ZOOM. Same h3_cell/value data as the
      // hex fill layer below, fed by the same effect (see "PM2.5 /
      // forecast layer data").
      map.addSource(SOURCE_OVERVIEW, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
      map.addLayer({
        id: LAYER_OVERVIEW,
        type: 'circle',
        source: SOURCE_OVERVIEW,
        maxzoom: OVERVIEW_MAX_ZOOM,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 7, OVERVIEW_MAX_ZOOM, 16],
          'circle-color': colorScaleExpression(PM25_COLOR_SCALE, 'value'),
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,
          'circle-opacity': 0.9,
        },
      })

      // Per-hex detail: only rendered once zoomed in far enough for
      // individual hexagons to be legible.
      map.addSource(SOURCE_PM25, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
      map.addLayer({
        id: LAYER_PM25_FILL,
        type: 'fill',
        source: SOURCE_PM25,
        minzoom: OVERVIEW_MAX_ZOOM,
        paint: {
          'fill-color': colorScaleExpression(PM25_COLOR_SCALE, 'value'),
          'fill-opacity': 0.65,
        },
      })
      map.addLayer({
        id: LAYER_PM25_OUTLINE,
        type: 'line',
        source: SOURCE_PM25,
        minzoom: OVERVIEW_MAX_ZOOM,
        paint: { 'line-color': '#00000040', 'line-width': 1 },
      })

      map.addSource(SOURCE_PDI, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
      map.addLayer({
        id: LAYER_PDI_FILL,
        type: 'fill',
        source: SOURCE_PDI,
        minzoom: OVERVIEW_MAX_ZOOM,
        layout: { visibility: 'none' },
        paint: {
          'fill-color': colorScaleExpression(PDI_COLOR_SCALE, 'value'),
          'fill-opacity': 0.55,
        },
      })

      map.addSource(SOURCE_WIND, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
      // An image icon rather than a '↑' text glyph: the basemap's glyph
      // server has no arrow characters, so a text arrow silently renders
      // nothing.
      map.addImage(WIND_ARROW_IMAGE, windArrowImage())
      map.addLayer({
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

      const clickableLayers = [LAYER_PM25_FILL, LAYER_PDI_FILL, LAYER_OVERVIEW]
      map.on('click', clickableLayers, (event) => {
        const h3Cell = event.features?.[0]?.properties?.h3_cell
        if (typeof h3Cell === 'string') dispatch({ type: 'SELECT_CELL', cell: h3Cell })
      })
      map.on('mouseenter', clickableLayers, () => {
        map.getCanvas().style.cursor = 'pointer'
      })
      map.on('mouseleave', clickableLayers, () => {
        map.getCanvas().style.cursor = ''
      })

      setMapReady(true)
    })

    return () => {
      map.remove()
      mapRef.current = null
      setMapReady(false)
    }
  }, [dispatch])

  // PM2.5 / forecast layer data — switches source with the timeline, and
  // feeds both the per-hex detail source and the country-wide overview
  // source (same cells/values, two different geometries — see the layer
  // setup above).
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    const source = mapRef.current.getSource(SOURCE_PM25)
    const overviewSource = mapRef.current.getSource(SOURCE_OVERVIEW)
    if (!(source instanceof GeoJSONSource) || !(overviewSource instanceof GeoJSONSource)) return

    let cells: CellValue[] | null = null
    if (state.horizon === 'now') {
      if (currentGrid.status === 'success') {
        cells = currentGrid.data.map((cell) => ({ h3Cell: cell.h3_cell, value: cell.pm25 }))
      }
    } else if (forecastGrid.status === 'success') {
      cells = forecastGrid.data.map((forecast) => ({
        h3Cell: forecast.h3_cell,
        value: forecast.predicted_pm25,
      }))
    }
    if (!cells) return
    source.setData(cellsToFeatureCollection(cells))
    overviewSource.setData(cellsToCenterPointFeatureCollection(cells))
  }, [mapReady, state.horizon, currentGrid, forecastGrid])

  // PDI layer data — always from current state; there is no forecasted PDI.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    if (currentGrid.status !== 'success') return
    const cells = currentGrid.data.map((cell) => ({ h3Cell: cell.h3_cell, value: cell.pdi }))
    const source = mapRef.current.getSource(SOURCE_PDI)
    if (source instanceof GeoJSONSource) source.setData(cellsToFeatureCollection(cells))
  }, [mapReady, currentGrid])

  // PDI layer visibility toggle.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return
    mapRef.current.setLayoutProperty(
      LAYER_PDI_FILL,
      'visibility',
      state.showPdi ? 'visible' : 'none',
    )
  }, [mapReady, state.showPdi])

  // Wind arrows.
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
    const source = mapRef.current.getSource(SOURCE_WIND)
    if (source instanceof GeoJSONSource) source.setData(windToFeatureCollection(points))
  }, [mapReady, weather])

  return <div ref={containerRef} className="map-canvas" />
}
