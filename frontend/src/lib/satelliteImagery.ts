// NASA GIBS True Color satellite imagery (WMTS/REST tile template), served
// through our own backend.
//
// GIBS publishes VIIRS Suomi-NPP Corrected Reflectance as a plain XYZ
// raster tile pyramid under the `epsg3857` (Web Mercator) WMTS "best"
// endpoint, which is directly consumable by MapLibre's `raster` source. The
// tiles are proxied by GET /api/v1/tiles/gibs/{layer}/{z}/{y}/{x}?date=… so
// the browser only ever talks to our API: the backend holds any credential,
// caches upstream tiles, and can point at a mirror without a frontend change.
//
// Each product is addressed by its UTC date in YYYY-MM-DD. The backend
// selects that date from the product's WMTS time dimension and checks
// representative tiles for the current viewport. Each GIBS product also
// publishes its own tile pyramid, and asking for a
//     level it doesn't have is a 400 upstream. True Color goes to zoom 9
//     (`GoogleMapsCompatible_Level9`); Deep Blue AOD only to 6, so the AOD
//     source caps at 6 and MapLibre overzooms the level-6 tiles past that.
//     Both ceilings are mirrored in the backend's layer table
//     (app.services.tiles.GIBS_LAYERS), which is where the proxy enforces
//     them.

import { API_BASE_URL } from './api'
import type { BoundingBox, GibsLayerAvailability } from './types'

/** The most zoomed-in GIBS tile matrix for the True Color product. */
export const GIBS_MAX_ZOOM = 9
/** ...and for the Deep Blue AOD product, which stops at level 6. */
export const GIBS_AOD_MAX_ZOOM = 6
/** MapLibre raster source tile size — GIBS serves 256px tiles here. */
export const GIBS_TILE_SIZE = 256
/** Required attribution for NASA GIBS imagery. */
export const GIBS_ATTRIBUTION = 'Imagery: NASA GIBS / EOSDIS (VIIRS S-NPP True Color)'

/** Backend tile-proxy paths. The layer keys are the ones the backend
 *  allow-lists (app.services.tiles.GIBS_LAYERS) — it does not accept an
 *  arbitrary GIBS product name, so these two strings are the whole contract. */
const GIBS_PROXY = `${API_BASE_URL}/api/v1/tiles/gibs`
const NO2_PROXY = `${API_BASE_URL}/api/v1/tiles/no2`

const pad2 = (value: number): string => String(value).padStart(2, '0')

/** `YYYY-MM-DD` in UTC, the date format GIBS addresses its daily layer by. */
export function gibsDateString(date: Date = new Date()): string {
  return `${date.getUTCFullYear()}-${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())}`
}

/** The XYZ tile template the proxy is asked for, with the composite date as a
 *  query parameter. Note the `{z}/{y}/{x}` order: GIBS' WMTS REST convention
 *  is row-then-column, and MapLibre honours whatever order the placeholders
 *  appear in — swapping to `{x}/{y}` would request the wrong tiles. */
export function gibsTrueColorTileUrl(date?: Date): string {
  const query = date ? `?date=${gibsDateString(date)}` : ''
  return `${GIBS_PROXY}/truecolor/{z}/{y}/{x}${query}`
}

// ---------------------------------------------------------------------------
// Seasonal smog — VIIRS S-NPP Deep Blue Aerosol Optical Depth (550 nm).
//
// Same GIBS "best available" WMTS convention as True Color, but a `.png`
// product on a coarser pyramid (level 6 - see GIBS_AOD_MAX_ZOOM). Drawn
// semi-transparent (0.6) so the hex grid and basemap stay readable
// underneath.
// ---------------------------------------------------------------------------

/** Opacity the AOD raster sits at — high enough to read the smog, low enough
 *  that the pollution grid still shows through. */
export const GIBS_AOD_OPACITY = 0.6
/** Attribution for the aerosol product (distinct from True Color). */
export const GIBS_AOD_ATTRIBUTION = 'Aerosol: NASA GIBS / EOSDIS (VIIRS S-NPP Deep Blue AOD)'

/** Deep Blue AOD tile template, dated like True Color and proxied the same
 *  way. The upstream product name lives on the backend (the `aod` key in
 *  app.services.tiles.GIBS_LAYERS) now that the browser no longer builds
 *  GIBS URLs itself. */
export function gibsAerosolTileUrl(date?: Date): string {
  const query = date ? `?date=${gibsDateString(date)}` : ''
  return `${GIBS_PROXY}/aod/{z}/{y}/{x}${query}`
}

/** Ask the backend to select the newest advertised product date with usable
 *  representative tiles for this map viewport. The reported fraction is a
 *  tile-response sample, not pixel-level quality or area coverage. */
export async function fetchGibsAvailability(
  layer: 'truecolor' | 'aod',
  bbox: BoundingBox,
  signal?: AbortSignal,
): Promise<GibsLayerAvailability> {
  const query = new URLSearchParams({
    min_lat: String(bbox.minLat),
    min_lon: String(bbox.minLon),
    max_lat: String(bbox.maxLat),
    max_lon: String(bbox.maxLon),
  })
  const response = await fetch(`${GIBS_PROXY}/${layer}/availability?${query}`, { signal })
  if (!response.ok) throw new Error(`GIBS availability request failed (${response.status})`)
  return (await response.json()) as GibsLayerAvailability
}

// ---------------------------------------------------------------------------
// Industrial emissions — Copernicus Sentinel-5P tropospheric NO2.
//
// Sentinel-5P NO2 is served as a WMS layer by the Copernicus Data Space
// Ecosystem (CDSE) / Sentinel Hub, and by Google Earth Engine. Both need an
// account token, which now lives in the *backend* (NO2_WMS_URL / NO2_WMS_TOKEN
// in .env) rather than in a VITE_ variable: the browser only ever calls
// GET /api/v1/tiles/no2/{z}/{y}/{x}, which builds the GetMap request and
// appends the credential server-side. The separate status endpoint reports
// whether the provider is configured, so unconfigured deployments never send
// a JSON error response to MapLibre as a raster image.
// ---------------------------------------------------------------------------

/** Attribution shown once a real NO2 endpoint is configured. */
export const NO2_ATTRIBUTION = 'NO₂: Copernicus Sentinel-5P (ESA)'

/** MapLibre raster tiles entry for the NO2 passthrough. */
export function no2TileUrl(): string {
  return `${NO2_PROXY}/{z}/{y}/{x}`
}

/** Whether the backend has a NO2 endpoint configured. A status endpoint keeps
 *  the unconfigured case out of the raster path: an error response must never
 *  be handed to MapLibre as if it were a PNG tile. */
export async function no2Available(): Promise<boolean> {
  try {
    const response = await fetch(`${NO2_PROXY}/status`)
    if (!response.ok) return false
    const body = (await response.json()) as { available?: unknown }
    return body.available === true
  } catch {
    return false
  }
}
