// Maps the map's current zoom level to a level-of-detail tier: which H3
// resolution to request from the backend, and whether the request should
// be scoped to the current viewport or always cover all of India. See
// README.md's "Level of detail" section for the full design and the
// backend half of this contract (app.services.grid_query.resolve_cells).
//
// Only three things ever decide what data is on screen: the current
// zoom (via this module), the current viewport (via MapView's moveend
// handler), and the backend's response to those two — nothing here
// fetches more than the current tier needs, at any zoom.

import { hexEdgeKm } from './h3Geometry'
import type { BoundingBox } from './types'
import type { LodQuery } from './api'

export type LodTier = 'country' | 'state'

/** India's approximate extent — the one bbox the country tier always
 * requests explicitly (see MapPage.tsx). The backend only applies
 * `resolution` together with a bbox (see app.services.grid's docstring),
 * so the country tier can't just omit bbox to mean "nationwide"; it has
 * to say so. components/MapView.tsx derives its initial map-fit bounds
 * from this same constant, so the two can't drift apart. */
export const INDIA_BBOX: BoundingBox = { minLat: 6.5, minLon: 68.0, maxLat: 37.5, maxLon: 97.5 }

export interface Lod {
  tier: LodTier
  /** App-facing level: 1 is the India overview. Search can reach level 5. */
  level: 1 | 2 | 3 | 4 | 5
  /** H3 resolution to request from the backend at this tier. */
  resolution: number
  /** Whether a request at this tier should be scoped to the current
   * map viewport. Country tier is never scoped: at its coarse
   * resolution, all of India is only a few hundred cells — scoping it
   * would save nothing worth the complexity. State and local tiers are
   * always scoped, which is the actual point of this design: never ask
   * the backend for fine-resolution data outside what's on screen. */
  scopedToViewport: boolean
}

// App levels map to raw H3 resolutions 3–7. Without a place search the map
// is capped before level 3; a searched scope can reach level 5.
const COUNTRY_MAX_ZOOM = 6
const RESOLUTION_4_MIN_ZOOM = 7
const RESOLUTION_5_MIN_ZOOM = 9
const RESOLUTION_6_MIN_ZOOM = 10.5
const COUNTRY_RESOLUTION = 3
const LEVEL2_RESOLUTION = 4
const LEVEL3_RESOLUTION = 5
const LEVEL4_RESOLUTION = 6
const LEVEL5_RESOLUTION = 7

/** Without search, the map stops at Resolution 2 (raw H3 resolution 4). */
export const MAX_UNSCOPED_ZOOM = 6.99
/** A searched state/place can zoom through app Resolution 5 (raw H3 7). */
export const MAX_SEARCH_ZOOM = 12
// Kept as the general search ceiling for callers that need a location target.
export const MAX_ZOOM = MAX_SEARCH_ZOOM

export function lodForZoom(zoom: number): Lod {
  if (zoom < COUNTRY_MAX_ZOOM) {
    return { tier: 'country', level: 1, resolution: COUNTRY_RESOLUTION, scopedToViewport: false }
  }
  if (zoom < RESOLUTION_4_MIN_ZOOM) {
    return { tier: 'state', level: 2, resolution: LEVEL2_RESOLUTION, scopedToViewport: true }
  }
  if (zoom < RESOLUTION_5_MIN_ZOOM) {
    return { tier: 'state', level: 3, resolution: LEVEL3_RESOLUTION, scopedToViewport: true }
  }
  if (zoom < RESOLUTION_6_MIN_ZOOM) {
    return { tier: 'state', level: 4, resolution: LEVEL4_RESOLUTION, scopedToViewport: true }
  }
  return { tier: 'state', level: 5, resolution: LEVEL5_RESOLUTION, scopedToViewport: true }
}

// The wind-arrow layer only ever displays a thinned-down, sparse subset
// of whatever weather points come back (see MapView's thinBySpatialGrid
// — at most ~100 on screen at once), so fetching the country tier's full
// PM2.5 grid resolution (802 cells nationwide) worth of weather points
// on every load and every 60s poll is mostly wasted payload: measured at
// ~210KB for res 3 versus ~30KB for res 2 (113 cells, already close to
// the ~100 the map actually renders). Local weather stays at the grid's
// resolution through res 6; res 7/8 weather is capped at 6 because the wind
// overlay is spatially thinned and doesn't need pollution-grid detail.
const COUNTRY_WEATHER_RESOLUTION = 2
const MAX_WEATHER_RESOLUTION = 6

export function weatherResolutionForLod(lod: Lod): number {
  return lod.tier === 'country'
    ? COUNTRY_WEATHER_RESOLUTION
    : Math.min(lod.resolution, MAX_WEATHER_RESOLUTION)
}

/** A stable primitive key for a level-of-detail query — the cache key for
 *  forecast frames and the dependency key for data fetching. A fresh bbox
 *  object every render would never compare equal, and rounding to ~1km also
 *  means a sub-pixel pan doesn't retrigger a fetch on its own. Shared so
 *  TimelineControl's prefetch and MapPage's read use the exact same key
 *  (same bbox + resolution → same key → same cached frame). */
export function lodKey(query: LodQuery): string {
  if (!query.bbox) return `${query.resolution ?? 'default'}:nationwide`
  const round = (n: number) => Math.round(n * 100) / 100
  const { minLat, minLon, maxLat, maxLon } = query.bbox
  return `${query.resolution ?? 'default'}:${round(minLat)},${round(minLon)},${round(maxLat)},${round(maxLon)}`
}

/** Grow a bbox outward by one cell's radius at `resolution`. The backend
 *  enumerates cells covering a bbox by their CENTER, so a cell straddling the
 *  viewport edge whose center falls just outside is dropped — it visibly fails
 *  to render even though part of it is on screen. A hexagon's circumradius
 *  equals its edge length, so padding by one edge length includes every cell
 *  that overlaps the original bbox. */
function padBbox(bbox: BoundingBox, resolution: number): BoundingBox {
  const edgeKm = hexEdgeKm(resolution)
  const centerLat = (bbox.minLat + bbox.maxLat) / 2
  const dLat = edgeKm / 111.32
  const dLon = edgeKm / (111.32 * Math.max(Math.cos((centerLat * Math.PI) / 180), 0.05))
  return {
    minLat: Math.max(-85, bbox.minLat - dLat),
    minLon: Math.max(-180, bbox.minLon - dLon),
    maxLat: Math.min(85, bbox.maxLat + dLat),
    maxLon: Math.min(180, bbox.maxLon + dLon),
  }
}

/** The bbox a level-of-detail read should cover for this tier/viewport, or
 *  undefined when a scoped tier has no viewport yet (MapView hasn't reported
 *  one). Country tier always covers all of India. Both are padded so border
 *  cells render. */
export function lodBbox(lod: Lod, viewport: BoundingBox | null): BoundingBox | undefined {
  if (!lod.scopedToViewport) return padBbox(INDIA_BBOX, lod.resolution)
  if (!viewport) return undefined
  return padBbox(viewport, lod.resolution)
}

/** The complete query ({resolution, bbox}) for a tier/viewport — one place,
 *  so MapPage's read and TimelineControl's prefetch can't drift (they must
 *  produce identical cache keys). */
export function lodQueryFor(lod: Lod, viewport: BoundingBox | null): LodQuery {
  const bbox = lodBbox(lod, viewport)
  return bbox ? { resolution: lod.resolution, bbox } : { resolution: lod.resolution }
}
