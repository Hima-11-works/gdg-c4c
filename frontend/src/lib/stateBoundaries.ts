// India state/UT boundaries and country outline, used two ways:
// components/MapView.tsx hands the same URLs straight to MapLibre GeoJSON
// sources (it fetches and renders the polygons itself), and
// findStateForPoint below does a plain point-in-polygon test against the
// parsed features so components/CellDetailPanel.tsx can label a clicked
// cell with the state it falls in — the "location/state if available"
// part of a cell's detail view.
//
// Data source: geoBoundaries ADM1 (Open Database License, ODC-ODbL),
// https://www.geoboundaries.org — the geoBoundaries Global Database of
// Political Administrative Boundaries, maintained by William & Mary's
// geoLab. Simplified with mapshaper (10% keep-shapes) for web use, then
// names normalized from diacritical forms (e.g. "Kashmīr" → "Kashmir")
// to standard ASCII. Includes all 28 states and 8 union territories as
// of the current administrative structure (post-2019 J&K/Ladakh
// reorganization, post-2020 Dadra & Nagar Haveli / Daman & Diu merger).
// The country outline (india_country.geojson) is dissolved from the
// same state-level data with mapshaper -dissolve.
//
// License: ODC-ODbL (Open Database License)
// https://opendatacommons.org/licenses/odbl/1-0/
// Attribution: geoBoundaries, William & Mary geoLab

import type { Feature, FeatureCollection, MultiPolygon, Polygon, Position } from 'geojson'
import { DISTRICT_BOUNDARIES_URL } from './staticLayers'

export const STATE_BOUNDARIES_URL = '/data/india_states.geojson'
export const INDIA_OUTLINE_URL = '/data/india_country.geojson'

/** A boundary collection from either level: ADM1 states/UTs or ADM2
 *  districts. Both are polygons with a single `name` property, so one set of
 *  lookups serves the drawer's "which state is this" label and the place
 *  scope's mask at either level. */
export type Boundaries = FeatureCollection<Polygon | MultiPolygon, { name: string }>
export type StateBoundaries = Boundaries
export type DistrictBoundaries = Boundaries

let cache: Promise<StateBoundaries> | null = null
let districtCache: Promise<DistrictBoundaries> | null = null

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

/** The ADM2 district polygons, loaded on demand: the map draws them from
 *  level 2 (still straight from the URL, through MapLibre) but only a
 *  district-scoped search needs them parsed, so this stays a separate cache
 *  that most sessions never touch. */
export function loadDistrictBoundaries(): Promise<DistrictBoundaries> {
  districtCache ??= fetch(DISTRICT_BOUNDARIES_URL).then((response) => {
    if (!response.ok) {
      throw new Error(`Failed to load district boundaries: HTTP ${response.status}`)
    }
    return response.json() as Promise<DistrictBoundaries>
  })
  return districtCache
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

/** The boundary name whose polygon contains (latitude, longitude), or null if
 * it falls outside every polygon in `boundaries` (open water, an area this
 * simplified dataset dropped, or just outside India). Works on either level -
 * states/UTs or districts - because both are name-tagged polygons. */
export function findBoundaryForPoint(
  latitude: number,
  longitude: number,
  boundaries: Boundaries,
): string | null {
  const match = boundaries.features.find((feature) => featureContains(feature, latitude, longitude))
  return match?.properties.name ?? null
}

/** The feature named `name`, or undefined when neither dataset spells it that
 *  way (the two publishers don't always agree). */
export function findBoundaryByName(
  boundaries: Boundaries,
  name: string,
): Feature<Polygon | MultiPolygon, { name: string }> | undefined {
  return boundaries.features.find((feature) => feature.properties.name === name)
}

/** The state/UT name whose polygon contains (latitude, longitude). Kept as
 *  the drawer's own entry point so its "which state is this" label reads the
 *  same as it always did. */
export function findStateForPoint(
  latitude: number,
  longitude: number,
  boundaries: StateBoundaries,
): string | null {
  return findBoundaryForPoint(latitude, longitude, boundaries)
}
