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

import type { FeatureCollection, Point } from 'geojson'

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
}

const ANOMALY_SEED: ThermalAnomaly[] = [
  // ── Punjab–Haryana stubble cluster (high confidence, seasonally dense) ──
  { id: 'f1', lat: 31.22, lon: 75.42, frp: 42.8, detectionMinutesAgo: 45, confidence: 0.96, region: 'stubble' },
  { id: 'f2', lat: 31.05, lon: 75.78, frp: 58.3, detectionMinutesAgo: 38, confidence: 0.92, region: 'stubble' },
  { id: 'f3', lat: 30.87, lon: 75.61, frp: 17.4, detectionMinutesAgo: 52, confidence: 0.88, region: 'stubble' },
  { id: 'f4', lat: 31.34, lon: 76.02, frp: 31.2, detectionMinutesAgo: 27, confidence: 0.9, region: 'stubble' },
  { id: 'f5', lat: 29.98, lon: 76.42, frp: 12.8, detectionMinutesAgo: 61, confidence: 0.85, region: 'stubble' },
  { id: 'f6', lat: 30.65, lon: 76.31, frp: 25.9, detectionMinutesAgo: 33, confidence: 0.93, region: 'stubble' },
  { id: 'f7', lat: 30.44, lon: 74.98, frp: 9.6, detectionMinutesAgo: 44, confidence: 0.81, region: 'stubble' },
  { id: 'f8', lat: 29.62, lon: 76.88, frp: 20.1, detectionMinutesAgo: 55, confidence: 0.86, region: 'stubble' },
  { id: 'f9', lat: 30.18, lon: 75.12, frp: 14.7, detectionMinutesAgo: 49, confidence: 0.83, region: 'stubble' },
  { id: 'f10', lat: 31.52, lon: 75.29, frp: 36.5, detectionMinutesAgo: 22, confidence: 0.95, region: 'stubble' },
  { id: 'f11', lat: 29.35, lon: 75.05, frp: 11.3, detectionMinutesAgo: 71, confidence: 0.78, region: 'stubble' },
  { id: 'f12', lat: 30.81, lon: 76.55, frp: 27.4, detectionMinutesAgo: 29, confidence: 0.91, region: 'stubble' },

  // ── Eastern MP / Jharkhand / Chhattisgarh industrial-mining cluster ──
  { id: 'f13', lat: 22.08, lon: 81.66, frp: 88.1, detectionMinutesAgo: 36, confidence: 0.95, region: 'industrial' },
  { id: 'f14', lat: 21.28, lon: 85.24, frp: 46.7, detectionMinutesAgo: 41, confidence: 0.89, region: 'industrial' },
  { id: 'f15', lat: 21.53, lon: 83.02, frp: 33.6, detectionMinutesAgo: 57, confidence: 0.87, region: 'industrial' },
  { id: 'f16', lat: 22.79, lon: 83.97, frp: 52.4, detectionMinutesAgo: 24, confidence: 0.94, region: 'industrial' },
]

/** Deterministic per-point action the response system already ran. */
const DETECTION_ACTIONS: Record<ThermalAnomaly['region'], string> = {
  stubble: 'Automated CAQM Stubble Alert Dispatched',
  industrial: 'Automated SPCB Industrial Compliance Alert Dispatched',
}

/** Feature collection for the thermal-anomaly map layers. */
export function fireAnomalyFeatureCollection(): FeatureCollection<
  Point,
  { id: string; frp: number; minutes: number; confidence: number }
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
      },
      geometry: { type: 'Point', coordinates: [anomaly.lon, anomaly.lat] },
    })),
  }
}

export function anomalyById(id: number | string | null | undefined): ThermalAnomaly | null {
  if (id === null || id === undefined) return null
  return ANOMALY_SEED.find((anomaly) => anomaly.id === String(id)) ?? null
}

/** Popup body for a clicked anomaly — VIIRS provenance, FRP, detection
 *  age and the action the automated dispatcher took. */
export function anomalyPopupHtml(anomaly: ThermalAnomaly): string {
  return (
    `<strong>Satellite Thermal Anomaly</strong>` +
    `<span class="fire-popup-source">Source: VIIRS (Suomi-NPP) 375m Active Fire</span>` +
    `<span>FRP (Fire Radiative Power): <b>${anomaly.frp.toFixed(1)} MW</b></span>` +
    `<span>Detection Time: <b>${anomaly.detectionMinutesAgo} mins ago</b></span>` +
    `<span class="fire-popup-action">${anomaly.confidence >= 0.9 ? '✓ ' : '⚙ '}${DETECTION_ACTIONS[anomaly.region]}</span>`
  )
}
