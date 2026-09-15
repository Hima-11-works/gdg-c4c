// India state/UT boundaries, used two ways: components/MapView.tsx hands
// the same URL straight to a MapLibre GeoJSON source (it fetches and
// renders the polygons itself), and findStateForPoint below does a plain
// point-in-polygon test against the parsed features so
// components/CellDetailPanel.tsx can label a clicked cell with the state
// it falls in — the "location/state if available" part of a cell's
// detail view.
//
// public/data/india_states.geojson is a simplified (mapshaper -simplify
// 3%), GADM-derived dataset — a demo-appropriate approximation, not a
// survey-accurate or currently-official one: it predates the 2014 split
// of Telangana out of Andhra Pradesh (still shown merged), and one very
// small territory (Lakshadweep) was dropped by simplification. "Orissa"
// and "Uttaranchal" were renamed to their current names (Odisha,
// Uttarakhand) when this file was prepared; nothing else was corrected.
// Fine for "which state is this, roughly" in an illustrative demo UI —
// not a source of truth for anything else.

import type { Feature, FeatureCollection, MultiPolygon, Polygon, Position } from 'geojson'

export const STATE_BOUNDARIES_URL = '/data/india_states.geojson'

export type StateBoundaries = FeatureCollection<Polygon | MultiPolygon, { name: string }>

let cache: Promise<StateBoundaries> | null = null

/** Fetches and parses the boundaries once per session; every caller
 * (MapView's point-in-polygon lookups, CellDetailPanel) shares the one
 * in-flight/resolved promise rather than re-fetching. MapLibre's own
 * GeoJSON source fetches STATE_BOUNDARIES_URL separately for rendering —
 * that's a normal browser-cached HTTP request, not a second parse of
 * this same data. */
export function loadStateBoundaries(): Promise<StateBoundaries> {
  cache ??= fetch(STATE_BOUNDARIES_URL).then((response) => {
    if (!response.ok) {
      throw new Error(`Failed to load state boundaries: HTTP ${response.status}`)
    }
    return response.json() as Promise<StateBoundaries>
  })
  return cache
}

// Standard ray-casting point-in-polygon test. GeoJSON rings are
// [longitude, latitude] pairs, so this takes (longitude, latitude) in
// that same order rather than the more common (latitude, longitude) —
// findStateForPoint below is the (lat, lon) entry point everything else
// should call.
function pointInRing(longitude: number, latitude: number, ring: Position[]): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    const straddles = yi > latitude !== yj > latitude
    if (straddles && longitude < ((xj - xi) * (latitude - yi)) / (yj - yi) + xi) {
      inside = !inside
    }
  }
  return inside
}

// polygonRings[0] is the outer ring; any further rings are holes.
function pointInPolygon(longitude: number, latitude: number, polygonRings: Position[][]): boolean {
  if (!pointInRing(longitude, latitude, polygonRings[0])) return false
  return polygonRings.slice(1).every((hole) => !pointInRing(longitude, latitude, hole))
}

function featureContains(feature: Feature<Polygon | MultiPolygon>, lat: number, lon: number) {
  const { geometry } = feature
  if (geometry.type === 'Polygon') {
    return pointInPolygon(lon, lat, geometry.coordinates)
  }
  return geometry.coordinates.some((polygonRings) => pointInPolygon(lon, lat, polygonRings))
}

/** The state/UT name whose polygon contains (latitude, longitude), or
 * null if it falls outside every polygon in `boundaries` (open water, a
 * territory this simplified dataset dropped, or just outside India). */
export function findStateForPoint(
  latitude: number,
  longitude: number,
  boundaries: StateBoundaries,
): string | null {
  const match = boundaries.features.find((feature) => featureContains(feature, latitude, longitude))
  return match?.properties.name ?? null
}
