// The corridor axis, as map geometry.
//
// Drawn as a **dashed** line between the two published city coordinates with a
// marker at each sampled cell, because that is literally what the data is: a
// straight-line sampling, not a road route. A solid line in a solid colour would
// invite the reader to trace it as NH-48/NH-44, which the contract forbids.
//
// The dash pattern and the explicit `geometry_source` label are the map's share
// of that caveat; CorridorPanel states it in words next to the same selection.

import type { Feature, FeatureCollection, LineString, Point } from 'geojson'
import { cellCenter } from './h3Geometry'
import { isIllustrativeGeometry } from './corridors'
import type { CorridorCatalogEntry } from './types'

export const CORRIDOR_AXIS_COLOR = '#c084fc'
export const CORRIDOR_CELL_COLOR = '#e9d5ff'

/** The two endpoints as one line. */
function axisFeature(corridor: CorridorCatalogEntry): Feature<LineString> {
  const from = corridor.endpoints[0]
  const to = corridor.endpoints[corridor.endpoints.length - 1]
  return {
    type: 'Feature',
    properties: {
      name: corridor.name,
      corridor_id: corridor.corridor_id,
      geometry_source: corridor.geometry_source,
      illustrative: isIllustrativeGeometry(corridor.geometry_source),
    },
    geometry: {
      type: 'LineString',
      coordinates: [
        [from.longitude, from.latitude],
        [to.longitude, to.latitude],
      ],
    },
  }
}

/** Where the cells are sampled along the axis. These are the catalog's own
 *  count and resolution; the points are the cell centres, not a density claim. */
function cellFeatures(
  corridor: CorridorCatalogEntry,
  cells: string[] | null,
): Feature<Point>[] {
  if (cells === null || cells.length === 0) return []
  return cells.map((cell) => {
    const [latitude, longitude] = cellCenter(cell)
    return {
      type: 'Feature',
      properties: { h3_cell: cell, geometry_source: corridor.geometry_source },
      geometry: { type: 'Point', coordinates: [longitude, latitude] },
    }
  })
}

export function corridorFeatureCollection(
  corridor: CorridorCatalogEntry | null,
  eventCells: string[] | null,
): FeatureCollection<LineString | Point> {
  if (corridor === null) {
    return { type: 'FeatureCollection', features: [] }
  }
  return {
    type: 'FeatureCollection',
    features: [axisFeature(corridor), ...cellFeatures(corridor, eventCells)],
  }
}
