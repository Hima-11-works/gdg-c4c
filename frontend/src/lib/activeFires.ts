// NASA FIRMS active thermal anomalies (VIIRS / MODIS near-real-time), as the
// backend serves them.
//
// The backend owns this source end to end: app.ingestion.firms fetches the
// feed with the FIRMS MAP_KEY held server-side, parses the CSV, and stores
// immutable FireHotspot rows; GET /api/v1/fires reads them back worst-FRP
// first and capped. The browser therefore needs no FIRMS key, no CSV parser
// and no third-party endpoint - it consumes our own API through
// lib/api.ts's fetchActiveFires.
//
// What is left here is the genuinely frontend-shaped part: the ActiveFire
// view model, the backend-row -> view-model mapping, and the GeoJSON / popup
// renderings the map layers use.

import { severityForFrp } from './fireAnomalies'
import type { FireSeverity } from './fireAnomalies'
import type { FireHotspotOut } from './types'
import type { FeatureCollection, Point } from 'geojson'

export interface ActiveFire {
  id: string
  latitude: number
  longitude: number
  /** Fire Radiative Power, MW - null when the row omits it. */
  frp: number | null
  /** Brightness temperature, Kelvin (the 4 µm band). */
  brightness: number | null
  /** Normalized confidence, 0-1. */
  confidence: number | null
  /** The raw FIRMS confidence token (`l`/`n`/`h` or a 0-100 string). */
  confidenceLabel: string
  /** The backend's normalised class verbatim: `low`|`nominal`|`high`|`unknown`.
   *  Carried separately from the token so the candidate triage can quote the
   *  detector's own classification rather than re-deriving it. */
  confidenceClass: string
  /** The overpass as an ISO instant, so a consumer does not have to reassemble
   *  it from the FIRMS date/time pair. */
  acquiredAt: string
  /** The H3 cell the detection was snapped to at ingest time. */
  h3Cell: string
  acqDate: string
  acqTime: string
  satellite: string
  /** `D` (day) or `N` (night) overpass. */
  daynight: string
  severity: FireSeverity
}

/** VIIRS reports confidence as a letter, MODIS as 0-100. Normalize both to
 *  0-1 and keep the raw token for display. */
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

/** One API row as the map's view model.
 *
 *  The stored detection carries a stable content-derived id, so the old
 *  position+minute identity hack is gone. The overpass is split back into the
 *  FIRMS date/time pair the popup prints (UTC `YYYY-MM-DD` and `HHMM`, the
 *  shape its CSV used), and severity is derived from FRP exactly as before so
 *  the circle layers colour and size identically. */
export function activeFireFromHotspot(row: FireHotspotOut): ActiveFire {
  const acquired = new Date(row.acquired_at)
  const valid = !Number.isNaN(acquired.getTime())
  const iso = valid ? acquired.toISOString() : ''
  const confidence = parseConfidence(row.confidence_raw)

  return {
    id: row.detection_id,
    latitude: row.latitude,
    longitude: row.longitude,
    frp: row.frp_mw,
    brightness: row.brightness_ti4_k,
    confidence: confidence.value,
    confidenceLabel: confidence.label,
    confidenceClass: row.confidence_class,
    acquiredAt: iso,
    h3Cell: row.h3_cell,
    acqDate: iso.slice(0, 10),
    acqTime: iso.slice(11, 16).replace(':', ''),
    satellite: row.satellite,
    daynight: row.daynight ?? '',
    severity: severityForFrp(row.frp_mw),
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

/** Popup body for a clicked FIRMS detection - the raw FIRMS properties,
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
    `<strong>NASA FIRMS Thermal Detection</strong>` +
    `<span class="fire-popup-source">${props.satellite ?? 'VIIRS'} · real satellite detection, not a confirmed ground fire</span>` +
    `<span>FRP (Fire Radiative Power): <b>${frp}</b></span>` +
    `<span>Brightness: <b>${brightness}</b></span>` +
    `<span>Confidence: <b>${confidence}</b></span>` +
    `<span>Overpass: <b>${overpass}</b> · ${acquired}</span>`
  )
}
