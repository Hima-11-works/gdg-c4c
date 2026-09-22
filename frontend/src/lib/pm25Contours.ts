// Range contours for "contrast mode": the boundaries between cells that fall
// in different PM2.5 bands.
//
// Two builders, because the two render modes show the same values differently:
//
//  - buildRangeContours (hex view): every shared hex edge belongs to two
//    cells; an edge whose two cells are in different bands is a range
//    boundary, and drawing just those edges outlines each same-range region
//    topologically - unlike the per-cell outline, which would trace every
//    hexagon.
//  - buildSmoothRangeContours (smooth view): the raster is a continuous
//    surface, so the boundaries are iso-lines. Marching squares across the
//    smoothed field's own grid traces the band thresholds as smooth curves -
//    topographic edges that follow the surface the raster draws, rather than
//    the hexagons underneath it.
//
// Both are pure/deterministic: functions of the frame's data alone.

import { cellToBoundary } from 'h3-js'
import { inverseMercatorY, mercatorY, MIN_COVERAGE } from './smoothField'
import type { SmoothFieldGrid } from './smoothField'
import type { ColorStop } from './colorScales'
import type { BoundingBox } from './types'
import type { MultiLineString, Position } from 'geojson'

export interface ContourCell {
  h3Cell: string
  value: number | null
}

/** Index of the color band `value` falls in — 0 for the lowest stop, up
 *  through the number of stops. Two values share a band iff this matches. */
function bandIndex(scale: ColorStop[], value: number): number {
  let band = 0
  for (let i = 0; i < scale.length; i++) {
    if (value >= scale[i].value) band = i
  }
  return band
}

// Shared hex edges are the same two vertices in both cells' boundaries; rounding
// to ~1m absorbs any float noise while staying far below the vertex spacing.
function edgeKey(a: Position, b: Position): string {
  const ka = `${a[0].toFixed(5)},${a[1].toFixed(5)}`
  const kb = `${b[0].toFixed(5)},${b[1].toFixed(5)}`
  return ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`
}

/**
 * Line segments separating PM2.5 bands across `cells`. Returns null when there
 * is no boundary (all cells in one band, or fewer than two cells).
 */
export function buildRangeContours(
  cells: ContourCell[],
  scale: ColorStop[],
): MultiLineString | null {
  const edges = new Map<string, { a: Position; b: Position; bands: number[] }>()

  for (const cell of cells) {
    if (cell.value === null) continue
    const band = bandIndex(scale, cell.value)
    const ring = cellToBoundary(cell.h3Cell, true) as Position[]
    for (let i = 0; i < ring.length - 1; i++) {
      const a = ring[i]
      const b = ring[i + 1]
      const key = edgeKey(a, b)
      const existing = edges.get(key)
      if (existing) existing.bands.push(band)
      else edges.set(key, { a, b, bands: [band] })
    }
  }

  const lines: Position[][] = []
  for (const edge of edges.values()) {
    // Exactly two cells (an interior edge) of different bands.
    if (edge.bands.length === 2 && edge.bands[0] !== edge.bands[1]) {
      lines.push([edge.a, edge.b])
    }
  }

  return lines.length > 0 ? { type: 'MultiLineString', coordinates: lines } : null
}

// ---------------------------------------------------------------------------
// Smooth-view contours: marching squares over the smoothed field grid.
// ---------------------------------------------------------------------------

/** Grid-space corner offsets, clockwise from the top-left of the cell. */
const CORNERS: ReadonlyArray<readonly [number, number]> = [
  [0, 0], // 00 top-left
  [1, 0], // 10 top-right
  [1, 1], // 11 bottom-right
  [0, 1], // 01 bottom-left
]

/** The two cell edges each marching-squares case crosses, indexed by the
 *  4-bit case (bit 0 = top-left above the threshold, then clockwise).
 *
 *  Cases 5 and 10 are the ambiguous saddle cases: both diagonals are above
 *  (or below) the threshold while the other two corners are not, so the two
 *  segments can be joined either way. They are resolved against the mean of
 *  the four corners, which is the standard choice and keeps a saddle from
 *  flickering between the two readings as the field changes.
 *
 *  Edges: 0 = top, 1 = right, 2 = bottom, 3 = left. */
const EDGE_PAIRS: ReadonlyArray<readonly [number, number]> = [
  [0, 0], // 0  none
  [3, 0], // 1  top-left
  [0, 1], // 2  top-right
  [3, 1], // 3  left-right
  [1, 2], // 4  right-bottom
  [3, 2], // 5  saddle (resolved below)
  [0, 2], // 6  top-bottom
  [3, 2], // 7  left-bottom
  [2, 3], // 8  bottom-left
  [0, 2], // 9  top-bottom
  [0, 1], // 10 saddle (resolved below)
  [1, 2], // 11 right-bottom
  [3, 1], // 12 left-right
  [0, 1], // 13 top-right
  [3, 0], // 14 top-left
  [0, 0], // 15 none
]

/**
 * Iso-lines at the PM2.5 band boundaries across the smoothed field.
 *
 * The thresholds are the colour scale's own stops (every stop except the
 * lowest, which is the floor of the ramp), so these lines mark exactly where
 * the raster crosses into the next band - the smooth-view counterpart of
 * buildRangeContours. Returns null when no boundary exists in the frame.
 */
export function buildSmoothRangeContours(
  grid: SmoothFieldGrid,
  bbox: BoundingBox,
  scale: ColorStop[],
): MultiLineString | null {
  const { width, height, values, coverage } = grid
  if (width < 2 || height < 2) return null

  const thresholds = scale.slice(1).map((stop) => stop.value)
  const lonSpan = bbox.maxLon - bbox.minLon
  const yTop = mercatorY(bbox.maxLat)
  const yBottom = mercatorY(bbox.minLat)

  // Grid space (fractional node coordinates) -> [lon, lat]. Row 0 is the
  // north edge, matching how the raster is textured (see smoothField.ts).
  const toPosition = (gx: number, gy: number): Position => [
    bbox.minLon + (lonSpan * gx) / (width - 1),
    inverseMercatorY(yTop + ((yBottom - yTop) * gy) / (height - 1)),
  ]

  const lines: Position[][] = []
  for (let gy = 0; gy < height - 1; gy++) {
    for (let gx = 0; gx < width - 1; gx++) {
      const base = gy * width + gx
      const indices = [base, base + 1, base + width + 1, base + width]
      // Skip cells touching an uncovered node: the field reads as 0 there, and
      // an iso-line drawn against that would be an artefact of missing data.
      if (indices.some((index) => coverage[index] < MIN_COVERAGE)) continue

      for (const threshold of thresholds) {
        const cornerValues = indices.map((index) => values[index])
        let caseIndex = 0
        for (let corner = 0; corner < 4; corner++) {
          if (cornerValues[corner] >= threshold) caseIndex |= 1 << corner
        }
        if (caseIndex === 0 || caseIndex === 15) continue

        const edgePoint = (edge: number): Position => {
          // Edge n runs from corner n to corner n+1 (wrapping), so its two
          // endpoints are grid neighbours in either x or y.
          const [ax, ay] = CORNERS[edge]
          const [bx, by] = CORNERS[(edge + 1) % 4]
          const va = cornerValues[edge]
          const vb = cornerValues[(edge + 1) % 4]
          const span = vb - va
          const t = span === 0 ? 0.5 : (threshold - va) / span
          return toPosition(gx + ax + (bx - ax) * t, gy + ay + (by - ay) * t)
        }

        let pairs: ReadonlyArray<readonly [number, number]> = [EDGE_PAIRS[caseIndex]]
        if (caseIndex === 5 || caseIndex === 10) {
          const mean = (cornerValues[0] + cornerValues[1] + cornerValues[2] + cornerValues[3]) / 4
          const centreAbove = mean >= threshold
          // 5 = corners 00 and 11 above; 10 = 10 and 01 above.
          const joinTopLeft = (caseIndex === 5) === centreAbove
          pairs = joinTopLeft ? [[3, 0], [1, 2]] : [[0, 1], [2, 3]]
        }

        for (const [from, to] of pairs) {
          if (from === to) continue
          lines.push([edgePoint(from), edgePoint(to)])
        }
      }
    }
  }

  return lines.length > 0 ? { type: 'MultiLineString', coordinates: lines } : null
}
