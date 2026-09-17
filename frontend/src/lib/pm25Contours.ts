// Range contours for "contrast mode": the boundaries between cells that fall
// in different PM2.5 bands. Every shared hex edge belongs to two cells; an edge
// whose two cells are in different bands is a range boundary, and drawing just
// those edges outlines each same-range region topologically — unlike the
// per-cell outline, which would trace every hexagon.
//
// Pure/deterministic: a function of the frame's (cell, value) pairs alone.

import { cellToBoundary } from 'h3-js'
import type { ColorStop } from './colorScales'
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
