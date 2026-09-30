import type { FeatureCollection, Point } from 'geojson'
import type { LocalPollutionHotspot } from './localHotspots'
import type { FireReportWithStatus } from './types'
import { FIRE_KIND_LABELS } from './citizenReports'
import { cellCenter } from './h3Geometry'

export type FireHotspotSignalSource = 'verified-citizen-report' | 'map-data-prediction'

export interface FireHotspotSignalProperties {
  id: string
  source: FireHotspotSignalSource
  h3_cell: string
  severity: number
  report_kind?: string
  reported_at?: string
  pm25?: number
  nearby_median?: number
  delta?: number
  confidence?: number
  is_demo?: boolean
}

/**
 * Signals eligible for the fire / thermal-hotspot map layer.
 * Citizen reports are included only after backend verification. Predictions
 * use the existing local PM2.5 anomaly model and remain explicitly unverified.
 */
export function fireHotspotSignalsFeatureCollection(
  reports: FireReportWithStatus[],
  predictions: LocalPollutionHotspot[],
): FeatureCollection<Point, FireHotspotSignalProperties> {
  return {
    type: 'FeatureCollection',
    features: [
      ...reports
        .filter((report) => report.is_verified === true)
        .map((report) => ({
          type: 'Feature' as const,
          properties: {
            id: `report-${report.id}`,
            source: 'verified-citizen-report' as const,
            h3_cell: report.h3_cell,
            severity: report.smoke_intensity >= 4 ? 3 : report.smoke_intensity >= 3 ? 2 : 1,
            report_kind: report.kind,
            reported_at: report.reported_at,
          },
          geometry: { type: 'Point' as const, coordinates: [report.longitude, report.latitude] },
        })),
      ...predictions.map((prediction) => {
        const [latitude, longitude] = cellCenter(prediction.h3Cell)
        return {
          type: 'Feature' as const,
          properties: {
            id: `prediction-${prediction.h3Cell}`,
            source: 'map-data-prediction' as const,
            h3_cell: prediction.h3Cell,
            severity: prediction.delta >= 50 ? 3 : prediction.delta >= 35 ? 2 : 1,
            pm25: prediction.pm25,
            nearby_median: prediction.nearbyMedian,
            delta: prediction.delta,
            confidence: prediction.confidence,
            is_demo: prediction.isDemo,
          },
          geometry: { type: 'Point' as const, coordinates: [longitude, latitude] },
        }
      }),
    ],
  }
}

function escape(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** Popup copy keeps confirmed reports and map-derived predictions distinct. */
export function fireHotspotSignalPopupHtml(
  properties: Record<string, unknown>,
): string {
  if (properties.source === 'verified-citizen-report') {
    const kind = FIRE_KIND_LABELS[properties.report_kind as keyof typeof FIRE_KIND_LABELS]
    return (
      '<strong>Verified citizen fire report</strong>' +
      '<span class="fire-popup-source">Verified by the report review workflow</span>' +
      (kind ? `<span>Report type: <b>${escape(kind)}</b></span>` : '') +
      `<span>H3 cell: <b>${escape(properties.h3_cell)}</b></span>` +
      `<span>Reported: <b>${escape(properties.reported_at)}</b></span>`
    )
  }

  return (
    '<strong>Predicted fire hotspot — unverified</strong>' +
    '<span class="fire-popup-source">Map-derived local PM2.5 anomaly; not a confirmed fire</span>' +
    `<span>H3 cell: <b>${escape(properties.h3_cell)}</b></span>` +
    `<span>PM2.5: <b>${escape(properties.pm25)} µg/m³</b></span>` +
    `<span>Nearby median: <b>${escape(properties.nearby_median)} µg/m³</b></span>` +
    `<span>Above nearby median: <b>+${escape(properties.delta)} µg/m³</b></span>` +
    (properties.is_demo === true
      ? '<span class="fire-popup-source">Demo map data</span>'
      : '<span class="fire-popup-source">Model signal only — verify on the ground</span>')
  )
}
