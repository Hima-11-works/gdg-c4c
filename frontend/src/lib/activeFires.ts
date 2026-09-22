// NASA FIRMS active thermal anomalies (VIIRS / MODIS near-real-time).
//
// Two sources, in priority order:
//
//  1. Our own backend, `GET /api/v1/fires` (the default). The backend owns
//     the FIRMS ingest (app.ingestion.firms -> the fire_hotspot table), so
//     the browser gets the same immutable detections without needing a
//     MAP_KEY, without depending on NASA's CORS behaviour, and without
//     re-parsing CSV. This is the path the map uses.
//  2. NASA directly, as a CSV: the open 24-hour global archive (no key), or
//     the keyed "area" API when `VITE_FIRMS_MAP_KEY` is set. Used only when
//     `VITE_FIRMS_ENDPOINT` or `VITE_FIRMS_MAP_KEY` is configured - handy
//     when running the frontend with no backend, or against a proxy that
//     returns the same CSV schema.
//
// Both paths produce the same `ActiveFire[]`, so callers don't care which
// ran. The CSV archive covers the whole planet and can hold tens of
// thousands of rows, so that path filters to the India bounding box and caps
// the feature count - purely to keep the browser renderable, not as a data
// product claim. The API path asks the backend for the India box instead, so
// the filter and cap are enforced server-side.

import { API_BASE_URL } from './api'
import { cellForPoint, resolutionOfCell } from './h3Geometry'
import { INDIA_BBOX } from './lod'
import { severityForFrp } from './fireAnomalies'
import type { FireSeverity } from './fireAnomalies'
import type { Envelope } from './types'
import type { FeatureCollection, Point } from 'geojson'

const FIRMS_AREA_URL = 'https://firms.modaps.eosdis.nasa.gov/api/area/csv'
const FIRMS_OPEN_24H_URL =
  'https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_24h.csv'

/** Filter margin around INDIA_BBOX (degrees) so fires just over a border
 *  still register against nearby Indian cells. */
const BBOX_MARGIN_DEG = 2
/** Hard cap on rendered detections - a busy stubble season can put tens of
 *  thousands of points in the archive, which no browser circle layer
 *  should be handed in one go. The backend applies the same cap. */
const MAX_FEATURES = 2000

/** Window the map asks for, matching the backend's default. */
const SINCE_HOURS = 24

/** True when the frontend should talk to NASA itself instead of our API. */
export function usesDirectFirms(): boolean {
  return (
    (import.meta.env.VITE_FIRMS_ENDPOINT ?? '') !== '' ||
    (import.meta.env.VITE_FIRMS_MAP_KEY ?? '') !== ''
  )
}

/** Resolved FIRMS CSV endpoint: an explicit override wins, then the keyed
 *  area API if a key is configured, then the open global archive. */
export function firmsCsvEndpoint(): string {
  const override = import.meta.env.VITE_FIRMS_ENDPOINT
  if (override) return override
  const mapKey = import.meta.env.VITE_FIRMS_MAP_KEY
  if (mapKey) {
    // `world` stands in for the area parameter; the bbox filter below does
    // the actual clipping, so a broader request is harmless (and avoids a
    // second, FIRMS-specific bbox syntax to keep in sync).
    return `${FIRMS_AREA_URL}/${encodeURIComponent(mapKey)}/VIIRS_SNPP_NRT/world/1`
  }
  return FIRMS_OPEN_24H_URL
}

export interface ActiveFire {
  id: string
  /** The H3 cell the backend snapped this detection to at ingest time.
   *  Undefined on the direct-CSV path, which has no cell of its own. */
  h3Cell?: string
  latitude: number
  longitude: number
  /** Fire Radiative Power, MW — null when the row omits it. */
  frp: number | null
  /** Brightness temperature, Kelvin (bright_ti4 / brightness). */
  brightness: number | null
  /** Normalized confidence, 0–1. */
  confidence: number | null
  /** The raw FIRMS confidence token (`l`/`n`/`h` or a 0–100 string). */
  confidenceLabel: string
  acqDate: string
  acqTime: string
  satellite: string
  /** `D` (day) or `N` (night) overpass. */
  daynight: string
  severity: FireSeverity
}

/** VIIRS reports confidence as a letter, MODIS as 0–100. Normalize both to
 *  0–1 and keep the raw token for display. */
function parseConfidence(raw: string): { value: number | null; label: string } {
  const token = raw.trim()
  const lower = token.toLowerCase()
  if (lower === 'l') return { value: 0.3, label: 'Low' }
  if (lower === 'n') return { value: 0.6, label: 'Nominal' }
  if (lower === 'h') return { value: 0.9, label: 'High' }
  const numeric = Number(token)
  if (Number.isFinite(numeric)) return { value: Math.min(1, Math.max(0, numeric / 100)), label: token }
  return { value: null, label: token }
}

/** Minimal RFC-4180-ish CSV parser: handles quoted fields (with embedded
 *  commas/quotes) and both LF and CRLF line endings. Returns one object per
 *  row keyed by header. Blank lines are skipped. */
function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = []
  let field = ''
  let row: string[] = []
  let quoted = false

  for (let i = 0; i < text.length; i++) {
    const char = text[i]
    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i++
        } else {
          quoted = false
        }
      } else {
        field += char
      }
      continue
    }
    if (char === '"') {
      quoted = true
    } else if (char === ',') {
      row.push(field)
      field = ''
    } else if (char === '\n' || char === '\r') {
      // Treat CRLF as one terminator.
      if (char === '\r' && text[i + 1] === '\n') i++
      row.push(field)
      field = ''
      if (row.some((cell) => cell !== '')) rows.push(row)
      row = []
    } else {
      field += char
    }
  }
  if (field !== '' || row.length > 0) {
    row.push(field)
    if (row.some((cell) => cell !== '')) rows.push(row)
  }

  const [header, ...body] = rows
  if (!header) return []
  const keys = header.map((name) => name.trim().toLowerCase())
  return body.map((cells) => {
    const record: Record<string, string> = {}
    keys.forEach((key, index) => {
      record[key] = cells[index] ?? ''
    })
    return record
  })
}

function inIndiaBbox(latitude: number, longitude: number): boolean {
  return (
    latitude >= INDIA_BBOX.minLat - BBOX_MARGIN_DEG &&
    latitude <= INDIA_BBOX.maxLat + BBOX_MARGIN_DEG &&
    longitude >= INDIA_BBOX.minLon - BBOX_MARGIN_DEG &&
    longitude <= INDIA_BBOX.maxLon + BBOX_MARGIN_DEG
  )
}

/** Parse a FIRMS CSV body into the detections that fall in the India box,
 *  worst FRP first. Exported for testing without a network call. */
export function parseFirmsCsv(text: string): ActiveFire[] {
  const records = parseCsv(text)
  const fires: ActiveFire[] = []

  for (let index = 0; index < records.length; index++) {
    const record = records[index]
    const latitude = Number(record.latitude)
    const longitude = Number(record.longitude)
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue
    if (!inIndiaBbox(latitude, longitude)) continue

    const frpRaw = Number(record.frp)
    const frp = Number.isFinite(frpRaw) ? frpRaw : null
    const brightnessRaw = Number(record.bright_ti4 ?? record.brightness)
    const confidence = parseConfidence(record.confidence ?? '')

    fires.push({
      // Stable-ish identity for popup de-duplication: position + overpass
      // time. Two detections at the same place and minute are the same fire.
      id: `${latitude.toFixed(4)},${longitude.toFixed(4)}@${record.acq_date ?? ''}T${record.acq_time ?? ''}`,
      latitude,
      longitude,
      frp,
      brightness: Number.isFinite(brightnessRaw) ? brightnessRaw : null,
      confidence: confidence.value,
      confidenceLabel: confidence.label,
      acqDate: record.acq_date ?? '',
      acqTime: record.acq_time ?? '',
      satellite: record.satellite ?? '',
      daynight: record.daynight ?? '',
      severity: severityForFrp(frp ?? 0),
    })
  }

  fires.sort((a, b) => (b.frp ?? 0) - (a.frp ?? 0))
  return fires.slice(0, MAX_FEATURES)
}

/** One detection as `GET /api/v1/fires` returns it (see the backend's
 *  FireHotspotOut). Field names follow the domain object there, so they
 *  carry their units. */
interface FireHotspotRow {
  detection_id: string
  h3_cell: string
  latitude: number
  longitude: number
  frp_mw: number
  brightness_ti4_k: number | null
  confidence_raw: string
  confidence_class: string
  acquired_at: string
  satellite: string
  daynight: string | null
}

/** Split an ISO UTC timestamp into the date and HHMM pair the popup shows,
 *  matching what the CSV path gets from FIRMS' acq_date/acq_time columns. */
function acquiredParts(acquiredAt: string): { date: string; time: string } {
  const parsed = new Date(acquiredAt)
  if (Number.isNaN(parsed.getTime())) return { date: '', time: '' }
  const iso = parsed.toISOString()
  return { date: iso.slice(0, 10), time: `${iso.slice(11, 13)}${iso.slice(14, 16)}` }
}

/** Turn the API's rows into the same `ActiveFire` shape the CSV path
 *  produces. Confidence is normalised by the same parser, so a raw token
 *  ('l'/'n'/'h' or a 0-100 string) means the same thing either way. */
export function activeFiresFromApi(rows: FireHotspotRow[]): ActiveFire[] {
  const fires = rows.map((row) => {
    const confidence = parseConfidence(row.confidence_raw ?? '')
    const { date, time } = acquiredParts(row.acquired_at)
    const frp = Number.isFinite(row.frp_mw) ? row.frp_mw : null
    return {
      id: row.detection_id,
      h3Cell: row.h3_cell,
      latitude: row.latitude,
      longitude: row.longitude,
      frp,
      brightness: row.brightness_ti4_k ?? null,
      confidence: confidence.value,
      confidenceLabel: confidence.label,
      acqDate: date,
      acqTime: time,
      satellite: row.satellite ?? '',
      daynight: row.daynight ?? '',
      severity: severityForFrp(frp ?? 0),
    }
  })

  fires.sort((a, b) => (b.frp ?? 0) - (a.frp ?? 0))
  return fires.slice(0, MAX_FEATURES)
}

/** Read the backend's stored FIRMS detections.
 *
 *  Deliberately asks for the whole window with no bbox: the endpoint turns a
 *  bbox into H3 cells at the backend's resolution and refuses a box that
 *  covers too many of them (the same GRID_QUERY_MAX_CELLS guard grid/weather
 *  use), which a country-sized box at a fine resolution always trips. FIRMS
 *  ingestion is already bounded by INGEST_BBOX_*, so the stored set is the
 *  India feed; the client-side filter below is the safety net, exactly as on
 *  the CSV path. */
async function fetchActiveFiresFromApi(): Promise<Envelope<ActiveFire[]>> {
  const params = new URLSearchParams({ since_hours: String(SINCE_HOURS) })
  const response = await fetch(`${API_BASE_URL}/api/v1/fires?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`GET /api/v1/fires failed with status ${response.status}`)
  }
  const body = (await response.json()) as Envelope<FireHotspotRow[]>
  const inIndia = (body.data ?? []).filter((row) => inIndiaBbox(row.latitude, row.longitude))
  return {
    generated_at: body.generated_at,
    // The backend only serves rows a real FIRMS ingest wrote, so this is a
    // real satellite product - never the illustrative mock layer.
    is_demo: false,
    data: activeFiresFromApi(inIndia),
  }
}

/** Fetch the last 24h of FIRMS detections as an `Envelope` so it drops
 *  straight into `useApiResource`, the same hook every backend read uses.
 *
 *  Default: our own API (see the module docstring). Set VITE_FIRMS_ENDPOINT
 *  or VITE_FIRMS_MAP_KEY to talk to NASA directly instead. `is_demo: false`
 *  is deliberate on both paths: this is a real satellite product, not the
 *  illustrative mock layer. */
export async function fetchActiveFires(): Promise<Envelope<ActiveFire[]>> {
  if (!usesDirectFirms()) return fetchActiveFiresFromApi()

  const response = await fetch(firmsCsvEndpoint())
  if (!response.ok) {
    throw new Error(`NASA FIRMS request failed with status ${response.status}`)
  }
  const text = await response.text()
  return {
    generated_at: new Date().toISOString(),
    is_demo: false,
    data: parseFirmsCsv(text),
  }
}

/** GeoJSON for the FIRMS circle layers. Only the properties the paint and
 *  popup need are carried through. */
export function activeFiresFeatureCollection(
  fires: ActiveFire[],
): FeatureCollection<Point, Record<string, string | number | null>> {
  return {
    type: 'FeatureCollection',
    features: fires.map((fire) => ({
      type: 'Feature',
      properties: {
        id: fire.id,
        frp: fire.frp,
        brightness: fire.brightness,
        confidence: fire.confidence,
        confidence_label: fire.confidenceLabel,
        acq_date: fire.acqDate,
        acq_time: fire.acqTime,
        satellite: fire.satellite,
        daynight: fire.daynight,
        severity: fire.severity,
      },
      geometry: { type: 'Point', coordinates: [fire.longitude, fire.latitude] },
    })),
  }
}

/** Detections whose coordinates fall inside `h3Cell`, worst FRP first.
 *
 *  Matched by point-in-cell, not by cell-string equality: the backend snaps
 *  a detection to its own H3_RESOLUTION at ingest time, which is finer than
 *  the cell the drawer selects (a level-of-detail resolution), so the two
 *  strings rarely match. Testing each detection's coordinates at the
 *  selected cell's own resolution is exact and needs no shared resolution. */
export function activeFiresInCell(
  fires: ActiveFire[],
  h3Cell: string | null | undefined,
): ActiveFire[] {
  if (!h3Cell) return []
  const resolution = resolutionOfCell(h3Cell)
  if (resolution === undefined) return []
  return fires
    .filter((fire) => cellForPoint(fire.latitude, fire.longitude, resolution) === h3Cell)
    .sort((a, b) => (b.frp ?? 0) - (a.frp ?? 0))
}

/** Popup body for a clicked FIRMS detection — the raw FIRMS properties,
 *  which is what the brief asks the click to surface. */export function activeFirePopupHtml(props: Record<string, unknown>): string {
  const frp = typeof props.frp === 'number' ? `${props.frp.toFixed(1)} MW` : 'n/a'
  const brightness =
    typeof props.brightness === 'number' ? `${props.brightness.toFixed(1)} K` : 'n/a'
  const confidence =
    typeof props.confidence === 'number'
      ? `${Math.round(props.confidence * 100)}% (${props.confidence_label ?? '—'})`
      : String(props.confidence_label ?? 'n/a')
  const overpass = props.daynight === 'N' ? 'Night' : props.daynight === 'D' ? 'Day' : '—'
  const acquired = `${props.acq_date ?? '—'} ${props.acq_time ?? ''}`.trim()

  return (
    `<strong>NASA FIRMS Active Fire</strong>` +
    `<span class="fire-popup-source">${props.satellite ?? 'VIIRS'} · real satellite detection</span>` +
    `<span>FRP (Fire Radiative Power): <b>${frp}</b></span>` +
    `<span>Brightness: <b>${brightness}</b></span>` +
    `<span>Confidence: <b>${confidence}</b></span>` +
    `<span>Overpass: <b>${overpass}</b> · ${acquired}</span>`
  )
}
