// Citizen fire/burning reports, from the real submissions API
// (GET /api/v1/reports - see lib/api.ts's fetchReports and backend
// app.services.reports). Reports are snapped to an H3 cell by the backend,
// so a report belongs to exactly the cell it was filed in - no hashing, no
// invented attribution.
//
// This module is presentation only: labels, the map pin image, and small
// lookups over whatever the API returned. It never fabricates a report.

import type { FeatureCollection, Point } from 'geojson'
import { cellToParent } from 'h3-js'
import { resolutionOfCell } from './h3Geometry'
import type { FireReportKind, FireReportOut } from './types'

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

/**
 * The verification state of every resident submission.
 *
 * This is not a status the backend reports — `ReportOut` has no verification
 * field, and the published README states plainly that a citizen report is
 * "subjective, often non-numeric, and unverified", with any trust or moderation
 * layer explicitly unbuilt. So "unverified" is not a guess about a particular
 * row: it is the only true description of every row this endpoint can hold,
 * and the UI says it wherever a resident's number or note is shown, so a
 * reader never mistakes it for a measurement.
 *
 * If the backend ever grows a real verification field, this constant is the
 * one place to replace with that field's value.
 */
export const CITIZEN_VERIFICATION_LABEL = 'Unverified'

export const CITIZEN_VERIFICATION_DETAIL = 'resident submitted, not a measurement'

/** One-line badge text, e.g. for a drawer row or a confirmation. */
export const CITIZEN_VERIFICATION_BADGE = `${CITIZEN_VERIFICATION_LABEL} — ${CITIZEN_VERIFICATION_DETAIL}`

export const CITIZEN_VERIFICATION_TOOLTIP =
  'Submitted by a resident, not a sensor or a satellite. Nothing checks it before it is ' +
  'stored, and the backend reports no verification status for these reports, so treat it ' +
  'as a concern raised rather than a measurement.'

/** What the report form says about the photo and the local reading: neither is
 *  transmitted, because no endpoint accepts them. Kept here so the form and any
 *  other surface that mentions them cannot drift. */
export const LOCAL_ONLY_DETAIL =
  'Kept on this device. There is no photo or sensor upload endpoint, so this is not sent anywhere.'


export function smokeLabel(intensity: number): string {
  const index = Math.min(Math.max(Math.round(intensity), 1), SMOKE_LABELS.length) - 1
  return SMOKE_LABELS[index]
}

/** The report filed in this cell, newest first — or null.
 *
 *  Containment, not equality. The backend snaps a report at the write
 *  resolution (res 8), while the map selects cells at whatever display
 *  resolution the current zoom is on — which is coarser everywhere except the
 *  deepest level. An exact-string match therefore never fired, and the
 *  drawer's citizen-report section was unreachable in practice. A report
 *  belongs to the selected cell when the two share a parent at the coarser of
 *  their resolutions, which is true both for a res-8 report inside a res-6
 *  selection and for a coarser report covering a finer selection. */
export function reportForCell(
  reports: FireReportOut[],
  h3Cell: string | null | undefined,
): FireReportOut | null {
  if (!h3Cell) return null
  const targetResolution = resolutionOfCell(h3Cell)
  if (targetResolution === undefined) return null

  let newest: FireReportOut | null = null
  for (const report of reports) {
    const reportResolution = resolutionOfCell(report.h3_cell)
    if (reportResolution === undefined) continue
    const shared = Math.min(targetResolution, reportResolution)
    let belongs: boolean
    try {
      belongs =
        cellToParent(h3Cell, shared) === cellToParent(report.h3_cell, shared)
    } catch {
      // An unparseable cell on either side: skip rather than guess.
      continue
    }
    if (!belongs) continue
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

/** Feature collection of report pins for the map symbol layer. */
export function reportsFeatureCollection(
  reports: FireReportOut[],
): FeatureCollection<Point, { id: number; kind: FireReportKind }> {
  return {
    type: 'FeatureCollection',
    features: reports.map((report) => ({
      type: 'Feature',
      properties: { id: report.id, kind: report.kind },
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
 *  renders offline with no image asset to load. */
export function cameraPinImage(): ImageData {
  const size = 52
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!

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
  ctx.fillStyle = '#f4f5f7'
  ctx.fill()
  ctx.restore()
  // Hairline ring so the badge survives bright fire-glow adjacency.
  ctx.lineWidth = 1.5
  ctx.strokeStyle = 'rgba(30, 36, 44, 0.55)'
  ctx.stroke()

  // Pin tail
  ctx.beginPath()
  ctx.moveTo(size / 2, size - 8)
  ctx.lineTo(size / 2 - 4, badgeY + badgeR - 3)
  ctx.lineTo(size / 2 + 4, badgeY + badgeR - 3)
  ctx.closePath()
  ctx.fillStyle = '#f4f5f7'
  ctx.shadowColor = 'rgba(0, 0, 0, 0.5)'
  ctx.shadowBlur = 3
  ctx.fill()
  ctx.shadowColor = 'transparent'
  ctx.shadowBlur = 0
  ctx.shadowOffsetY = 0

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
