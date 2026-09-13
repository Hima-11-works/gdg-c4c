import { useEffect, useRef, useState } from 'react'
import { GeoJSONSource, Map as MapLibreMap, NavigationControl } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { colorScaleExpression, PDI_COLOR_SCALE, PM25_COLOR_SCALE } from '../lib/colorScales'
import {
  cellsToFeatureCollection,
  EMPTY_FEATURE_COLLECTION,
  windToFeatureCollection,
} from '../lib/h3Geometry'
import { useMapUi } from '../state/MapUiContext'
import type { AsyncResource } from '../hooks/useApiResource'
import type { ForecastOut, GridStateOut, WeatherReadingOut } from '../lib/types'

// A free, key-less MapLibre-maintained basemap — good enough for an MVP;
// swap for a hosted style later without touching anything below.
const MAP_STYLE = 'https://demotiles.maplibre.org/style.json'
const INITIAL_CENTER: [number, number] = [-122.4194, 37.7749] // San Francisco, matches backend demo data
const INITIAL_ZOOM = 9

const SOURCE_PM25 = 'cells-pm25'
const LAYER_PM25_FILL = 'cells-pm25-fill'
const LAYER_PM25_OUTLINE = 'cells-pm25-outline'
const SOURCE_PDI = 'cells-pdi'
const LAYER_PDI_FILL = 'cells-pdi-fill'
const SOURCE_WIND = 'wind-points'
const LAYER_WIND = 'wind-arrows'

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
      center: INITIAL_CENTER,
      zoom: INITIAL_ZOOM,
    })
    mapRef.current = map
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')

    map.on('load', () => {
      map.addSource(SOURCE_PM25, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
      map.addLayer({
        id: LAYER_PM25_FILL,
        type: 'fill',
        source: SOURCE_PM25,
        paint: {
          'fill-color': colorScaleExpression(PM25_COLOR_SCALE, 'value'),
          'fill-opacity': 0.65,
        },
      })
      map.addLayer({
        id: LAYER_PM25_OUTLINE,
        type: 'line',
        source: SOURCE_PM25,
        paint: { 'line-color': '#00000040', 'line-width': 1 },
      })

      map.addSource(SOURCE_PDI, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
      map.addLayer({
        id: LAYER_PDI_FILL,
        type: 'fill',
        source: SOURCE_PDI,
        layout: { visibility: 'none' },
        paint: {
          'fill-color': colorScaleExpression(PDI_COLOR_SCALE, 'value'),
          'fill-opacity': 0.55,
        },
      })

      map.addSource(SOURCE_WIND, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
      map.addLayer({
        id: LAYER_WIND,
        type: 'symbol',
        source: SOURCE_WIND,
        layout: {
          'text-field': '↑',
          'text-rotate': ['get', 'rotation'],
          'text-rotation-alignment': 'map',
          'text-allow-overlap': true,
          'text-ignore-placement': true,
          'text-size': ['interpolate', ['linear'], ['get', 'wind_speed'], 0, 12, 15, 26],
        },
        paint: {
          'text-color': '#1d4ed8',
          'text-halo-color': '#ffffff',
          'text-halo-width': 1.5,
        },
      })

      const clickableLayers = [LAYER_PM25_FILL, LAYER_PDI_FILL]
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
