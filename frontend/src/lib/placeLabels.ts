// Place labels for the map's deeper zoom tiers.
//
// Source: the same bundled GeoNames file the search bar reads
// (lib/locations.ts) - states, districts, cities and localities as points, no
// new fetch. That is all the repo has: there are no district *polygons* and no
// road geometry anywhere in it, so these layers name places rather than
// outline them, and the map grows denser by adding finer places as it zooms
// (cities and districts at the state tier, localities at the local tier).
//
// Pure shaping: one point feature per place, carrying only what the label
// layers filter and sort on.

import type { IndiaLocation } from './locations'
import type { FeatureCollection, Point } from 'geojson'

export interface PlaceLabelProperties {
  name: string
  kind: IndiaLocation['t']
}

export function placeLabelsFeatureCollection(
  locations: IndiaLocation[],
): FeatureCollection<Point, PlaceLabelProperties> {
  return {
    type: 'FeatureCollection',
    features: locations.map((location) => ({
      type: 'Feature',
      properties: { name: location.n, kind: location.t },
      geometry: { type: 'Point', coordinates: [location.lon, location.lat] },
    })),
  }
}
