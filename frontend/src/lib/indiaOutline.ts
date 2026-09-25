// India's country outline as a *queryable* geometry, for the two places the
// map has to answer "is this bit of geometry in India?" in JavaScript rather
// than letting MapLibre clip it:
//
//   - the hex-cell view, which drops cells that don't touch India at all
//     (a cell that straddles the border is kept - the overlap is real air);
//   - the smooth raster view, which is otherwise painted across the entire
//     viewport bbox, i.e. over Bangladesh, Nepal and the Bay of Bengal.
//
// The outline itself is the same dissolved country polygon MapView already
// feeds to the india-outline source (see stateBoundaries' INDIA_OUTLINE_URL),
// so the clip and the drawn border can never disagree about where India is.
//
// Why a spatial index rather than plain point-in-polygon: the outline has
// ~3,200 vertices and a country-tier viewport resolves tens of thousands of
// cells (GRID_QUERY_MAX_CELLS is 50,000), so testing every cell against every
// vertex would be ~10^9 operations on each data frame. The bucket grid below
// reduces that to a Set lookup for the overwhelming majority of cells, which
// are nowhere near the border, and only the handful straddling it pay for an
// exact test.

import type { Feature, MultiPolygon, Polygon, Position } from 'geojson'
import { INDIA_OUTLINE_URL } from './stateBoundaries'
import { cellBoundaryRing, cellCenter } from './h3Geometry'
import { mercatorY } from './smoothField'

type CountryFeature = Feature<Polygon | MultiPolygon>

/** Bucket size of the index grid, in degrees. 0.5 deg is ~55 km - fine enough
 *  that a bucket straddling the border is always caught by a vertex or by its
 *  own centre falling inside, coarse enough that all of India is only a few
 *  thousand buckets. */
const BUCKET_DEG = 0.5

export class IndiaOutline {
  /** Outer rings only. The dissolved country outline has no holes, so a ring
   *  is either inside or it isn't - see contains(). */
  private readonly rings: Position[][] = []
  readonly minLon: number
  readonly minLat: number
  readonly maxLon: number
  readonly maxLat: number
  private readonly cols: number
  private readonly rows: number
  /** Bucket indices (row * cols + col) that India passes through or occupies. */
  private readonly buckets = new Set<number>()

  constructor(feature: CountryFeature) {
    const geometry = feature.geometry
    const polygons: Position[][][] =
      geometry.type === 'Polygon' ? [geometry.coordinates] : (geometry as MultiPolygon).coordinates
    for (const polygon of polygons) this.rings.push(polygon[0])

    let minLon = Infinity
    let minLat = Infinity
    let maxLon = -Infinity
    let maxLat = -Infinity
    for (const ring of this.rings) {
      for (const [lon, lat] of ring) {
        if (lon < minLon) minLon = lon
        if (lon > maxLon) maxLon = lon
        if (lat < minLat) minLat = lat
        if (lat > maxLat) maxLat = lat
      }
    }
    this.minLon = minLon
    this.minLat = minLat
    this.maxLon = maxLon
    this.maxLat = maxLat

    this.cols = Math.max(1, Math.ceil((maxLon - minLon) / BUCKET_DEG))
    this.rows = Math.max(1, Math.ceil((maxLat - minLat) / BUCKET_DEG))
    this.buildBuckets()
  }

  private bucketOf(lon: number, lat: number): number {
    const col = Math.min(this.cols - 1, Math.max(0, Math.floor((lon - this.minLon) / BUCKET_DEG)))
    const row = Math.min(this.rows - 1, Math.max(0, Math.floor((lat - this.minLat) / BUCKET_DEG)))
    return row * this.cols + col
  }

  /** Ray casting, same even-odd test stateBoundaries uses. */
  private pointInRing(lon: number, lat: number, ring: Position[]): boolean {
    let inside = false
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0]
      const yi = ring[i][1]
      const xj = ring[j][0]
      const yj = ring[j][1]
      if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
        inside = !inside
      }
    }
    return inside
  }

  /** Exact: is this coordinate inside India's outline? */
  contains(lon: number, lat: number): boolean {
    if (lon < this.minLon || lon > this.maxLon || lat < this.minLat || lat > this.maxLat) {
      return false
    }
    return this.rings.some((ring) => this.pointInRing(lon, lat, ring))
  }

  private buildBuckets(): void {
    // A bucket can only be reached by a cell if India passes through it or
    // fills it. Filling is caught by the centre test; a bucket the border
    // merely clips is caught by the vertex test. Everything else is provably
    // empty and never needs an exact test.
    for (let row = 0; row < this.rows; row++) {
      for (let col = 0; col < this.cols; col++) {
        const lon = this.minLon + (col + 0.5) * BUCKET_DEG
        const lat = this.minLat + (row + 0.5) * BUCKET_DEG
        if (lon > this.maxLon || lat > this.maxLat) continue
        if (this.contains(lon, lat)) this.buckets.add(row * this.cols + col)
      }
    }
    for (const ring of this.rings) {
      for (const [lon, lat] of ring) {
        this.buckets.add(this.bucketOf(lon, lat))
      }
    }
  }

  /** Whether any part of this cell could touch India.
   *
   * The cheap index test first, then - only for a cell that reaches a
   * border-adjacent bucket - an exact test of the cell's centre, its vertices
   * and its edge midpoints. Testing the midpoints as well as the corners is
   * what makes a *partially* overlapping cell survive: a cell whose centre and
   * all six corners sit outside India but whose body clips the border still
   * gets kept, which is the behaviour the hex view wants.
   *
   * The remaining gap is a cell that India enters and leaves without any of
   * those twelve sample points landing inside - i.e. India threading a sliver
   * straight through a ~1 km hexagon. That does not occur at this
   * simplification level, and treating it as "outside" is the safer error. */
  cellTouches(lon: number, lat: number, ring: Position[]): boolean {
    if (lon < this.minLon || lon > this.maxLon || lat < this.minLat || lat > this.maxLat) {
      return false
    }
    // Prove the cell's own bounding box doesn't reach any India bucket.
    let minLon = Infinity
    let minLat = Infinity
    let maxLon = -Infinity
    let maxLat = -Infinity
    for (const [x, y] of ring) {
      if (x < minLon) minLon = x
      if (x > maxLon) maxLon = x
      if (y < minLat) minLat = y
      if (y > maxLat) maxLat = y
    }
    const colFrom = Math.floor((minLon - this.minLon) / BUCKET_DEG)
    const colTo = Math.floor((maxLon - this.minLon) / BUCKET_DEG)
    const rowFrom = Math.floor((minLat - this.minLat) / BUCKET_DEG)
    const rowTo = Math.floor((maxLat - this.minLat) / BUCKET_DEG)
    let nearBorder = false
    for (let row = rowFrom; row <= rowTo && !nearBorder; row++) {
      for (let col = colFrom; col <= colTo; col++) {
        if (this.buckets.has(row * this.cols + col)) {
          nearBorder = true
          break
        }
      }
    }
    if (!nearBorder) return false

    if (this.contains(lon, lat)) return true
    const n = ring.length
    for (let i = 0; i < n; i++) {
      const [x1, y1] = ring[i]
      const [x2, y2] = ring[(i + 1) % n]
      if (this.contains(x1, y1)) return true
      if (this.contains((x1 + x2) / 2, (y1 + y2) / 2)) return true
    }
    return false
  }

  /** Does the H3 cell touch India at all? Convenience wrapper so callers
   *  don't have to pull the boundary and centre out of h3Geometry themselves. */
  cellTouchesH3(h3Cell: string): boolean {
    const [lat, lon] = cellCenter(h3Cell)
    return this.cellTouches(lon, lat, cellBoundaryRing(h3Cell))
  }

  /** An India-shaped alpha mask for a raster covering `bbox` at
   *  `width` x `height`, with the shape grown outward by `marginPx` pixels.
   *
   *  The margin exists so the smooth view doesn't end in a hard aliased line
   *  exactly on the border: the field is interpolated, so letting it run a
   *  little past the coast reads as the coastline fading out rather than as
   *  a rectangle being chopped. It is applied as a wide round-joined stroke
   *  on top of the fill, which is exactly a dilation of the outline.
   *
   *  Rasterized with canvas rather than per-pixel ray casting: the same ~3,200
   *  vertices that make an exact JS test too slow at cell scale are a single
   *  cheap path fill here, and this runs once per rendered frame (not per
   *  animation frame). */
  buildMask(
    bbox: { minLat: number; minLon: number; maxLat: number; maxLon: number },
    width: number,
    height: number,
    marginPx: number,
  ): Uint8ClampedArray {
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')!

    // Rows are distributed in MERCATOR y, not in latitude, and this has to
    // match how the raster itself is built. MapLibre's image source takes
    // these corners as lng/lat but converts them to Mercator to warp the
    // image, so image row n sits at mercY(maxLat) + n/(h-1) *
    // (mercY(minLat) - mercY(maxLat)). buildSmoothFieldGrid lays its rows out
    // the same way. Spacing rows linearly in latitude here instead shifted
    // every row's latitude by up to ~0.7 deg in the middle of the country, so
    // the mask tested a point further south than the row actually showed and
    // the field spilled roughly 20 px past India's northern border - a
    // clip that drifted off the coastline it was supposed to follow.
    const yTop = mercatorY(bbox.maxLat)
    const yBottom = mercatorY(bbox.minLat)
    // Positive: Mercator y grows north, so the northern edge is the larger
    // value and this span is how far we travel DOWN the image. Dividing by
    // (yBottom - yTop) instead puts every row off the top of the canvas and
    // silently blanks the whole field.
    const mercSpan = yTop - yBottom

    const toX = (lon: number) =>
      ((lon - bbox.minLon) / Math.max(bbox.maxLon - bbox.minLon, 1e-9)) * width
    const toY = (lat: number) => ((yTop - mercatorY(lat)) / mercSpan) * height

    ctx.beginPath()
    for (const ring of this.rings) {
      for (let i = 0; i < ring.length; i++) {
        const x = toX(ring[i][0])
        const y = toY(ring[i][1])
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.closePath()
    }
    ctx.fillStyle = '#fff'
    ctx.fill()
    if (marginPx > 0) {
      ctx.lineWidth = marginPx * 2
      ctx.lineJoin = 'round'
      ctx.lineCap = 'round'
      ctx.strokeStyle = '#fff'
      ctx.stroke()
    }

    const pixels = ctx.getImageData(0, 0, width, height).data
    const mask = new Uint8ClampedArray(width * height)
    for (let i = 0, p = 3; i < mask.length; i++, p += 4) mask[i] = pixels[p]
    return mask
  }

  /** Knock out every pixel of a rendered field that falls outside India (plus
   *  `marginPx` of slack), leaving the pixels already transparent alone.
   *
   *  The raster is georeferenced to the whole viewport bbox, so without this
   *  the smooth field is painted over neighbouring countries and the sea. The
   *  margin is deliberately kept here rather than baked into the outline: a
   *  field that stops dead on the coastline reads as a clipping artifact,
   *  while one that runs a little past it reads as the data fading out at the
   *  edge of what is covered.
   *
   *  Mutates in place - renderSmoothFieldFromGrid hands back a fresh ImageData
   *  every call, so there's nothing here to copy. */
  clipImageToIndia(
    image: ImageData,
    bbox: { minLat: number; minLon: number; maxLat: number; maxLon: number },
    marginPx: number,
  ): void {
    const { width, height, data } = image
    const mask = this.buildMask(bbox, width, height, marginPx)
    for (let i = 0, p = 3; i < mask.length; i++, p += 4) {
      if (mask[i] === 0) data[p] = 0
    }
  }
}

let cached: Promise<IndiaOutline> | null = null

/** Fetches and indexes India's outline once per session. Every caller shares
 *  the one in-flight promise; the MapLibre india-outline source fetches the
 *  same URL separately, which is just a browser-cached second request. */
export function loadIndiaOutline(): Promise<IndiaOutline> {
  cached ??= fetch(INDIA_OUTLINE_URL).then((response) => {
    if (!response.ok) {
      throw new Error(`Failed to load India outline: HTTP ${response.status}`)
    }
    return response.json() as Promise<{ features: CountryFeature[] }>
  }).then((collection) => {
    const feature = collection.features[0]
    if (!feature) throw new Error('India outline has no features')
    return new IndiaOutline(feature)
  })
  return cached
}
