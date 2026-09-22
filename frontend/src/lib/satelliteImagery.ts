// NASA GIBS True Color satellite imagery (WMTS/REST tile template).
//
// GIBS publishes VIIRS Suomi-NPP Corrected Reflectance as a plain XYZ
// raster tile pyramid under the `epsg3857` (Web Mercator) WMTS "best"
// endpoint, which is directly consumable by MapLibre's `raster` source.
//
// The product is a DAILY composite, addressed by its UTC date in
// YYYY-MM-DD in the URL path. Two facts drive the date logic below:
//
//  1. The imagery is processed with a lag, so *today's* tiles are
//     frequently missing or only partially populated (the classic
//     "half the map is black" bug). Yesterday's composite is reliably
//     complete, so that is the default date.
//  2. `GoogleMapsCompatible_Level9` only exists up to zoom 9. Beyond
//     that GIBS has no tiles, so the source caps its maxzoom and lets
//     MapLibre overzoom the level-9 tiles instead of requesting 404s.

/** The most zoomed-in GIBS tile matrix for this product. */
export const GIBS_MAX_ZOOM = 9
/** MapLibre raster source tile size — GIBS serves 256px tiles here. */
export const GIBS_TILE_SIZE = 256
/** Required attribution for NASA GIBS imagery. */
export const GIBS_ATTRIBUTION = 'Imagery: NASA GIBS / EOSDIS (VIIRS S-NPP True Color)'

const GIBS_BASE =
  'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default'
const GIBS_MATRIX_SET = 'GoogleMapsCompatible_Level9'

const pad2 = (value: number): string => String(value).padStart(2, '0')

/** `YYYY-MM-DD` in UTC, the date format GIBS addresses its daily layer by. */
export function gibsDateString(date: Date = new Date()): string {
  return `${date.getUTCFullYear()}-${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())}`
}

/** Yesterday (UTC) — the newest GIBS daily composite that is reliably
 *  complete. Pass `now` for a deterministic value in tests. */
export function latestImageryDate(now: Date = new Date()): Date {
  const date = new Date(now.getTime())
  date.setUTCDate(date.getUTCDate() - 1)
  return date
}

/** The XYZ tile template with `{Time}` resolved to the given (or latest
 *  complete) UTC date. Note the `{z}/{y}/{x}` order: GIBS' WMTS REST
 *  convention is row-then-column, and MapLibre honours whatever order the
 *  placeholders appear in — swapping to `{x}/{y}` would request the wrong
 *  tiles. */
export function gibsTrueColorTileUrl(date: Date = latestImageryDate()): string {
  return `${GIBS_BASE}/${gibsDateString(date)}/${GIBS_MATRIX_SET}/{z}/{y}/{x}.jpg`
}

// ---------------------------------------------------------------------------
// Seasonal smog — VIIRS S-NPP Deep Blue Aerosol Optical Depth (550 nm).
//
// Same GIBS "best available" WMTS convention as True Color, but a `.png`
// product and the `_Best_Available` suffix: that variant never 404s for a
// missing recent day, it falls back to the newest processed composite, which
// is exactly what a seasonal overlay wants. Drawn semi-transparent (0.6) so
// the hex grid and basemap stay readable underneath.
// ---------------------------------------------------------------------------

/** Opacity the AOD raster sits at — high enough to read the smog, low enough
 *  that the pollution grid still shows through. */
export const GIBS_AOD_OPACITY = 0.6
/** Attribution for the aerosol product (distinct from True Color). */
export const GIBS_AOD_ATTRIBUTION = 'Aerosol: NASA GIBS / EOSDIS (VIIRS S-NPP Deep Blue AOD)'

const GIBS_AOD_BASE =
  'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best' +
  '/VIIRS_SNPP_Deep_Blue_Aerosol_Optical_Depth_550_Land_Best_Available/default'

/** Deep Blue AOD tile template, `{Time}` resolved like True Color. */
export function gibsAerosolTileUrl(date: Date = latestImageryDate()): string {
  return `${GIBS_AOD_BASE}/${gibsDateString(date)}/${GIBS_MATRIX_SET}/{z}/{y}/{x}.png`
}

// ---------------------------------------------------------------------------
// Industrial emissions — Copernicus Sentinel-5P tropospheric NO2.
//
// Sentinel-5P NO2 is served as a WMS layer by the Copernicus Data Space
// Ecosystem (CDSE) / Sentinel Hub, and by Google Earth Engine. Both require
// an account token, so this stays an explicit, clearly-marked placeholder:
// point `VITE_NO2_WMS_URL` at a GetMap endpoint and the layer comes alive
// with no code change. Until then no tiles are requested — showing a
// fabricated heat-map where real NO2 should be would be worse than showing
// nothing at all.
// ---------------------------------------------------------------------------

/** Placeholder: set VITE_NO2_WMS_URL to a Sentinel-5P NO2 WMS GetMap
 *  endpoint (see .env.example) to render real industrial-emission tiles. */
export const NO2_WMS_URL: string | undefined = import.meta.env.VITE_NO2_WMS_URL

/** Attribution shown once a real NO2 endpoint is configured. */
export const NO2_ATTRIBUTION = 'NO₂: Copernicus Sentinel-5P (ESA)'

/** MapLibre raster tiles entry for a WMS layer: wraps the GetMap endpoint in
 *  MapLibre's `{bbox-epsg-3857}` substitution so the source requests the
 *  right extent per tile. Returns null while no endpoint is configured. */
export function no2TileUrl(): string | null {
  if (!NO2_WMS_URL) return null
  const separator = NO2_WMS_URL.includes('?') ? '&' : '?'
  return (
    `${NO2_WMS_URL}${separator}` +
    'service=WMS&version=1.3.0&request=GetMap' +
    '&layers=NO2&styles=&format=image/png&transparent=true' +
    '&crs=EPSG:3857&width=256&height=256&bbox={bbox-epsg-3857}'
  )
}
