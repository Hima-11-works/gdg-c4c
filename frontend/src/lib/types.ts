// Wire types for the backend's documented API contract (see
// docs/architecture.md's "API" section and backend/app/api/schemas.py).
// These mirror the Pydantic response schemas field-for-field. Timestamps
// stay as ISO strings here — parsing to Date happens only where a
// component actually needs to format one, not at the wire boundary.

export interface Envelope<T> {
  generated_at: string
  is_demo: boolean
  data: T
}

export interface GridStateOut {
  h3_cell: string
  timestamp: string
  confidence: number
  pm25: number | null
  // Heuristic "pollution pressure index" — not a scientific measurement.
  // Usually null today: no pipeline writes it yet (see HeuristicPDIModel).
  pdi: number | null
  wind_speed: number | null
  wind_direction: number | null
}

export type ForecastHorizonHours = 1 | 3 | 6

export interface ForecastOut {
  h3_cell: string
  generated_at: string
  forecast_time: string
  forecast_hours: number
  predicted_pm25: number
  confidence: number
}

export interface WeatherReadingOut {
  h3_cell: string
  latitude: number
  longitude: number
  wind_speed: number
  wind_direction: number
  precipitation: number
  boundary_layer_height: number | null
  measured_at: string
}

export type AlertSeverity = 'watch' | 'warning' | 'critical'

export interface AlertOut {
  h3_cell: string
  severity: AlertSeverity
  message: string
  created_at: string
  // Context the alert was raised with — never fabricated, so any of
  // these can be null (see backend/app/domain/types.py's Alert docstring).
  current_pm25: number | null
  forecast_pm25: number | null
  forecast_hours: number | null
  confidence: number | null
  forecast_time: string | null
}

export interface CellDetailOut {
  h3_cell: string
  current: GridStateOut | null
  forecasts: ForecastOut[]
  weather: WeatherReadingOut | null
}
