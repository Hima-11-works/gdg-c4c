// The only module that touches the h3-js library — mirrors the backend's
// own rule (app.domain.h3_grid is its one H3 entry point) so cell-geometry
// calls never scatter across components. Pure data shaping only: turning
// (h3_cell, value) pairs already computed by the backend into GeoJSON for
// MapLibre. No interpolation, estimation, or forecasting happens here.

import { cellToBoundary } from 'h3-js'
import type { Feature, FeatureCollection, Point, Polygon, Position } from 'geojson'

export interface CellValue {
  h3Cell: string
  value: number | null
}

export interface WindPoint {
  h3Cell: string
  latitude: number
  longitude: number
  windSpeed: number
  windDirection: number
}

type CellFeature = Feature<Polygon, { h3_cell: string; value: number | null }>
type WindFeature = Feature<Point, { wind_speed: number; rotation: number }>

export const EMPTY_FEATURE_COLLECTION: FeatureCollection = {
  type: 'FeatureCollection',
  features: [],
}

// A cell's hexagon boundary is a pure function of its h3_cell string — it
// never changes. The map polls /grid/current, /grid/forecast, and
// /weather every 60s (see components/MapPage.tsx), and each poll tick
// hands cellsToFeatureCollection the same ~1900 cells with only `value`
// possibly different, so recomputing cellToBoundary (real trigonometry,
// not a cheap lookup) for every cell on every tick is wasted work. This
// cache is unbounded, which is fine here: the cell set is the fixed H3
// grid covering one configured MVP region, not user-generated data.
const boundaryCache = new Map<string, Position[]>()

function boundaryFor(h3Cell: string): Position[] {
  let boundary = boundaryCache.get(h3Cell)
  if (!boundary) {
    boundary = cellToBoundary(h3Cell, true) as Position[]
    boundaryCache.set(h3Cell, boundary)
  }
  return boundary
}

/** One polygon feature per cell, with `h3_cell` and `value` properties for
 * MapLibre's data-driven styling and click handling. `value` is left as
 * `null` when the backend had no estimate for that cell — features with a
 * null value still render (so the hex boundary is visible), just with no
 * fill color driven by it (see lib/colorScales.ts's fallback color). */
export function cellsToFeatureCollection(
  cells: CellValue[],
): FeatureCollection<Polygon, CellFeature['properties']> {
  const features: CellFeature[] = cells.map((cell) => ({
    type: 'Feature',
    properties: { h3_cell: cell.h3Cell, value: cell.value },
    geometry: {
      type: 'Polygon',
      coordinates: [boundaryFor(cell.h3Cell)],
    },
  }))
  return { type: 'FeatureCollection', features }
}

/** One point feature per weather reading, carrying wind speed/direction as
 * properties. `rotation` is the compass bearing the wind is blowing
 * TOWARD (meteorological wind_direction is where it blows FROM — see the
 * comment in components/MapView.tsx where this feeds the arrow layer). */
export function windToFeatureCollection(
  points: WindPoint[],
): FeatureCollection<Point, WindFeature['properties']> {
  const features: WindFeature[] = points.map((point) => ({
    type: 'Feature',
    properties: {
      wind_speed: point.windSpeed,
      rotation: (point.windDirection + 180) % 360,
    },
    geometry: { type: 'Point', coordinates: [point.longitude, point.latitude] },
  }))
  return { type: 'FeatureCollection', features }
}
