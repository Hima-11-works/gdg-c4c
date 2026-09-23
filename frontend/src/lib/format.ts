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

/** Thousands-separated whole number, for counts a reader should be able to
 *  scan (residents, cells, stations). Presentation only. */
export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : Math.round(value).toLocaleString()
}

/** A 0–1 fraction as a percentage. */
export function formatPercent(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`
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

// The map's two selectable measures. Both are PM2.5 concentrations in µg/m³;
// they differ in what each cell's number is averaged over, which is exactly
// what the tooltips have to say, because the two layers can show different
// colours for the same cell and neither is wrong.
export type MapMetric = 'pm25' | 'exposure'

export const METRIC_LABEL: Record<MapMetric, string> = {
  pm25: 'PM2.5',
  exposure: 'Population exposure',
}

/** Short form for tight spots (the legend heading, the toggle). */
export const EXPOSURE_LABEL = 'Population-weighted PM2.5'

export const PM25_METRIC_TOOLTIP =
  'PM2.5 per H3 cell, averaged over the cell’s area — the concentration in the air, ' +
  'regardless of who is there to breathe it.'

export const EXPOSURE_METRIC_TOOLTIP =
  'The same PM2.5 readings, but each cell is averaged with its resident population as ' +
  'the weight: crowded cells dominate, empty ones count for nothing. This is the backend’s ' +
  'population-weighted concentration (ExposureOut.population_weighted_pm25), not a medical ' +
  'dose estimate. A cell is blank when the published run has no population estimate for it.'

/** Thresholds above which the dashboard calls a published run or its source
 *  observations stale. Chosen, not measured: publications are hourly, so a
 *  day-old run is unambiguously old, and the same for the newest observation
 *  behind it. Both are stated in the UI next to the numbers they qualify, so a
 *  reader can judge the age themselves rather than trusting a badge. */
export const STALE_RUN_HOURS = 24
export const STALE_SOURCE_HOURS = 24

/** "3h ago" / "just now" / "in 2h" — coarse, honest relative time. Returns
 *  null for an unparseable or missing timestamp rather than guessing. */
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string | null {
  if (iso === null || iso === undefined || iso === '') return null
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return null
  const minutes = Math.round((now.getTime() - then.getTime()) / 60_000)
  const past = minutes >= 0
  const magnitude = Math.abs(minutes)
  if (magnitude < 1) return 'just now'
  if (magnitude < 60) return past ? `${magnitude} min ago` : `in ${magnitude} min`
  const hours = Math.round(magnitude / 60)
  if (hours < 48) return past ? `${hours}h ago` : `in ${hours}h`
  const days = Math.round(hours / 24)
  return past ? `${days}d ago` : `in ${days}d`
}

/** Hours between a timestamp and now, or null when it can't be read. */
export function ageInHours(iso: string | null | undefined, now: Date = new Date()): number | null {
  if (iso === null || iso === undefined || iso === '') return null
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return null
  return (now.getTime() - then.getTime()) / 3_600_000
}
