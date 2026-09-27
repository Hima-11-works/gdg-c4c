// The only module that touches the h3-js library — mirrors the backend's
// own rule (app.domain.h3_grid is its one H3 entry point) so cell-geometry
// calls never scatter across components. Helpers shape backend values for
// MapLibre. A missing child may inherit its parent's display value, but the
// parent's identity stays attached and no numeric interpolation is done.

import {
  cellToBoundary,
  cellToLatLng,
  cellToParent,
  getHexagonEdgeLengthAvg,
  getResolution,
  gridDisk,
  latLngToCell,
} from 'h3-js'
import type { Feature, FeatureCollection, Point, Polygon, Position } from 'geojson'

export interface CellValue {
  h3Cell: string
  value: number | null
  /** True when the map uses a coarser cell's value to shade this cell. */
  generalized?: boolean
  /** Source H3 cell shown when a generalized child is clicked. */
  sourceCell?: string
  /** App-facing resolution level of the source cell. */
  sourceLevel?: number
}

export interface CoarseCellLayer {
  resolution: number
  cells: CellValue[]
}

/** Fill missing display values from the nearest available parent cell. This
 *  is map presentation only: the child retains its own geometry, and clicks
 *  can open the real parent cell that supplied the generalized value. */
export function inheritMissingCellValues(
  cells: CellValue[],
  coarseLayers: CoarseCellLayer[],
): CellValue[] {
  const available = coarseLayers
    .slice()
    .sort((a, b) => b.resolution - a.resolution)
    .map((layer) => ({
      resolution: layer.resolution,
      values: new Map(
        layer.cells
          .filter((cell): cell is CellValue & { value: number } => cell.value !== null)
          .map((cell) => [cell.h3Cell, cell.value]),
      ),
    }))

  return cells.map((cell) => {
    if (cell.value !== null) return cell
    for (const layer of available) {
      if (getResolution(cell.h3Cell) <= layer.resolution) continue
      const parent = cellToParent(cell.h3Cell, layer.resolution)
      const value = layer.values.get(parent)
      if (value !== undefined) {
        return {
          ...cell,
          value,
          generalized: true,
          sourceCell: parent,
          sourceLevel: layer.resolution - 2,
        }
      }
    }
    return cell
  })
}

export interface WindPoint {
  h3Cell: string
  latitude: number
  longitude: number
  windSpeed: number
  windDirection: number
}

type CellFeature = Feature<
  Polygon,
  {
    h3_cell: string
    value: number | null
    generalized: boolean
    source_h3_cell: string
    source_level: number | null
  }
>
type WindFeature = Feature<Point, { wind_speed: number; rotation: number }>

export const EMPTY_FEATURE_COLLECTION: FeatureCollection = {
  type: 'FeatureCollection',
  features: [],
}

// A cell's hexagon boundary is a pure function of its h3_cell string — it
// never changes. The map polls its current level-of-detail data every
// 60s (see components/MapPage.tsx) without the user necessarily having
// moved, and each poll tick hands cellsToFeatureCollection the same
// cells with only `value` possibly different, so recomputing
// cellToBoundary (real trigonometry, not a cheap lookup) for every cell
// on every tick is wasted work.
//
// Bounded rather than left to grow forever: demo_data's continuous
// nationwide field (see backend/app/services/demo_data.py) means a
// session that pans/zooms around several different cities at local-tier
// resolution can rack up tens of thousands of distinct cell ids, each
// worth a handful of coordinate pairs — small individually, not
// negligible after a long session. Simplest possible bound: once the
// cache grows past CACHE_MAX_ENTRIES, drop it and start over, rather
// than a real LRU — a cleared cache just means the next few cells pay
// the recompute cost again, which is cheap.
const CACHE_MAX_ENTRIES = 20_000
const boundaryCache = new Map<string, Position[]>()

/** A cell's center as [latitude, longitude] — used to look up which
 * state a clicked cell falls in (see lib/stateBoundaries.ts), not for
 * rendering (the polygon boundary above is what's drawn). */
export function cellCenter(h3Cell: string): [number, number] {
  return cellToLatLng(h3Cell) as [number, number]
}

/** Immediate H3 neighbors for local comparisons. Keep H3 operations in this
 *  module so rendering and analysis use the same cell-geometry boundary. */
export function neighboringCells(h3Cell: string): string[] {
  try {
    return gridDisk(h3Cell, 1).filter((cell) => cell !== h3Cell)
  } catch {
    return []
  }
}

/** The H3 resolution a cell string was minted at, or undefined if it isn't
 *  a valid cell.
 *
 *  A cell string is only meaningful at its own resolution: the backend's
 *  GET /cells/{h3_cell} validates `resolution` against the cell and returns
 *  422 on a mismatch (app.domain.h3_grid.assert_valid_cell). So this — not
 *  the current zoom tier — is what a selection must ask for. The two
 *  diverge for a moment after every zoom that changes tier: the tier state
 *  updates immediately (see SET_VIEWPORT) while the matching data is still
 *  in flight, so the cells still painted on screen are the previous,
 *  coarser tier's. Reading the resolution off the clicked cell keeps that
 *  window correct instead of erroring. */
export function resolutionOfCell(h3Cell: string): number | undefined {
  try {
    return getResolution(h3Cell)
  } catch {
    return undefined
  }
}

/** The H3 cell a point falls in at `resolution` - the inverse of
 *  cellCenter, used to say which cell a point event (a satellite fire
 *  detection, say) belongs to, in the same terms the drawer's selection is
 *  expressed in. */
export function cellForPoint(latitude: number, longitude: number, resolution: number): string {
  return latLngToCell(latitude, longitude, resolution)
}

/** The boundary of an H3 cell as a closed GeoJSON ring of [lon, lat]
 *  positions, ready to be used as a polygon ring or a hole. */
export function cellBoundaryRing(h3Cell: string): Position[] {
  return cellToBoundary(h3Cell, true) as unknown as Position[]
}

/** Average H3 cell edge length at `resolution`, in km. Sizes the smoothing
 *  kernel of the smooth (raster) view so the blur scales with the grid: a
 *  coarse country-tier grid gets a wide kernel, a fine city-tier grid a
 *  narrow one. */
export function hexEdgeKm(resolution: number): number {
  return getHexagonEdgeLengthAvg(resolution, 'km')
}

function boundaryFor(h3Cell: string): Position[] {
  let boundary = boundaryCache.get(h3Cell)
  if (!boundary) {
    if (boundaryCache.size >= CACHE_MAX_ENTRIES) boundaryCache.clear()
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
    properties: {
      h3_cell: cell.h3Cell,
      value: cell.value,
      generalized: cell.generalized === true,
      source_h3_cell: cell.generalized ? (cell.sourceCell ?? cell.h3Cell) : cell.h3Cell,
      source_level: cell.generalized ? (cell.sourceLevel ?? null) : null,
    },
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
