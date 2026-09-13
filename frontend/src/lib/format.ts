// Presentational formatting only (rounding, compass labels) — never a
// computed pollution value. Every number displayed by the UI comes
// straight from the backend response.

const COMPASS_POINTS = [
  'N',
  'NNE',
  'NE',
  'ENE',
  'E',
  'ESE',
  'SE',
  'SSE',
  'S',
  'SSW',
  'SW',
  'WSW',
  'W',
  'WNW',
  'NW',
  'NNW',
] as const

/** `degrees` is meteorological convention (direction the wind blows
 * FROM), matching WeatherReadingOut.wind_direction as returned by the API. */
export function compassLabel(degrees: number): string {
  const index = Math.round(degrees / 22.5) % 16
  return COMPASS_POINTS[index]
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? '—' : value.toFixed(digits)
}
