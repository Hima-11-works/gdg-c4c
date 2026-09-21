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

// The one place the app names PDI — every label/tooltip on it should
// come from here so the wording can't drift between the legend, the
// layer toggle, and the cell detail panel.
export const PDI_LABEL = 'Pollution Development Index (PDI)'

export const PDI_TOOLTIP =
  'A heuristic pollution-pressure score, not an exact measurement of emissions or ' +
  'absorption. Blends pollution level, urban/industrial pressure, road/activity ' +
  'pressure, and vegetation (a pollution sink) — kept independent of PM2.5, so the ' +
  'two numbers can and do differ for the same cell.'

// Backend factor keys (see CellDetailOut.pdi_factors / app.domain.pdi)
// are short, code-oriented identifiers — this is the only place that
// maps them to the display wording a user reads. An unrecognized key
// (a factor added on the backend before this map is updated) falls back
// to the raw key rather than disappearing silently.
const PDI_FACTOR_LABELS: Record<string, string> = {
  pm25: 'Pollution level',
  industrial_pressure: 'Urban / industrial pressure',
  road_pressure: 'Road / activity pressure',
  vegetation_sink: 'Vegetation (sink)',
  fire_pressure: 'Reported fire',
}

export function pdiFactorLabel(key: string): string {
  return PDI_FACTOR_LABELS[key] ?? key
}
