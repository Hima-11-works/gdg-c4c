// Wire types for the backend's documented API contract (see
// docs/architecture.md's "API" section and backend/app/api/schemas.py).
// These mirror the Pydantic response schemas field-for-field. Timestamps
// stay as ISO strings here — parsing to Date happens only where a
// component actually needs to format one, not at the wire boundary.
//
// Deliberately source-agnostic: nothing here (or anywhere the frontend
// reads a response) says whether a value came from OpenAQ, Open-Meteo,
// seeded demo data, or a future provider (satellite retrievals,
// government sensor feeds, ...) — `Envelope.is_demo` is the one signal
// the frontend gets, and it means "illustrative, not measured," not
// "which system produced this." Adding a per-provider field to any type
// below would break that; if a component ever needs to know the source,
// that's a sign the decision belongs in the backend, not here.

export interface Envelope<T> {
  generated_at: string
  is_demo: boolean
  data: T
}

/** A map viewport, sent as min_lat/min_lon/max_lat/max_lon query params —
 * see lib/api.ts's level-of-detail-aware fetch functions and
 * app.api.deps.get_bbox_query on the backend. */
export interface BoundingBox {
  minLat: number
  minLon: number
  maxLat: number
  maxLon: number
}

export interface GridStateOut {
  h3_cell: string
  timestamp: string
  confidence: number
  pm25: number | null
  // Pollution Development Index (PDI) — a heuristic pollution-pressure
  // score in roughly [-100, 100], NOT a scientific measurement of
  // emissions or absorption, and deliberately independent of pm25 above
  // (they can and do diverge for the same cell) — see PDI_TOOLTIP in
  // lib/format.ts and CellDetailOut.pdi_factors for the breakdown. Null
  // only if the backend genuinely has nothing to compute it from (e.g.
  // every configured factor weight is zero) — see
  // app.services.pdi.HeuristicPDIModel.
  pdi: number | null
  wind_speed: number | null
  wind_direction: number | null
}

export interface ForecastOut {
  h3_cell: string
  generated_at: string
  forecast_time: string
  forecast_hours: number
  forecast_minutes: number
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
  temperature: number | null
  humidity: number | null
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
  // The normalized [0, 1] value of each factor behind current.pdi (e.g.
  // "pm25", "industrial_pressure", "road_pressure", "vegetation_sink",
  // "fire_pressure") - not each factor's weighted contribution, just how
  // strongly that signal was present here. Null when no breakdown is
  // available for this reading (a row written before migration 0005, or a
  // PDI model that returns none). The pipeline persists it now.
  pdi_factors: Record<string, number> | null
}

/** What a citizen reported burning. Wire values match the backend's
 *  app.domain.types.FireKind. */
export type FireReportKind =
  | 'building_fire'
  | 'industrial_fire'
  | 'forest_fire'
  | 'crop_burning'
  | 'other'

/** One citizen fire/burning report, as POST/GET /api/v1/reports return it.
 *  `smoke_intensity` is the user's 1-5 smoke slider - a triage choice the
 *  backend scales a modeled plume from, not a measurement. */
export interface FireReportOut {
  id: number
  /** The H3 cell the backend snapped the report to at write time. */
  h3_cell: string
  latitude: number
  longitude: number
  kind: FireReportKind
  smoke_intensity: number
  duration_hours: number
  notes: string | null
  client_report_id: string | null
  reported_at: string
}

/** Request body for POST /api/v1/reports - the same bounds the backend
 *  validates (intensity 1-5, duration 0-24h, notes <= 280 chars). */
export interface FireReportSubmit {
  latitude: number
  longitude: number
  kind: FireReportKind
  smoke_intensity: number
  duration_hours: number
  notes?: string
  /** Idempotency id: a retry of the same submission must not stack reports. */
  client_report_id?: string
}
