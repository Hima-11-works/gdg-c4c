// India place search — states, districts, cities, and localities.
//
// Data source: GeoNames India dump (https://download.geonames.org/export/dump/),
// license CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Filtered
// to administrative level 1 (states/UTs) and 2 (districts), populated places
// (cities/towns, plus places within 10 km of a major city so metro
// neighbourhoods like Koramangala are included), and sections of populated
// places (localities). Each entry is {n, t, s, lat, lon}. Loaded lazily on
// first search — 10k+ entries is cheap to filter client-side and keeping it
// out of the initial bundle avoids a payload every map load pays for.
//
// Like lib/stateBoundaries.ts, this is a static asset fetched once and cached
// per session; it is not backend data and has no is_demo concept.

import { MAX_ZOOM } from './lod'

export type LocationKind = 'state' | 'district' | 'city' | 'locality'

export interface IndiaLocation {
  /** Name (ASCII). */
  n: string
  /** Kind. */
  t: LocationKind
  /** Owning state/UT name (equals the name itself for a state). */
  s: string
  lat: number
  lon: number
}

export const LOCATIONS_URL = '/data/india_locations.json'

/** Initial zoom by result kind: a state opens at app Resolution 3, a district
 *  at Resolution 4, and city/locality searches at Resolution 5. */
export const ZOOM_BY_KIND: Record<LocationKind, number> = {
  state: 7,
  district: 9,
  city: MAX_ZOOM,
  locality: MAX_ZOOM,
}

export const KIND_LABEL: Record<LocationKind, string> = {
  state: 'State / UT',
  district: 'District',
  city: 'City',
  locality: 'Locality',
}

/** Plural forms, for prose that talks about a kind of place rather than one
 *  of them (the scope chip's "no boundary data exists for …"). Spelling these
 *  out avoids the "localitys" that naive pluralisation produces. */
export const KIND_PLURAL: Record<LocationKind, string> = {
  state: 'states / UTs',
  district: 'districts',
  city: 'cities',
  locality: 'localities',
}

// Rank by match quality first, then by kind (states/districts more useful
// than a locality of the same name), then shortest name (more likely the
// canonical place).
const KIND_PRIORITY: Record<LocationKind, number> = {
  state: 0,
  district: 1,
  city: 2,
  locality: 3,
}

let cache: Promise<IndiaLocation[]> | null = null

export function loadLocations(): Promise<IndiaLocation[]> {
  cache ??= fetch(LOCATIONS_URL).then((response) => {
    if (!response.ok) {
      throw new Error(`Failed to load locations: HTTP ${response.status}`)
    }
    return response.json() as Promise<IndiaLocation[]>
  })
  return cache
}

/**
 * Ranked substring search over the location list. Matches the name or the
 * owning state's name. Exact match ranks best, then prefix, then substring.
 */
export function searchLocations(
  locations: IndiaLocation[],
  query: string,
  limit = 8,
): IndiaLocation[] {
  const q = query.trim().toLowerCase()
  if (q.length === 0) return []

  const scored: { loc: IndiaLocation; score: number }[] = []
  for (const loc of locations) {
    const name = loc.n.toLowerCase()
    let score: number
    if (name === q) score = 0
    else if (name.startsWith(q)) score = 1
    else if (name.includes(q)) score = 2
    else if (loc.s.toLowerCase().includes(q)) score = 3
    else continue
    scored.push({ loc, score })
  }

  scored.sort((a, b) => {
    if (a.score !== b.score) return a.score - b.score
    const kind = KIND_PRIORITY[a.loc.t] - KIND_PRIORITY[b.loc.t]
    if (kind !== 0) return kind
    return a.loc.n.length - b.loc.n.length
  })

  return scored.slice(0, limit).map((entry) => entry.loc)
}
