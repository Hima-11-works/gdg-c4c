// Sampleable 2D wind field built from per-cell weather readings.
//
// Inverse-distance-weights the east/north velocity components of nearby
// readings, so the field is smooth (a nearest-neighbour field would show
// hard Voronoi seams as the animated particles cross cell boundaries). A
// uniform spatial hash keeps each lookup to the 3x3 buckets around a point.

import type { WeatherReadingOut } from './types'

export interface WindVector {
  /** Eastward component, m/s. */
  u: number
  /** Northward component, m/s. */
  v: number
}

interface Sample {
  lat: number
  lon: number
  u: number
  v: number
}

const SOFTENING = 0.02 // deg^2, avoids a singularity exactly on a sample

export class WindField {
  private readonly cell: number
  private readonly buckets = new Map<string, Sample[]>()

  constructor(readings: WeatherReadingOut[]) {
    let minLat = Infinity
    let maxLat = -Infinity
    let minLon = Infinity
    let maxLon = -Infinity
    const samples: Sample[] = []
    for (const r of readings) {
      if (!Number.isFinite(r.latitude) || !Number.isFinite(r.longitude)) continue
      // meteorological direction is where wind blows FROM; the vector points
      // toward +180.
      const toward = ((r.wind_direction + 180) % 360) * (Math.PI / 180)
      samples.push({
        lat: r.latitude,
        lon: r.longitude,
        u: r.wind_speed * Math.sin(toward),
        v: r.wind_speed * Math.cos(toward),
      })
      minLat = Math.min(minLat, r.latitude)
      maxLat = Math.max(maxLat, r.latitude)
      minLon = Math.min(minLon, r.longitude)
      maxLon = Math.max(maxLon, r.longitude)
    }
    const span = Math.max(maxLat - minLat, maxLon - minLon, 0.5)
    this.cell = Math.max(0.15, span / 14)
    for (const s of samples) {
      const k = this.key(Math.floor(s.lat / this.cell), Math.floor(s.lon / this.cell))
      const arr = this.buckets.get(k)
      if (arr) arr.push(s)
      else this.buckets.set(k, [s])
    }
  }

  private key(i: number, j: number): string {
    return `${i}:${j}`
  }

  /** IDW-interpolated wind vector at a point, or null where there is no
   *  nearby data at all. */
  sample(lat: number, lon: number): WindVector | null {
    const i = Math.floor(lat / this.cell)
    const j = Math.floor(lon / this.cell)
    const lonScale = Math.cos((lat * Math.PI) / 180)
    let uSum = 0
    let vSum = 0
    let wSum = 0
    for (let di = -1; di <= 1; di++) {
      for (let dj = -1; dj <= 1; dj++) {
        const arr = this.buckets.get(this.key(i + di, j + dj))
        if (!arr) continue
        for (const s of arr) {
          const dLat = s.lat - lat
          const dLon = (s.lon - lon) * lonScale
          const w = 1 / (dLat * dLat + dLon * dLon + SOFTENING)
          uSum += s.u * w
          vSum += s.v * w
          wSum += w
        }
      }
    }
    if (wSum === 0) return null
    return { u: uSum / wSum, v: vSum / wSum }
  }
}
