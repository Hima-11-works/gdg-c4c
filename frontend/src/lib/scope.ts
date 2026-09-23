// The searched-place scope: the map is masked down to one place, everything
// outside its area is greyed out, and a chip above the timeline names the
// place and clears the scope again.
//
// Where the area comes from — this is the only interesting decision here.
// States/UTs have a real polygon in public/data/india_states.geojson
// (geoBoundaries ADM1) and districts have one in india_districts.geojson
// (geoBoundaries ADM2), so both scope to that boundary, holes and all. Cities
// and localities have no boundary data anywhere in this repo — the places file
// is 10k+ points with no geometry — so their scope is the H3 hexagon covering
// the place at the detail level the map flies to, and every surface that shows
// it says "approximate". Nothing is invented: the area is either a real
// administrative boundary or a named hexagon, and the chip tells the user
// which one they are looking at.

import { cellBoundaryRing, cellForPoint } from './h3Geometry'
import { lodForZoom } from './lod'
import { findBoundaryByName, findBoundaryForPoint } from './stateBoundaries'
import type { Boundaries, StateBoundaries } from './stateBoundaries'
import type { LocationKind } from './locations'
import type { FeatureCollection, Polygon, Position } from 'geojson'

/** One place, as the search bar knows it. */
export interface PlaceSelection {
  name: string
  kind: LocationKind
  /** Owning state/UT (equals the name itself for a state search). */
  state: string
  latitude: number
  longitude: number
}

/** The area a scope masks down to. */
export type ScopeArea =
  /** A real administrative boundary, resolved by name against the right
   *  dataset and (failing that) by which polygon contains the place. */
  | { type: 'boundary'; dataset: 'state' | 'district'; name: string }
  /** The hexagon covering the place, for kinds with no boundary dataset.
   *  `resolution` is recorded so the chip can name the cell's size honestly. */
  | { type: 'cell'; h3Cell: string; resolution: number }

/** Both boundary collections a scope might need. Each is loaded only while a
 *  scope of that kind is active, so neither is fetched speculatively. */
export interface ScopeBoundaries {
  states: StateBoundaries | null
  districts: Boundaries | null
}

/** How the chip names a scoped area, per place kind. */
export const SCOPE_AREA_LABEL: Record<LocationKind, string> = {
  state: 'state / UT',
  district: 'district',
  city: 'city',
  locality: 'locality',
}

export interface MapScope extends PlaceSelection {
  area: ScopeArea
}

/** The scope a search selection produces. `zoom` is the zoom the map is
 *  about to fly to (see ZOOM_BY_KIND) — it decides the cell size for the
 *  kinds scoped by hexagon, so the scope matches the detail the user is
 *  about to be looking at. */
export function scopeForPlace(place: PlaceSelection, zoom: number): MapScope {
  if (place.kind === 'state' || place.kind === 'district') {
    return {
      ...place,
      area: { type: 'boundary', dataset: place.kind, name: place.name },
    }
  }
  const resolution = lodForZoom(zoom).resolution
  return {
    ...place,
    area: {
      type: 'cell',
      h3Cell: cellForPoint(place.latitude, place.longitude, resolution),
      resolution,
    },
  }
}

/** True when the scope's area is a hexagon rather than a real boundary, i.e.
 *  when the UI has to say "approximate". */
export function isApproximateScope(scope: MapScope): boolean {
  return scope.area.type === 'cell'
}

// The mask is the whole world as one polygon with the scope punched out of
// it as a hole, drawn over the data layers. Using a world-sized outer ring
// rather than the current viewport means the mask never needs rebuilding on
// pan or zoom, and a partially-covered hexagon is greyed only where it falls
// outside the scope — which is exactly the "semi-masked cell" behaviour,
// for free, from the fill's own hole.
const WORLD_RING: Position[] = [
  [-180, -85],
  [180, -85],
  [180, 85],
  [-180, 85],
  [-180, -85],
]

/** The feature a boundary scope resolved to, from the dataset its kind names:
 *  by name first, then (if the two publishers spell it differently — they
 *  disagree on ~23% of district names) by which polygon contains the place. */
function resolveBoundaryFeature(scope: MapScope, boundaries: ScopeBoundaries) {
  const area = scope.area
  if (area.type !== 'boundary') return undefined
  const collection = area.dataset === 'district' ? boundaries.districts : boundaries.states
  if (collection === null) return undefined
  const byName = findBoundaryByName(collection, area.name)
  if (byName !== undefined) return byName
  const containingName = findBoundaryForPoint(scope.latitude, scope.longitude, collection)
  return collection.features.find((feature) => feature.properties.name === containingName)
}

/** The rings that become holes in the mask. Only outer rings are used:
 *  an enclave inside a state polygon is *outside* the state, so it stays
 *  greyed (masked) rather than being punched back out. */
function scopeHoleRings(scope: MapScope, boundaries: ScopeBoundaries): Position[][] | null {
  if (scope.area.type === 'cell') {
    return [cellBoundaryRing(scope.area.h3Cell)]
  }

  const feature = resolveBoundaryFeature(scope, boundaries)
  if (feature === undefined) return null

  return feature.geometry.type === 'Polygon'
    ? [feature.geometry.coordinates[0]]
    : feature.geometry.coordinates.map((polygonRings) => polygonRings[0])
}

/**
 * The mask feature: world polygon minus the scope area. Null when the scope
 * can't be resolved (a boundary scope whose boundary file hasn't loaded, or
 * a name that matches nothing) — callers show no mask rather than an
 * arbitrary one.
 */
export function scopeMask(
  scope: MapScope,
  boundaries: ScopeBoundaries,
): FeatureCollection<Polygon> | null {
  const holes = scopeHoleRings(scope, boundaries)
  if (holes === null) return null

  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: {},
        geometry: { type: 'Polygon', coordinates: [WORLD_RING, ...holes] },
      },
    ],
  }
}

/**
 * Whether a point is inside the scoped place, used to keep clicks on greyed
 * cells from opening a drawer for a cell the user can't actually see.
 *
 * Deliberately answered from the same data the mask is drawn from rather than
 * by hit-testing the rendered mask: a cell scope is an exact cell lookup, and
 * a boundary scope reuses the same point-in-polygon test the drawer's state
 * label already trusts. While the boundary file is still loading there is
 * nothing to judge against, so points stay clickable rather than the whole
 * map going dead.
 */
export function scopeContains(
  scope: MapScope,
  boundaries: ScopeBoundaries,
  latitude: number,
  longitude: number,
): boolean {
  if (scope.area.type === 'cell') {
    return cellForPoint(latitude, longitude, scope.area.resolution) === scope.area.h3Cell
  }
  const feature = resolveBoundaryFeature(scope, boundaries)
  if (feature === undefined) return true
  const collection = scope.area.dataset === 'district' ? boundaries.districts : boundaries.states
  if (collection === null) return true
  return findBoundaryForPoint(latitude, longitude, collection) === feature.properties.name
}
