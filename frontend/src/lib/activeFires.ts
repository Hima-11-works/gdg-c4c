// NASA FIRMS active thermal anomalies (VIIRS / MODIS near-real-time).
//
// FIRMS exposes two relevant shapes:
//
//  - The "area" API (`/api/area/csv/{MAP_KEY}/{SOURCE}/{AREA}/{DAYS}`),
//    which needs a free MAP_KEY. Used automatically when
//    `VITE_FIRMS_MAP_KEY` is set.
//  - The open 24-hour global CSV archives under `/data/active_fire/...`,
//    which need no key at all. This is the default, prototyping endpoint.
//
// Either way the response is the same CSV schema, and the browser calls it
// directly. A self-hosted proxy can be dropped in without touching this
// file's callers by setting `VITE_FIRMS_ENDPOINT` to a URL that returns the
// same CSV (see README's deployment notes).
//
// The global archive is large and covers the whole planet, so the parse
// below filters to the India bounding box the map is scoped to and caps the
// feature count — both purely to keep the browser renderable, not as a
// data product claim.

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
/** Hard cap on rendered detections — a busy stubble season can put tens of
 *  thousands of points in the archive, which no browser circle layer
 *  should be handed in one go. */
const MAX_FEATURES = 2000

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

/** Fetch and parse the 24-hour FIRMS feed as an `Envelope` so it drops
 *  straight into `useApiResource`, the same hook every backend read uses.
 *  `is_demo: false` is deliberate: this is a real satellite product, not
 *  the illustrative mock layer. */
export async function fetchActiveFires(): Promise<Envelope<ActiveFire[]>> {
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

/** Popup body for a clicked FIRMS detection — the raw FIRMS properties,
 *  which is what the brief asks the click to surface. */
export function activeFirePopupHtml(props: Record<string, unknown>): string {
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
