// Mock VIIRS (Suomi-NPP) 375m active-fire detections for the thermal
// anomaly overlay. Two clusters, matching what the real product shows in
// peak season:
//  - Punjab / Haryana belt (post-monsoon agricultural stubble fires) —
//    dense, high FRP per the example
//  - Eastern MP / Jharkhand / Chhattisgarh industrial & mining belt —
//    fewer, steadier industrial detections
//
// Points stand in for the real VIIRS feed until a fire endpoint exists;
// the GeoJSON shape matches what that feed should return, so the layer
// can be repointed without touching the map code.
//
// Each detection carries a three-step `severity`, the triage key the map
// and the drawer style off: it separates a critical fire from a minor
// trash/stubble burn so response effort is not spent on the small ones.
// Authored here (not computed at render time) so the triage thresholds
// live in one place; the FRP bands behind them are documented on
// FRP_SEVERITY_BANDS.

import type { FeatureCollection, Point } from 'geojson'
import { cellForPoint, resolutionOfCell } from './h3Geometry'

/** 1 = minor/localized, 2 = elevated, 3 = critical. */
export type FireSeverity = 1 | 2 | 3

export interface ThermalAnomaly {
  id: string
  lat: number
  lon: number
  /** Fire Radiative Power, megawatts. */
  frp: number
  /** Minutes since the satellite recorded the detection. */
  detectionMinutesAgo: number
  /** Detection confidence, 0–1. */
  confidence: number
  region: 'stubble' | 'industrial'
  /** Triage severity - see FRP_SEVERITY_BANDS. */
  severity: FireSeverity
}

/** FRP (MW) → severity, the documented rule behind the authored values
 *  below: under 20 MW is a minor/localized burn, 20–50 is elevated, and
 *  50+ is a critical fire worth a response. A triage threshold, not a
 *  fire-science classification. */
export const FRP_SEVERITY_BANDS: ReadonlyArray<{ max: number; severity: FireSeverity }> = [
  { max: 20, severity: 1 },
  { max: 50, severity: 2 },
  { max: Infinity, severity: 3 },
]

export function severityForFrp(frp: number): FireSeverity {
  for (const band of FRP_SEVERITY_BANDS) {
    if (frp < band.max) return band.severity
  }
  return 3
}

/** A priority number for display: severity 3 is Priority 1 (most urgent).
 *  Severity and priority run in opposite directions on purpose - the
 *  satellite product reports severity 1-3, an operations room talks in
 *  Priority 1-3. */
export function priorityForSeverity(severity: FireSeverity): 1 | 2 | 3 {
  return (4 - severity) as 1 | 2 | 3
}

const ANOMALY_SEED: ThermalAnomaly[] = [
  // ── Punjab–Haryana stubble cluster (high confidence, seasonally dense) ──
  { id: 'f1', lat: 31.22, lon: 75.42, frp: 42.8, detectionMinutesAgo: 45, confidence: 0.96, region: 'stubble', severity: 2 },
  { id: 'f2', lat: 31.05, lon: 75.78, frp: 58.3, detectionMinutesAgo: 38, confidence: 0.92, region: 'stubble', severity: 3 },
  { id: 'f3', lat: 30.87, lon: 75.61, frp: 17.4, detectionMinutesAgo: 52, confidence: 0.88, region: 'stubble', severity: 1 },
  { id: 'f4', lat: 31.34, lon: 76.02, frp: 31.2, detectionMinutesAgo: 27, confidence: 0.9, region: 'stubble', severity: 2 },
  { id: 'f5', lat: 29.98, lon: 76.42, frp: 12.8, detectionMinutesAgo: 61, confidence: 0.85, region: 'stubble', severity: 1 },
  { id: 'f6', lat: 30.65, lon: 76.31, frp: 25.9, detectionMinutesAgo: 33, confidence: 0.93, region: 'stubble', severity: 2 },
  { id: 'f7', lat: 30.44, lon: 74.98, frp: 9.6, detectionMinutesAgo: 44, confidence: 0.81, region: 'stubble', severity: 1 },
  { id: 'f8', lat: 29.62, lon: 76.88, frp: 20.1, detectionMinutesAgo: 55, confidence: 0.86, region: 'stubble', severity: 2 },
  { id: 'f9', lat: 30.18, lon: 75.12, frp: 14.7, detectionMinutesAgo: 49, confidence: 0.83, region: 'stubble', severity: 1 },
  { id: 'f10', lat: 31.52, lon: 75.29, frp: 36.5, detectionMinutesAgo: 22, confidence: 0.95, region: 'stubble', severity: 2 },
  { id: 'f11', lat: 29.35, lon: 75.05, frp: 11.3, detectionMinutesAgo: 71, confidence: 0.78, region: 'stubble', severity: 1 },
  { id: 'f12', lat: 30.81, lon: 76.55, frp: 27.4, detectionMinutesAgo: 29, confidence: 0.91, region: 'stubble', severity: 2 },

  // ── Eastern MP / Jharkhand / Chhattisgarh industrial-mining cluster ──
  { id: 'f13', lat: 22.08, lon: 81.66, frp: 88.1, detectionMinutesAgo: 36, confidence: 0.95, region: 'industrial', severity: 3 },
  { id: 'f14', lat: 21.28, lon: 85.24, frp: 46.7, detectionMinutesAgo: 41, confidence: 0.89, region: 'industrial', severity: 2 },
  { id: 'f15', lat: 21.53, lon: 83.02, frp: 33.6, detectionMinutesAgo: 57, confidence: 0.87, region: 'industrial', severity: 2 },
  { id: 'f16', lat: 22.79, lon: 83.97, frp: 52.4, detectionMinutesAgo: 24, confidence: 0.94, region: 'industrial', severity: 3 },
]

/** The kind of response a detection *would* trigger in the authority
 *  workflow the brief describes. Illustrative: no alert is sent, because
 *  no CAQM/SPCB integration exists (see README's known limitations). */
const DETECTION_ACTIONS: Record<ThermalAnomaly['region'], string> = {
  stubble: 'Illustrative: CAQM stubble alert (not sent)',
  industrial: 'Illustrative: SPCB industrial compliance alert (not sent)',
}

/** Feature collection for the thermal-anomaly map layers. `severity` is a
 *  first-class property so the layer paint/filter expressions can style
 *  and triage off it ('frp' stays as the raw, measured-ish value). */
export function fireAnomalyFeatureCollection(): FeatureCollection<
  Point,
  { id: string; frp: number; minutes: number; confidence: number; severity: FireSeverity }
> {
  return {
    type: 'FeatureCollection',
    features: ANOMALY_SEED.map((anomaly) => ({
      type: 'Feature',
      properties: {
        id: anomaly.id,
        frp: anomaly.frp,
        minutes: anomaly.detectionMinutesAgo,
        confidence: anomaly.confidence,
        severity: anomaly.severity,
      },
      geometry: { type: 'Point', coordinates: [anomaly.lon, anomaly.lat] },
    })),
  }
}

export function anomalyById(id: number | string | null | undefined): ThermalAnomaly | null {
  if (id === null || id === undefined) return null
  return ANOMALY_SEED.find((anomaly) => String(anomaly.id) === String(id)) ?? null
}

/** Every detection whose point falls inside `h3Cell`, worst first (then
 *  most recent). Matching is cell-based on purpose: a detection is a point
 *  and the drawer's unit is a cell, so "in this cell" is the only claim
 *  that is actually true - no nearest-neighbour guessing. */
export function anomaliesInCell(h3Cell: string | null | undefined): ThermalAnomaly[] {
  if (!h3Cell) return []
  const resolution = resolutionOfCell(h3Cell)
  if (resolution === undefined) return []

  return ANOMALY_SEED.filter(
    (anomaly) => cellForPoint(anomaly.lat, anomaly.lon, resolution) === h3Cell,
  ).sort((a, b) =>
    b.severity - a.severity !== 0
      ? b.severity - a.severity
      : a.detectionMinutesAgo - b.detectionMinutesAgo,
  )
}

/** The detection that should drive this cell's triage - the worst one, or
 *  null when the cell has none. */
export function worstAnomalyInCell(h3Cell: string | null | undefined): ThermalAnomaly | null {
  return anomaliesInCell(h3Cell)[0] ?? null
}

/** Popup body for a clicked anomaly — provenance, FRP, detection age,
 *  triage severity and the action the authority workflow would take. */
export function anomalyPopupHtml(anomaly: ThermalAnomaly): string {
  const priority = priorityForSeverity(anomaly.severity)
  return (
    `<strong>Satellite Thermal Anomaly</strong>` +
    `<span class="fire-popup-source">Illustrative mock - no VIIRS/satellite ingest exists yet</span>` +
    `<span>FRP (Fire Radiative Power): <b>${anomaly.frp.toFixed(1)} MW</b></span>` +
    `<span>Detection Time: <b>${anomaly.detectionMinutesAgo} mins ago</b></span>` +
    `<span class="fire-popup-action">Priority ${priority} of 3 - ${
      anomaly.severity === 3 ? 'critical' : anomaly.severity === 2 ? 'elevated' : 'minor / localized'
    }</span>` +
    `<span class="fire-popup-action">${DETECTION_ACTIONS[anomaly.region]}</span>`
  )
}
