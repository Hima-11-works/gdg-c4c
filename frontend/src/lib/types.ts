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
  run_id?: string
  mode?: DataMode
  attribution?: DatasetRefOut[]
  coverage?: CoverageOut | null
}

export type DataMode = 'live' | 'demo' | 'mixed'
export type InputKind = 'observed' | 'modeled' | 'synthetic' | 'derived'

export interface DatasetRefOut {
  dataset_id: string
  source: string
  product: string
  version: string
  kind: InputKind
  region: string
  attribution: string
  license: string
}

export interface QualityFlagsOut {
  coverage_fraction: number
  observed_station_count: number
  max_observation_age_hours: number | null
  missing_fields: string[]
  warnings: string[]
}

export interface PredictionMetadataOut {
  input_kind: InputKind
  prediction_method: string
  model_version: string | null
  feature_schema_version: string
  dataset_versions: DatasetRefOut[]
  observed_at: string | null
  issued_at: string | null
  valid_at: string
  synthetic: boolean
  quality: QualityFlagsOut
}

export interface ExposureOut {
  population_weighted_pm25: number | null
  residents_above_threshold: number | null
  threshold_pm25: number | null
  covered_population: number
  unknown_population: number | null
  population_dataset_version: string | null
  scope: string
}

export interface CoverageOut {
  region: string
  resolution: number
  requested_cells: number
  returned_cells: number
  covered_fraction: number
  unsupported_cells: number
}

export interface StaticFeaturesV2Out {
  h3_cell: string
  population_count: number | null
  population_density_per_km2: number | null
  road_length_km_by_class: Record<string, number>
  major_road_distance_km: number | null
  built_up_fraction: number | null
  vegetation_fraction: number | null
  bare_soil_fraction: number | null
  industrial_fraction: number | null
  dataset_versions: DatasetRefOut[]
}

export interface V2Envelope<T> extends Envelope<T> {
  run_id: string
  mode: DataMode
  attribution: DatasetRefOut[]
  coverage: CoverageOut | null
}

export interface MetaV2Out {
  region: string
  latest_run_id: string
  generated_at: string
  native_resolution: number
  supported_display_resolutions: number[]
  supported_horizons_hours: number[]
  feature_schema_version: string
  model_version: string | null
  data_mode: DataMode
}

export interface GridCurrentV2Out {
  h3_cell: string
  valid_at: string
  latitude: number
  longitude: number
  pm25: number | null
  pm25_unit: string
  pdi: number | null
  pdi_version: string | null
  confidence: number
  wind_speed_ms: number | null
  wind_direction_deg: number | null
  metadata: PredictionMetadataOut
  exposure: ExposureOut | null
}

export interface ForecastV2Out {
  h3_cell: string
  baseline_pm25: number | null
  predicted_pm25: number
  lower_pm25: number | null
  upper_pm25: number | null
  forecast_hours: number
  forecast_time: string
  generated_at: string
  confidence: number
  metadata: PredictionMetadataOut
  exposure: ExposureOut | null
}

export interface WeatherV2Out {
  h3_cell: string
  latitude: number
  longitude: number
  issued_at: string
  valid_at: string
  wind_u_ms: number | null
  wind_v_ms: number | null
  wind_speed_ms: number | null
  wind_direction_deg: number | null
  precipitation_mm: number | null
  boundary_layer_height_m: number | null
  temperature_c: number | null
  relative_humidity_pct: number | null
  input_kind: InputKind
  dataset_versions: DatasetRefOut[]
}

export interface CellDetailV2Out {
  h3_cell: string
  current: GridCurrentV2Out | null
  forecasts: ForecastV2Out[]
  weather: WeatherV2Out | null
  static_features: StaticFeaturesV2Out | null
  exposure: ExposureOut | null
  pdi_factors: Record<string, number> | null
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
  latitude?: number
  longitude?: number
  metadata?: PredictionMetadataOut
  exposure?: ExposureOut | null
}

export interface ForecastOut {
  h3_cell: string
  generated_at: string
  forecast_time: string
  forecast_hours: number
  forecast_minutes: number
  predicted_pm25: number
  confidence: number
  lower_pm25?: number | null
  upper_pm25?: number | null
  metadata?: PredictionMetadataOut
  exposure?: ExposureOut | null
}

export interface WeatherReadingOut {
  h3_cell: string
  latitude: number
  longitude: number
  wind_speed: number | null
  wind_direction: number | null
  precipitation: number | null
  boundary_layer_height: number | null
  temperature: number | null
  humidity: number | null
  measured_at: string
  wind_u_ms?: number | null
  wind_v_ms?: number | null
  issued_at?: string
  valid_at?: string
}

export type AlertSeverity = 'watch' | 'warning' | 'critical'

export interface AlertOut {
  /** The published-alert identity the incident API accepts as a source:
   *  `v2:<run_id>:<h3_cell>:<forecast_hours>`. Deterministic and stable, so an
   *  alert the web is showing can be opened as an incident directly. */
  alert_id: string
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
  environmental?: {
    run_id: string
    mode: DataMode
    metadata: PredictionMetadataOut | null
    exposure: ExposureOut | null
    static_features: StaticFeaturesV2Out | null
  }
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

/** One stored NASA FIRMS detection, as GET /api/v1/fires returns it.
 *
 *  The backend ingests and parses the feed (app.ingestion.firms), so this is
 *  what the browser reads instead of NASA's CSV. `confidence_raw` is the
 *  raw FIRMS token ('l'/'n'/'h', or a 0-100 string for MODIS) and
 *  `confidence_class` its normalized form; `daynight` is null when the feed
 *  didn't say. */
export interface FireHotspotOut {
  detection_id: string
  /** The H3 cell the detection was snapped to at ingest time. */
  h3_cell: string
  latitude: number
  longitude: number
  /** Fire Radiative Power, megawatts. */
  frp_mw: number
  /** Brightness temperature (4 µm band), kelvin. */
  brightness_ti4_k: number | null
  confidence_raw: string
  confidence_class: string
  /** Satellite overpass time, UTC. */
  acquired_at: string
  satellite: string
  daynight: string | null
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

// --- citizen intake evidence (POST/GET /api/v1/reports/{id}/evidence) ---

/** Moderation state of an evidence record. The backend starts every record at
 *  `unverified` and never promotes one by itself. */
export type EvidenceVerificationStatus = 'unverified' | 'pending' | 'verified' | 'rejected'

/** The stored photo. `url` is relative to the API origin, so it is displayed
 *  through the same base the rest of the client uses. */
export interface ReportEvidenceMedia {
  content_type: string
  byte_size: number
  sha256: string
  url: string
  is_placeholder: boolean
}

/** A resident's own sensor reading. `source` is always `citizen` and
 *  `verified` is false for every non-verified state, so a citizen value can
 *  never be read as a trusted station observation. */
export interface ReportEvidenceSensor {
  pollutant: string
  value: number
  unit: string
  measured_at: string
  latitude: number
  longitude: number
  source: string
  verified: boolean
}

/** The evidence record for a report: a photo, a sensor reading, or both. One
 *  report has at most one record, so this is a sub-resource of the report id
 *  rather than a collection. */
export interface ReportEvidenceOut {
  id: number
  report_id: number
  client_report_id: string | null
  verification_status: EvidenceVerificationStatus
  media: ReportEvidenceMedia | null
  sensor: ReportEvidenceSensor | null
  notes: string | null
  submitted_at: string
}

/** The pollutants the backend accepts for a citizen reading. */
export const SENSOR_POLLUTANTS = ['pm25', 'pm10'] as const

export type SensorPollutant = (typeof SENSOR_POLLUTANTS)[number]

// --- persistent incident workflow (/api/v1/incidents) ---
//
// An incident is an operational record created from a published alert, a
// persisted fire alert, or a citizen report, and progressed by an authenticated
// responder. It is deliberately separate from its source: an alert can exist
// without an incident, and the incident keeps its own lifecycle, assignment,
// jurisdiction and append-only history.

/** What an incident was opened from. The source decides the responder role, so
 *  this is not free choice: a published alert is a pollution-control matter, a
 *  fire report a fire-department one. */
export type IncidentSourceType = 'published_alert' | 'alert' | 'report'

/** The states the backend enforces, in the order it allows them. `assigned` is
 *  reachable only through the assign endpoint, never the transition endpoint. */
export const INCIDENT_STATUS_ORDER = [
  'reported',
  'assigned',
  'acknowledged',
  'en_route',
  'on_scene',
  'resolved',
] as const

export type IncidentStatus = (typeof INCIDENT_STATUS_ORDER)[number] | 'cancelled'

/** Who may act on an incident. The acting role comes from the server-side
 *  registry, never from the request. */
export type ResponderRole = 'fire_department' | 'pollution_control'

export interface IncidentOut {
  id: number
  source_type: IncidentSourceType
  source_id: number | null
  source_ref: string | null
  /** True when the source came from a demo/synthetic publication, so a
   *  fallback run's incident cannot be read as a real-world event. */
  source_synthetic: boolean
  status: IncidentStatus
  responder_role: ResponderRole
  severity: 'watch' | 'warning' | 'critical'
  jurisdiction: string | null
  latitude: number | null
  longitude: number | null
  h3_cell: string | null
  linked_prediction_run_id: string | null
  evidence_report_ids: number[]
  assignee: string | null
  created_at: string
  updated_at: string
  resolved_at: string | null
}

export type IncidentEventType =
  | 'created'
  | 'assigned'
  | 'reassigned'
  | 'delivered'
  | 'transition'

/** One append-only history row. `actor`/`actor_jurisdiction` say *which
 *  authority* acted, not merely which role. */
export interface IncidentEventOut {
  id: number
  incident_id: number
  event_type: IncidentEventType
  from_status: IncidentStatus | null
  to_status: IncidentStatus | null
  role: ResponderRole | null
  actor: string | null
  actor_jurisdiction: string | null
  note: string | null
  created_at: string
}

/** A *simulated* hand-off to one responder role's inbox. The database forces
 *  `simulated = true`, and the notification string always begins "none": no
 *  email, SMS, webhook or push is sent by this system. */
export interface IncidentDeliveryOut {
  id: number
  incident_id: number
  audience_role: ResponderRole
  status: 'simulated' | 'acknowledged'
  assignee: string | null
  simulated: boolean
  notification: string
  simulated_at: string
  acknowledged_at: string | null
}

export interface InboxItemOut {
  delivery: IncidentDeliveryOut
  incident: IncidentOut
  is_open: boolean
}

/** Body for POST /api/v1/incidents. Exactly one of `source_id` / `source_ref`
 *  is required, and it must match `source_type`. */
export interface IncidentCreate {
  source_type: IncidentSourceType
  source_ref?: string
  source_id?: number
  severity?: 'watch' | 'warning' | 'critical'
  jurisdiction?: string
  evidence_report_ids?: number[]
}

// --- corridor pollution events (docs/api/corridor-evaluation.md) ---
//
// A corridor event is a published v2 run evaluated over a named corridor's
// cells at a set of horizons. The contract's governing rule: a forecast may only
// be reported as accurate against real, withheld station observations. When those
// observations are missing the answer is an explicit insufficient-data result,
// never a synthetic number presented as accuracy.

export interface CorridorEndpoint {
  label: string
  latitude: number
  longitude: number
}

export interface CorridorCatalogEntry {
  corridor_id: string
  name: string
  kind: string
  region: string
  /** `illustrative` until a sourced route dataset replaces the straight line. */
  geometry_source: string
  geometry_note: string
  geometry_description: string
  h3_resolution: number
  cell_count: number
  endpoints: CorridorEndpoint[]
  notes: string
}

export interface CorridorHorizon {
  horizon_hours: number
  /** The run's generation time — when the forecast was issued. */
  issued_at: string
  /** The hour the forecast predicts. */
  valid_at: string
}

export interface CorridorEvent {
  /** `corridor:<corridor_id>:<run_id>:h<digest>` — stable for a run + horizon set. */
  event_id: string
  corridor_id: string
  corridor_name: string
  /** The published run this event was evaluated over. */
  run_id: string
  run_mode: DataMode
  run_synthetic: boolean
  issued_at: string
  horizons: CorridorHorizon[]
  cell_count: number
  cells: string[]
  /** Highest forecast PM2.5 in the corridor — a model prediction, not evidence. */
  peak_predicted_ugm3: number | null
  peak_horizon_hours: number | null
  /** How many station observations were found, and from where. */
  label_count: number
  label_sources: string[]
}

export interface CorridorSlice {
  horizon_hours: number
  geography: string
  /** Scored (forecast, observed) pairs. */
  pairs: number
  station_count: number
  mae_ugm3: number | null
  rmse_ugm3: number | null
  bias_ugm3: number | null
  high_pollution_threshold_ugm3: number
  high_pollution_observed: number
  /** Null — not 0 — when no observation crossed the threshold. */
  high_pollution_recall: number | null
  high_pollution_precision: number | null
  /** False means the metrics above are null and must not be quoted. */
  sufficient: boolean
  note: string | null
}

export interface CorridorCoverage {
  corridor_cells: number
  cells_with_forecast: number
  cells_with_labels: number
  cells_scored: number
  requested_horizons: number
  horizons_scored: number
  fraction_cells_scored: number
  fraction_horizons_scored: number
}

export interface CorridorEvaluation {
  /** `evaluated` only when every requested horizon × slice had enough real labels. */
  verdict: 'evaluated' | 'insufficient_data'
  /** True only for `evaluated`. */
  usable_as_real_world_evidence: boolean
  label_provenance: string
  reasons: string[]
  min_labels: number
  high_pollution_threshold_ugm3: number
  coverage: CorridorCoverage
  slices: CorridorSlice[]
  evidence: CorridorSlice[]
}

export interface CorridorEventBundle {
  event: CorridorEvent
  evaluation: CorridorEvaluation
}

// --- federation demonstration (GET /api/v1/federation/status) ---
//
// The two-region federated-training *demonstration*: two disjoint partitions of
// one synthetic dataset, each trained locally, exchanging model updates only.
// The types below follow the route's schemas; the blocks the backend types as
// loose dicts (`aggregate`, `evaluation`, `limitations`) are modelled here for
// the fields the dashboard reads, and every one of them is treated as optional
// because a status payload is allowed to be partial.
//
// Nothing in this contract claims privacy, nationwide coverage or real-world
// accuracy, and neither does the UI: `region_scope`, `synthetic_only` and
// `evaluation.usable_as_real_world_evidence` exist precisely so the screen can
// say what the demonstration is not.

export interface FederationParticipantOut {
  participant_id: string
  region_label: string
  example_count: number
  train_count: number
  validation_count: number
  test_count: number
  station_count: number
  horizon_count: number
  update_path: string
  update_sha256: string
  weight_fraction: number
  joined_at?: string
}

export interface FederationModelVersionOut {
  model_id: string
  region: string
  horizon_hours: number
  /** Always `candidate` for this demonstration — never promoted. */
  status: string
  /** True for every model this demonstration can produce. */
  synthetic_only: boolean
}

export interface FederationAggregateOut {
  artifact_path: string
  artifact_sha256: string
  algorithm: string
  synthetic_only: boolean
}

export interface FederationEvaluationHorizonOut {
  horizon_hours: number
  mae_ugm3: number
  baseline_mae_ugm3: number
  rmse_ugm3: number
  bias_ugm3: number | null
  heldout_count: number
  seasons_seen?: string[]
}

export interface FederationEvaluationOut {
  /** `synthetic_evaluation_only`, or `unavailable`. */
  status: string
  usable_as_real_world_evidence: boolean
  reason: string
  heldout_examples?: number | null
  horizons?: FederationEvaluationHorizonOut[] | null
}

export interface FederationLimitationsOut {
  privacy?: string
  geography?: string
  accuracy?: string
}

/** `GET /api/v1/federation/status` returns model versions in one of two
 *  shapes, and the two carry different detail:
 *
 *   - a freshly demonstrated run (and the contract's example) returns a list of
 *     full objects — see [FederationModelVersionOut];
 *   - a run read back from the database returns only `{"model_ids": [...]}`,
 *     because that is all the persisted row carries.
 *
 *  Both are handled; a client that assumed only the richer shape would break on
 *  the other, which is exactly what happened when this was first wired up. */
export interface FederationModelIdsOut {
  model_ids?: string[]
}

export interface FederationStatusOut {
  /** `succeeded` | `failed` for a recorded run, or `no_federation_run`. */
  status: string
  run_id?: string | null
  participant_count?: number | null
  /** Always `two-partition-synthetic-demonstration`. */
  region_scope?: string | null
  feature_schema_version?: string | null
  horizons_hours?: number[] | null
  participants?: FederationParticipantOut[] | null
  aggregate?: FederationAggregateOut | null
  model_versions?: FederationModelVersionOut[] | FederationModelIdsOut | null
  evaluation?: FederationEvaluationOut | null
  raw_rows_exchanged_to_aggregator?: number | null
  limitations?: FederationLimitationsOut | null
  started_at?: string | null
  finished_at?: string | null
}
