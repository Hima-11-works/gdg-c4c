import { compassLabel } from './format'

export interface PlumeDriftEstimate {
  blowsFrom: string
  blowsToward: string
  blowsTowardDeg: number
  speedKmh: number
  speedMs: number
  drift3hKm: number
  drift6hKm: number
  severity: 'low' | 'moderate' | 'high' | 'severe'
  advisory: string
}

/**
 * Computes forward trans-boundary / inter-district plume dispersion trajectory
 * from meteorological wind speed and direction.
 */
export function estimatePlumeDrift(
  windSpeedMs: number | null,
  windDirectionDeg: number | null,
  pm25: number | null,
): PlumeDriftEstimate | null {
  if (windDirectionDeg === null) return null

  const speedMs = windSpeedMs !== null && windSpeedMs > 0 ? windSpeedMs : 3.5
  const speedKmh = Math.round(speedMs * 3.6)
  const blowsFrom = compassLabel(windDirectionDeg)
  const blowsTowardDeg = (Math.round(windDirectionDeg) + 180) % 360
  const blowsToward = compassLabel(blowsTowardDeg)

  const drift3hKm = Math.round(speedKmh * 3)
  const drift6hKm = Math.round(speedKmh * 6)

  const value = pm25 ?? 50
  let severity: PlumeDriftEstimate['severity'] = 'moderate'
  let advisory = `Particulate plume dispersing ${blowsToward}ward along the prevailing wind vector.`

  if (value >= 250) {
    severity = 'severe'
    advisory = `Critical trans-boundary smog plume: Dense particulates carrying ${blowsToward}ward (~${drift3hKm} km in 3h). High risk of downstream airshed emergency.`
  } else if (value >= 120) {
    severity = 'high'
    advisory = `Elevated trans-boundary dispersion: Heavy smoke plume advancing ${blowsToward}ward (~${drift3hKm} km in 3h). Downwind districts will see spikes.`
  } else if (value >= 60) {
    severity = 'moderate'
    advisory = `Active wind corridor: Plume tracking ${blowsToward}ward (~${drift3hKm} km in 3h) into adjacent administrative boundaries.`
  } else {
    severity = 'low'
    advisory = `Favorable dispersion: Light background particulates drifting ${blowsToward}ward at ${speedKmh} km/h.`
  }

  return {
    blowsFrom,
    blowsToward,
    blowsTowardDeg,
    speedKmh,
    speedMs,
    drift3hKm,
    drift6hKm,
    severity,
    advisory,
  }
}
