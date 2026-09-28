import { compassLabel } from './format'

export interface PlumeDriftEstimate {
  blowsFrom: string
  blowsToward: string
  blowsTowardDeg: number
  speedKmh: number
  speedMs: number
  drift3hKm: number
  drift6hKm: number
}

/**
 * Estimates straight-line wind travel distance. This is not a pollutant plume
 * model; do not infer pollutant movement or downstream air quality from it.
 */
export function estimatePlumeDrift(
  windSpeedMs: number | null,
  windDirectionDeg: number | null,
): PlumeDriftEstimate | null {
  if (
    windSpeedMs === null ||
    !Number.isFinite(windSpeedMs) ||
    windSpeedMs < 0 ||
    windDirectionDeg === null ||
    !Number.isFinite(windDirectionDeg)
  ) {
    return null
  }

  const speedMs = windSpeedMs
  const speedKmh = Math.round(speedMs * 3.6)
  const normalizedDirection = ((windDirectionDeg % 360) + 360) % 360
  const blowsFrom = compassLabel(normalizedDirection)
  const blowsTowardDeg = (normalizedDirection + 180) % 360
  const blowsToward = compassLabel(blowsTowardDeg)

  const drift3hKm = Math.round(speedMs * 3.6 * 3)
  const drift6hKm = Math.round(speedMs * 3.6 * 6)

  return {
    blowsFrom,
    blowsToward,
    blowsTowardDeg,
    speedKmh,
    speedMs,
    drift3hKm,
    drift6hKm,
  }
}
