// Citizen fire/burning reports, from the real submissions API
// (GET /api/v1/reports - see lib/api.ts's fetchReports and backend
// app.services.reports). Reports are snapped to an H3 cell by the backend,
// so a report belongs to exactly the cell it was filed in - no hashing, no
// invented attribution.
//
// This module is presentation only: labels, the map pin image, and small
// lookups over whatever the API returned. It never fabricates a report.

import type { FeatureCollection, Point } from 'geojson'
import type {
  FireReportKind,
  FireReportOut,
  FireReportStatus,
  FireReportWithStatus,
} from './types'

/** Human-readable label per report kind. */
export const FIRE_KIND_LABELS: Record<FireReportKind, string> = {
  building_fire: 'Building fire',
  industrial_fire: 'Industrial fire',
  forest_fire: 'Forest fire',
  crop_burning: 'Wood / crop burning',
  other: 'Other burning',
}

/** Smoke slider labels, index 0 = intensity 1. */
export const SMOKE_LABELS = ['Low', 'Moderate', 'High', 'Very high', 'Extreme'] as const

/** Human-readable label per lifecycle status.
 *
 *  The backend is the source of truth for what each status *means*
 *  (GET /api/v1/reports/statuses, and `status_meaning` on every report); this
 *  is only the short label for a chip. The wording is deliberately careful:
 *  "received" and "counted" are different things, and only a corroborated report
 *  is counted. */
export const REPORT_STATUS_LABELS: Record<FireReportStatus, string> = {
  submitted: 'Received — awaiting review',
  under_review: 'Under review',
  corroborated: 'Corroborated — counted in the model',
  rejected: 'Reviewed — not accepted',
  expired: 'Expired — too old to act on',
}

/** True when this report is currently allowed to change modeled air quality. */
export function reportCountsTowardModel(
  report: Pick<FireReportWithStatus, 'affects_air_quality_model'>,
): boolean {
  return report.affects_air_quality_model === true
}

export function smokeLabel(intensity: number): string {
  const index = Math.min(Math.max(Math.round(intensity), 1), SMOKE_LABELS.length) - 1
  return SMOKE_LABELS[index]
}

/** The report filed in this exact cell, newest first - or null. */
export function reportForCell<T extends FireReportOut>(
  reports: T[],
  h3Cell: string | null | undefined,
): T | null {
  if (!h3Cell) return null
  let newest: T | null = null
  for (const report of reports) {
    if (report.h3_cell !== h3Cell) continue
    if (newest === null || Date.parse(report.reported_at) > Date.parse(newest.reported_at)) {
      newest = report
    }
  }
  return newest
}

/** Minutes since a report was filed, floored at 0. */
export function minutesAgo(reportedAt: string, now: number = Date.now()): number {
  const filed = Date.parse(reportedAt)
  if (Number.isNaN(filed)) return 0
  return Math.max(0, Math.round((now - filed) / 60_000))
}

/** Feature collection of report pins for the map symbol layer.
 *
 *  `counts` rides along as a feature property so the symbol layer can pick a
 *  pin per report. It is the backend's `affects_air_quality_model` copied
 *  straight through, so the map's idea of "this one counts" is the same answer
 *  the model itself uses - the pin cannot disagree with the arithmetic.
 *
 *  Takes the v2 row shape. `FireReportWithStatus` is a superset of the v1 one,
 *  so a caller still holding v1 rows type-checks and every feature simply
 *  reports `counts: false` until the v2 read lands. */
export function reportsFeatureCollection(
  reports: FireReportWithStatus[],
): FeatureCollection<Point, { id: number; kind: FireReportKind; counts: boolean }> {
  return {
    type: 'FeatureCollection',
    features: reports.map((report) => ({
      type: 'Feature',
      properties: {
        id: report.id,
        kind: report.kind,
        counts: reportCountsTowardModel(report),
      },
      geometry: { type: 'Point', coordinates: [report.longitude, report.latitude] },
    })),
  }
}

/** An empty collection - what the source holds before the first fetch. */
export const EMPTY_REPORTS: FeatureCollection = {
  type: 'FeatureCollection',
  features: [],
}

/** Amber camera pin for the map symbol layer, drawn to a canvas so it
 *  renders offline with no image asset to load.
 *
 *  `counts` is the report's own `affects_air_quality_model`, straight from the
 *  backend - never re-derived here. A pin that looks the same whether or not
 *  the report counts is the exact misreading F1 exists to prevent, so the badge
 *  and its ring change: a claim nobody has reviewed is deliberately recessive
 *  (plain white badge, slate ring), and a corroborated one is affirmatively
 *  marked (green-tinted badge, green ring). The amber camera glyph is
 *  unchanged in both, because it says "a person reported a fire here" either
 *  way - only the standing changes. */
export function cameraPinImage(counts: boolean): ImageData {
  const size = 52
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!

  const badgeFill = counts ? '#e7f8ed' : '#f4f5f7'
  const ringStroke = counts ? 'rgba(22, 163, 74, 0.95)' : 'rgba(148, 163, 184, 0.95)'

  // White circular pill/badge with a dark drop shadow, so the amber
  // camera reads against the hot orange/red landed palette beneath it.
  const badgeR = 15
  const badgeX = size / 2
  const badgeY = size / 2 - 4
  ctx.save()
  ctx.shadowColor = 'rgba(0, 0, 0, 0.65)'
  ctx.shadowBlur = 5
  ctx.shadowOffsetY = 2
  ctx.beginPath()
  ctx.arc(badgeX, badgeY, badgeR, 0, Math.PI * 2)
  ctx.fillStyle = badgeFill
  ctx.fill()
  ctx.restore()
  // Ring carries the lifecycle signal; thicker and green once corroborated,
  // hairline and slate while it is only a claim.
  ctx.lineWidth = counts ? 2.5 : 1.5
  ctx.strokeStyle = ringStroke
  ctx.stroke()

  // Pin tail
  ctx.beginPath()
  ctx.moveTo(size / 2, size - 8)
  ctx.lineTo(size / 2 - 4, badgeY + badgeR - 3)
  ctx.lineTo(size / 2 + 4, badgeY + badgeR - 3)
  ctx.closePath()
  ctx.fillStyle = badgeFill
  ctx.shadowColor = 'rgba(0, 0, 0, 0.5)'
  ctx.shadowBlur = 3
  ctx.fill()
  ctx.shadowColor = 'transparent'
  ctx.shadowBlur = 0
  ctx.shadowOffsetY = 0

  // Tail edge in the same ring colour, so the pin reads as one badge rather
  // than a white circle sitting on an unrelated triangle.
  ctx.beginPath()
  ctx.moveTo(size / 2 - 4, badgeY + badgeR - 3)
  ctx.lineTo(size / 2 + 4, badgeY + badgeR - 3)
  ctx.lineWidth = counts ? 2 : 1
  ctx.strokeStyle = ringStroke
  ctx.stroke()

  // Amber camera glyph inside the badge.
  const bodyW = 15
  const bodyH = 10.5
  const bx = size / 2 - bodyW / 2
  const by = badgeY - bodyH / 2
  ctx.beginPath()
  ctx.roundRect(bx, by, bodyW, bodyH, 2.5)
  ctx.fillStyle = '#f59e0b'
  ctx.fill()
  // Viewfinder bump
  ctx.beginPath()
  ctx.roundRect(size / 2 - 3, by - 3, 6, 4, 1)
  ctx.fill()
  // Lens (dark, punched into the amber body)
  ctx.beginPath()
  ctx.arc(size / 2, badgeY + 0.5, 3.1, 0, Math.PI * 2)
  ctx.fillStyle = '#1e2128'
  ctx.fill()
  return ctx.getImageData(0, 0, size, size)
}
