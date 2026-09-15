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

import type { BoundingBox } from './types'

export type LodTier = 'country' | 'state' | 'local'

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

// Below zoom 6: whole-country view, one fixed nationwide fetch (see
// demo_data's continuous field — this is now genuinely hundreds of
// cells covering all of India, not a handful of city markers). From 6
// up to LOCAL_MIN_ZOOM, resolution steps up by exactly one H3 level
// roughly every zoom level (6->4, 7->5, 8->6, 9->7) instead of one flat
// resolution across the whole range — each H3 resolution step is
// already a ~7x jump in cell density on its own, so spreading four
// smaller steps across the state-tier zoom range reveals detail
// progressively instead of one abrupt jump straight to the finest
// resolution. Verified live (see the table in the commit that
// introduced this) against both a full-India and a single-city desktop
// viewport: every step change lands well under GRID_QUERY_MAX_CELLS,
// and each successive resolution's cell count at the zoom where it
// takes over is a modest few-thousand-to-low-tens-of-thousands, not a
// jump from hundreds to tens of thousands in one step like the old
// flat 5->8 mapping. Tuned for an ordinary desktop viewport, same
// caveat as before: a very large/ultra-wide monitor can still
// occasionally exceed the cap at a tier's lowest zoom — the backend
// rejects that request with a clear 422 rather than truncating it
// silently, and StatusBanner already shows that as a normal, retryable
// error rather than a crash, so this is a bounded, visible failure
// mode, not silent data loss or a frozen map.
const COUNTRY_MAX_ZOOM = 6
const STATE_RESOLUTION_STEPS: ReadonlyArray<readonly [maxZoom: number, resolution: number]> = [
  [7, 4],
  [8, 5],
  [9, 6],
  [10, 7],
]
const LOCAL_MIN_ZOOM = 10
const LOCAL_RESOLUTION = 8

export function lodForZoom(zoom: number): Lod {
  if (zoom < COUNTRY_MAX_ZOOM) {
    return { tier: 'country', resolution: 3, scopedToViewport: false }
  }
  if (zoom < LOCAL_MIN_ZOOM) {
    // STATE_RESOLUTION_STEPS' last entry has maxZoom === LOCAL_MIN_ZOOM,
    // so this is guaranteed to find a match given the guard above.
    const resolution = STATE_RESOLUTION_STEPS.find(([maxZoom]) => zoom < maxZoom)![1]
    return { tier: 'state', resolution, scopedToViewport: true }
  }
  return { tier: 'local', resolution: LOCAL_RESOLUTION, scopedToViewport: true }
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
