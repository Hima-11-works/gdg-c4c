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

export interface SourceHealthOut {
  dataset_id: string
  status: 'present' | 'empty' | 'stale' | 'missing' | 'failed'
  item_count: number
  latency_ms: number | null
  fetched_at: string | null
  error_summary: string | null
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
  /**
   * F3. `is_fallback` means the response was synthesised from stored grid
   * rows because no run was published - not that a pipeline produced it. The
   * run id rolls over on the hour, so without these a client cannot tell a
   * healthy pipeline from a fallback that looks identical.
   */
  is_demo: boolean
  is_fallback: boolean
  fallback_reason: string | null
  age_seconds: number | null
  is_stale: boolean
  source_health: SourceHealthOut[]
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
  status: string
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

export interface FederationModelIdsOut {
  model_ids?: string[]
}

/** The recorded two-partition demonstration; all properties after status are
 * optional because a no-run response intentionally carries only scope/limits. */
export interface FederationStatusOut {
  status: string
  run_id?: string | null
  participant_count?: number | null
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
  'building_fire' | 'industrial_fire' | 'forest_fire' | 'crop_burning' | 'other'

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

/** The report lifecycle, mirrored from the backend's ReportStatus enum.
 *  A client must not hardcode the order; GET /api/v1/reports/statuses is the
 *  source of truth and `affects_air_quality_model` is the field a UI must show
 *  rather than infer. */
export type FireReportStatus =
  'submitted' | 'under_review' | 'corroborated' | 'rejected' | 'expired'

/** A report with its standing: the v2 row shape and GET /api/v1/reports/{id}.
 *
 *  `affects_air_quality_model` is the distinction that matters to a citizen:
 *  a `submitted` report has been received, and is NOT changing the air-quality
 *  model. Conflating "received" with "counted" is the problem F1 exists to fix,
 *  so this type makes the field impossible to omit. */
export interface FireReportWithStatus extends FireReportOut {
  status: FireReportStatus
  status_meaning: string
  is_verified: boolean
  affects_air_quality_model: boolean
  last_status_change_at: string | null
  expires_at: string | null
  seconds_until_expiry: number | null
  corroborating_report_count: number
  cluster_id: string | null
  /** Photos/extra evidence attached so far (F2). Never required: a report with
   *  zero evidence is a normal claim. */
  evidence_count: number
  evidence_expected: boolean
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
