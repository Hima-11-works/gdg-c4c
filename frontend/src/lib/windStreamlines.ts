// Wind streamlines: integrate the per-cell wind field into long polylines
// that cross the map, instead of drawing a separate short tick at each
// sample. This is the visual basis for "wind currents" — the lines follow
// the local wind as they step, so they curve and merge across the viewport.
//
// Pure geometry, no rendering: MapView feeds the result to a line layer.
// The field is evaluated by nearest sample (a uniform spatial hash keeps
// each lookup O(1)), which is plenty for a synoptic wind picture.

import type { Feature, FeatureCollection, LineString } from 'geojson'
import type { BoundingBox, WeatherReadingOut } from './types'

const EARTH_RADIUS_KM = 6371.0088
/** Below this, treat the wind as calm and stop a streamline. */
const CALM_SPEED_MS = 0.4
/** Roughly how many streamline seeds to place across the viewport. */
const TARGET_SEED_COUNT = 130
const MAX_STEPS = 120

function destination(lat: number, lon: number, bearingDeg: number, distanceKm: number): [number, number] {
  const d = distanceKm / EARTH_RADIUS_KM
  const br = (bearingDeg * Math.PI) / 180
  const lat1 = (lat * Math.PI) / 180
  const lon1 = (lon * Math.PI) / 180
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(br),
  )
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(br) * Math.sin(d) * Math.cos(lat1),
      Math.cos(d) - Math.sin(lat1) * Math.sin(lat2),
    )
  return [(lat2 * 180) / Math.PI, ((lon2 * 180) / Math.PI + 540) % 360 - 180]
}

/** Deterministic pseudo-random in [0, 1) from two integer-ish inputs. */
function hash(a: number, b: number): number {
  const x = Math.sin(a * 127.1 + b * 311.7) * 43758.5453
  return x - Math.floor(x)
}

interface WindSample {
  latitude: number
  longitude: number
  speed: number
  direction: number
}

export function buildWindStreamlines(
  readings: WeatherReadingOut[],
  bbox: BoundingBox,
): FeatureCollection<LineString> {
  const samples: WindSample[] = readings
    .filter((r) => r.wind_speed >= CALM_SPEED_MS)
    .map((r) => ({
      latitude: r.latitude,
      longitude: r.longitude,
      speed: r.wind_speed,
      direction: r.wind_direction,
    }))
  if (samples.length === 0) return { type: 'FeatureCollection', features: [] }

  const latSpan = Math.max(bbox.maxLat - bbox.minLat, 0.01)
  const lonSpan = Math.max(bbox.maxLon - bbox.minLon, 0.01)

  // Uniform spatial hash of the samples, one bucket ≈ 1/12 of the viewport.
  const cell = Math.max(latSpan, lonSpan) / 12
  const buckets = new Map<string, WindSample[]>()
  const bucketKey = (i: number, j: number) => `${i}:${j}`
  for (const s of samples) {
    const k = bucketKey(Math.floor(s.latitude / cell), Math.floor(s.longitude / cell))
    const arr = buckets.get(k)
    if (arr) arr.push(s)
    else buckets.set(k, [s])
  }

  const sampleAt = (lat: number, lon: number): WindSample | null => {
    const i = Math.floor(lat / cell)
    const j = Math.floor(lon / cell)
    const lonScale = Math.cos((lat * Math.PI) / 180)
    let best: WindSample | null = null
    let bestDist = Infinity
    for (let di = -1; di <= 1; di++) {
      for (let dj = -1; dj <= 1; dj++) {
        const arr = buckets.get(bucketKey(i + di, j + dj))
        if (!arr) continue
        for (const s of arr) {
          const dLat = s.latitude - lat
          const dLon = (s.longitude - lon) * lonScale
          const d = dLat * dLat + dLon * dLon
          if (d < bestDist) {
            bestDist = d
            best = s
          }
        }
      }
    }
    return best
  }

  const midLat = (bbox.minLat + bbox.maxLat) / 2
  const spanKm = Math.hypot(
    latSpan * 111,
    lonSpan * 111 * Math.cos((midLat * Math.PI) / 180),
  )
  // Step sized so a streamline can cross most of the viewport within
  // MAX_STEPS while keeping the polyline fine enough to look curved.
  const stepKm = Math.max(3, spanKm / 180)

  // Seeds on a jittered grid whose aspect roughly matches the viewport.
  const seedCols = Math.max(6, Math.round(Math.sqrt((TARGET_SEED_COUNT * lonSpan) / latSpan)))
  const seedRows = Math.max(6, Math.round(TARGET_SEED_COUNT / seedCols))
  const marginLat = latSpan * 0.2
  const marginLon = lonSpan * 0.2

  const features: Feature<LineString>[] = []
  for (let row = 0; row < seedRows; row++) {
    for (let col = 0; col < seedCols; col++) {
      const jitterX = hash(row, col) - 0.5
      const jitterY = hash(col + 7, row + 3) - 0.5
      let lat = bbox.minLat + ((row + 0.5 + jitterY * 0.8) * latSpan) / seedRows
      let lon = bbox.minLon + ((col + 0.5 + jitterX * 0.8) * lonSpan) / seedCols

      if (!sampleAt(lat, lon)) continue

      const coordinates: [number, number][] = [[lon, lat]]
      let speedSum = 0
      let speedCount = 0

      for (let step = 0; step < MAX_STEPS; step++) {
        const sample = sampleAt(lat, lon)
        if (!sample || sample.speed < CALM_SPEED_MS) break
        // meteorological direction is where wind blows FROM; flow goes to +180.
        const [nextLat, nextLon] = destination(lat, lon, (sample.direction + 180) % 360, stepKm)
        lat = nextLat
        lon = nextLon
        coordinates.push([lon, lat])
        speedSum += sample.speed
        speedCount++
        if (
          lon < bbox.minLon - marginLon ||
          lon > bbox.maxLon + marginLon ||
          lat < bbox.minLat - marginLat ||
          lat > bbox.maxLat + marginLat
        ) {
          break
        }
      }

      if (coordinates.length >= 3) {
        features.push({
          type: 'Feature',
          properties: { speed: speedCount > 0 ? speedSum / speedCount : 0 },
          geometry: { type: 'LineString', coordinates },
        })
      }
    }
  }

  return { type: 'FeatureCollection', features }
}
