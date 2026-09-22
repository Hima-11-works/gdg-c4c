// Smooth pollution field - renders the per-cell PM2.5 values as a continuous
// raster instead of discrete hexagons. A compact, Gaussian-like weighted
// average over nearby cell centers gives a smooth, Perlin-like surface; the
// result is colored with the exact same ramp as the hex view
// (lib/colorScales.ts), so the legend stays valid in both modes.
//
// Cost control: the field is evaluated on a coarse grid and bilinearly
// upsampled to the output image, and the kernel is compact (no exp()), so a
// per-frame render stays well under a few milliseconds during playback.
//
// The coarse grid is exposed separately from the rendering because contrast
// mode draws iso-lines across it (lib/pm25Contours.ts's
// buildSmoothRangeContours): one grid per frame, used twice, so the lines
// trace exactly the surface the raster shows.
//
// Output is an ImageData meant for a MapLibre `image` source georeferenced to
// `bbox`. Rows are mapped linearly in Web Mercator Y (not latitude), because
// MapLibre textures an image source across the projected rectangle of its
// corners - matching that mapping keeps the smooth field aligned with the
// hexagons and coastline instead of drifting at high latitudes.

import type { ColorStop } from './colorScales'
import type { BoundingBox } from './types'

export interface FieldPoint {
  latitude: number
  longitude: number
  value: number
}

/** The smoothed field on its coarse evaluation grid.
 *
 *  `values` and `coverage` are row-major with row 0 on the *north* edge, the
 *  same orientation the raster is textured in (see the module comment on the
 *  Mercator Y mapping). `coverage` is the kernel weight that reached each
 *  node: below MIN_COVERAGE there is no data near it, which is what both the
 *  renderer and the contour builder use to leave an area empty rather than
 *  treating "no data" as a value. */
export interface SmoothFieldGrid {
  width: number
  height: number
  values: Float32Array
  coverage: Float32Array
}

/** Coverage below which a node counts as having no data. The raster leaves
 *  those pixels transparent so the basemap shows through; the contour builder
 *  skips any cell touching one, so iso-lines can't sprout in empty space. */
export const MIN_COVERAGE = 0.02

interface Rgb {
  r: number
  g: number
  b: number
}

function parseHex(hex: string): Rgb {
  const value = Number.parseInt(hex.slice(1), 16)
  return { r: (value >> 16) & 255, g: (value >> 8) & 255, b: value & 255 }
}

/** Linearly interpolate the color ramp at `value` (matching MapLibre's
 *  `interpolate` over the same stops). */
function sampleColor(scale: ColorStop[], value: number): Rgb {
  if (value <= scale[0].value) return parseHex(scale[0].color)
  for (let i = 1; i < scale.length; i++) {
    const hi = scale[i]
    if (value <= hi.value) {
      const lo = scale[i - 1]
      const t = (value - lo.value) / (hi.value - lo.value)
      const a = parseHex(lo.color)
      const b = parseHex(hi.color)
      return {
        r: Math.round(a.r + (b.r - a.r) * t),
        g: Math.round(a.g + (b.g - a.g) * t),
        b: Math.round(a.b + (b.b - a.b) * t),
      }
    }
  }
  return parseHex(scale[scale.length - 1].color)
}

export const mercatorY = (lat: number): number =>
  Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360))
export const inverseMercatorY = (y: number): number =>
  ((2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180) / Math.PI

const MAX_PIXELS = 320
const COARSE = 96

/**
 * Evaluate the smoothed field over `bbox` on its coarse grid. `sigmaKm` is the
 * kernel width (see h3Geometry.hexEdgeKm).
 */
export function buildSmoothFieldGrid(
  points: FieldPoint[],
  bbox: BoundingBox,
  sigmaKm: number,
): SmoothFieldGrid {
  const lonSpan = Math.max(bbox.maxLon - bbox.minLon, 1e-6)
  const latSpan = Math.max(bbox.maxLat - bbox.minLat, 1e-6)

  const coarseW = Math.max(2, Math.round(lonSpan >= latSpan ? COARSE : (COARSE * lonSpan) / latSpan))
  const coarseH = Math.max(2, Math.round(lonSpan >= latSpan ? (COARSE * latSpan) / lonSpan : COARSE))
  const valueGrid = new Float32Array(coarseW * coarseH)
  const coverageGrid = new Float32Array(coarseW * coarseH)
  const grid: SmoothFieldGrid = {
    width: coarseW,
    height: coarseH,
    values: valueGrid,
    coverage: coverageGrid,
  }
  if (points.length === 0) return grid

  const yTop = mercatorY(bbox.maxLat)
  const yBottom = mercatorY(bbox.minLat)
  const midLat = (bbox.maxLat + bbox.minLat) / 2
  const kmPerLonDeg = Math.max(111.32 * Math.cos((midLat * Math.PI) / 180), 1)

  // Compact kernel: w = (1 - d²/R²)² for d < R, cheap and smooth. R is 2.5σ,
  // so a bucket of size R plus its 8 neighbours covers every contributing cell.
  const radius = Math.max(sigmaKm * 2.5, 1e-3)
  const radius2 = radius * radius
  const bucketDeg = Math.max(radius / 111.32, 1e-6)
  const cols = Math.max(1, Math.ceil(lonSpan / bucketDeg))
  const rows = Math.max(1, Math.ceil(latSpan / bucketDeg))
  const buckets = new Map<number, FieldPoint[]>()
  const bucketIndex = (row: number, col: number) => row * cols + col
  const clampCol = (lon: number) =>
    Math.min(cols - 1, Math.max(0, Math.floor((lon - bbox.minLon) / bucketDeg)))
  const clampRow = (lat: number) =>
    Math.min(rows - 1, Math.max(0, Math.floor((lat - bbox.minLat) / bucketDeg)))
  for (const point of points) {
    const index = bucketIndex(clampRow(point.latitude), clampCol(point.longitude))
    const list = buckets.get(index)
    if (list) list.push(point)
    else buckets.set(index, [point])
  }

  for (let gy = 0; gy < coarseH; gy++) {
    const y = yTop + ((yBottom - yTop) * gy) / (coarseH - 1)
    const lat = inverseMercatorY(y)
    const row = clampRow(lat)
    for (let gx = 0; gx < coarseW; gx++) {
      const lon = bbox.minLon + (lonSpan * gx) / (coarseW - 1)
      const col = clampCol(lon)

      let weightSum = 0
      let valueSum = 0
      for (let dr = -1; dr <= 1; dr++) {
        const r = row + dr
        if (r < 0 || r >= rows) continue
        for (let dc = -1; dc <= 1; dc++) {
          const c = col + dc
          if (c < 0 || c >= cols) continue
          const list = buckets.get(bucketIndex(r, c))
          if (!list) continue
          for (const point of list) {
            const dLat = (lat - point.latitude) * 111.32
            const dLon = (lon - point.longitude) * kmPerLonDeg
            const dist2 = dLat * dLat + dLon * dLon
            if (dist2 >= radius2) continue
            const t = 1 - dist2 / radius2
            const weight = t * t
            weightSum += weight
            valueSum += weight * point.value
          }
        }
      }

      const gi = gy * coarseW + gx
      if (weightSum > 1e-4) {
        valueGrid[gi] = valueSum / weightSum
        coverageGrid[gi] = Math.min(1, weightSum)
      }
    }
  }

  return grid
}

/**
 * Colour a grid from buildSmoothFieldGrid into an ImageData, bilinearly
 * upsampled. Data pixels are opaque; where coverage is below MIN_COVERAGE the
 * pixel stays transparent, so the basemap shows through exactly as it does
 * between hexes.
 */
export function renderSmoothFieldFromGrid(
  grid: SmoothFieldGrid,
  bbox: BoundingBox,
  scale: ColorStop[],
): ImageData {
  const { width: coarseW, height: coarseH, values: valueGrid, coverage: coverageGrid } = grid
  const lonSpan = Math.max(bbox.maxLon - bbox.minLon, 1e-6)
  const latSpan = Math.max(bbox.maxLat - bbox.minLat, 1e-6)
  const pixelsPerDeg = MAX_PIXELS / Math.max(lonSpan, latSpan)
  const width = Math.max(2, Math.round(lonSpan * pixelsPerDeg))
  const height = Math.max(2, Math.round(latSpan * pixelsPerDeg))

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')!
  const image = ctx.createImageData(width, height)
  const data = image.data

  for (let py = 0; py < height; py++) {
    const v = ((py / (height - 1)) * (coarseH - 1))
    const y0 = Math.floor(v)
    const y1 = Math.min(y0 + 1, coarseH - 1)
    const fy = v - y0
    for (let px = 0; px < width; px++) {
      const u = (px / (width - 1)) * (coarseW - 1)
      const x0 = Math.floor(u)
      const x1 = Math.min(x0 + 1, coarseW - 1)
      const fx = u - x0

      const i00 = y0 * coarseW + x0
      const i10 = y0 * coarseW + x1
      const i01 = y1 * coarseW + x0
      const i11 = y1 * coarseW + x1
      const w00 = (1 - fx) * (1 - fy)
      const w10 = fx * (1 - fy)
      const w01 = (1 - fx) * fy
      const w11 = fx * fy

      const coverage =
        coverageGrid[i00] * w00 + coverageGrid[i10] * w10 + coverageGrid[i01] * w01 + coverageGrid[i11] * w11
      const offset = (py * width + px) * 4
      if (coverage < MIN_COVERAGE) continue // transparent - no data nearby

      const value =
        valueGrid[i00] * w00 + valueGrid[i10] * w10 + valueGrid[i01] * w01 + valueGrid[i11] * w11
      const rgb = sampleColor(scale, value)
      data[offset] = rgb.r
      data[offset + 1] = rgb.g
      data[offset + 2] = rgb.b
      data[offset + 3] = Math.round(255 * Math.min(1, coverage))
    }
  }

  return image
}
