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

// Level of detail is capped at "level 3", counting the country-wide view as
// level 1:
//   level 1 — H3 res 3, whole country (zoom < 6)
//   level 2 — H3 res 4, viewport-scoped (zoom 6–7)
//   level 3 — H3 res 5, viewport-scoped (zoom >= 7)   ← finest
// Finer resolutions (res 6–8) are deliberately not generated, so the map's
// zoom is capped at MAX_ZOOM to match: zooming in past level 3 would only
// enlarge the same cells without revealing anything new. Each step is a H3
// resolution step (~7x cell density), and every tier stays well under
// GRID_QUERY_MAX_CELLS for an ordinary desktop viewport.
const COUNTRY_MAX_ZOOM = 6
const LEVEL3_MIN_ZOOM = 7
const COUNTRY_RESOLUTION = 3
const LEVEL2_RESOLUTION = 4
const MAX_RESOLUTION = 5

/** Hard zoom ceiling on the map — the zoom at which level 3 (res 5) is
 *  reached. Beyond it there is no finer detail to reveal. Consumed by
 *  MapView's `maxZoom` and by the search bar's per-location zoom. */
export const MAX_ZOOM = 8

export function lodForZoom(zoom: number): Lod {
  if (zoom < COUNTRY_MAX_ZOOM) {
    return { tier: 'country', resolution: COUNTRY_RESOLUTION, scopedToViewport: false }
  }
  if (zoom < LEVEL3_MIN_ZOOM) {
    return { tier: 'state', resolution: LEVEL2_RESOLUTION, scopedToViewport: true }
  }
  return { tier: 'state', resolution: MAX_RESOLUTION, scopedToViewport: true }
}

/** The zoom at which PDI (part of "medium"/state-tier detail and up, not
 * the bare country overview) starts being available — re-exported so
 * components/MapView.tsx's layer setup can't drift from lodForZoom's own
 * country/state breakpoint. */
export const PDI_MIN_ZOOM = COUNTRY_MAX_ZOOM

// The wind-arrow layer only ever displays a thinned-down, sparse subset
// of whatever weather points come back (see MapView's thinBySpatialGrid
// — at most ~100 on screen at once), so fetching the country tier's full
// PM2.5 grid resolution (802 cells nationwide) worth of weather points
// on every load and every 60s poll is mostly wasted payload: measured at
// ~210KB for res 3 versus ~30KB for res 2 (113 cells, already close to
// the ~100 the map actually renders). State/local tiers are left at the
// grid's own resolution — those are already viewport-scoped and small
// enough that a second resolution isn't worth the extra parameter.
const COUNTRY_WEATHER_RESOLUTION = 2

export function weatherResolutionForLod(lod: Lod): number {
  return lod.tier === 'country' ? COUNTRY_WEATHER_RESOLUTION : lod.resolution
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
