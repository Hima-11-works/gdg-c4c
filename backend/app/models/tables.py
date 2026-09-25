"""SQLAlchemy Core table definitions — the database schema.

Plain Core Table objects, not an ORM: repositories build explicit
statements against them. This is the schema's reference definition;
alembic/versions/0001_initial_schema.py must be kept in sync with it by
hand, since there is no live database here to autogenerate a migration
against (see that file's docstring).
"""

from __future__ import annotations

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.domain.types import AlertSeverity

metadata = MetaData()

# H3 cell addresses render as at most 15 hex characters; 16 leaves headroom.
H3_CELL_LENGTH = 16

dataset_version = Table(
    "dataset_version",
    metadata,
    Column("id", String(120), primary_key=True),
    Column("source", String(80), nullable=False),
    Column("product", String(120), nullable=False),
    Column("version", String(80), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("region", String(80), nullable=False),
    Column("attribution", String(240), nullable=False),
    Column("license", String(160), nullable=False),
    Column("coverage_start", DateTime(timezone=True), nullable=True),
    Column("coverage_end", DateTime(timezone=True), nullable=True),
    Column("available_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "coverage_end IS NULL OR coverage_start IS NULL OR coverage_end >= coverage_start",
        name="ck_dataset_version_coverage_order",
    ),
    Index("ix_dataset_version_source", "source"),
)

ingestion_run = Table(
    "ingestion_run",
    metadata,
    Column("id", String(120), primary_key=True),
    Column("dataset_id", String(120), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("fetched_at", DateTime(timezone=True), nullable=True),
    Column("status", String(20), nullable=False),
    Column("errors", JSONB, nullable=False),
    Column("metrics", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("simulation_id", String(120), nullable=True),
    CheckConstraint(
        "finished_at IS NULL OR finished_at >= started_at",
        name="ck_ingestion_run_finish_order",
    ),
    Index("ix_ingestion_run_dataset_started", "dataset_id", "started_at"),
    Index("ix_ingestion_run_status", "status"),
)

cell_feature_snapshot = Table(
    "cell_feature_snapshot",
    metadata,
    Column("id", String(180), primary_key=True),
    Column("run_id", String(120), nullable=False),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("valid_at", DateTime(timezone=True), nullable=False),
    Column("horizon_hours", Float, nullable=False),
    Column("feature_schema_version", String(60), nullable=False),
    Column("vector", JSONB, nullable=False),
    Column("quality", JSONB, nullable=False),
    Column("dataset_refs", JSONB, nullable=False),
    CheckConstraint("horizon_hours >= 0", name="ck_feature_snapshot_horizon_nonnegative"),
    CheckConstraint("valid_at >= issued_at", name="ck_feature_snapshot_valid_after_issue"),
    UniqueConstraint(
        "run_id",
        "h3_cell",
        "horizon_hours",
        name="uq_feature_snapshot_run_cell_horizon",
    ),
    Index("ix_feature_snapshot_run_valid", "run_id", "valid_at"),
    Index("ix_feature_snapshot_cell_valid", "h3_cell", "valid_at"),
)

model_version = Table(
    "model_version",
    metadata,
    Column("id", String(180), primary_key=True),
    Column("artifact_uri", String(500), nullable=False),
    Column("artifact_sha256", String(64), nullable=False),
    Column("feature_schema_version", String(60), nullable=False),
    Column("feature_names", JSONB, nullable=False),
    Column("trained_at", DateTime(timezone=True), nullable=False),
    Column("training_start", DateTime(timezone=True), nullable=False),
    Column("training_end", DateTime(timezone=True), nullable=False),
    Column("region", String(80), nullable=False),
    Column("horizon_hours", Float, nullable=False),
    Column("metrics", JSONB, nullable=False),
    Column("synthetic_only", Boolean, nullable=False),
    Column("status", String(20), nullable=False),
    CheckConstraint("horizon_hours > 0", name="ck_model_version_horizon_positive"),
    CheckConstraint("training_end >= training_start", name="ck_model_training_range_order"),
    CheckConstraint(
        "status IN ('candidate', 'validated', 'promoted', 'rejected', 'retired')",
        name="ck_model_version_status",
    ),
    Index("ix_model_version_region_horizon_status", "region", "horizon_hours", "status"),
    Index("ix_model_version_trained_at", "trained_at"),
)

prediction_run = Table(
    "prediction_run",
    metadata,
    Column("id", String(120), primary_key=True),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("region", String(80), nullable=False),
    Column("mode", String(20), nullable=False),
    Column("feature_run_id", String(120), nullable=False),
    Column("feature_schema_version", String(60), nullable=False),
    Column("model_versions", JSONB, nullable=False),
    Column("scenario_id", String(120), nullable=True),
    Column("dataset_refs", JSONB, nullable=False),
    CheckConstraint("published_at >= generated_at", name="ck_prediction_run_publish_order"),
    CheckConstraint("mode IN ('live', 'demo', 'mixed')", name="ck_prediction_run_mode"),
    Index("ix_prediction_run_region_published", "region", "published_at"),
)

prediction_result = Table(
    "prediction_result",
    metadata,
    Column("run_id", String(120), ForeignKey("prediction_run.id", ondelete="CASCADE"), primary_key=True),
    Column("h3_cell", String(H3_CELL_LENGTH), primary_key=True),
    Column("horizon_hours", Float, primary_key=True),
    Column("valid_at", DateTime(timezone=True), nullable=False),
    Column("baseline_pm25", Float, nullable=True),
    Column("predicted_pm25", Float, nullable=True),
    Column("lower_pm25", Float, nullable=True),
    Column("upper_pm25", Float, nullable=True),
    Column("pdi", Float, nullable=True),
    Column("prediction_method", String(80), nullable=False),
    Column("model_version", String(180), nullable=True),
    Column("feature_schema_version", String(60), nullable=False),
    Column("input_kind", String(20), nullable=False),
    Column("synthetic", Boolean, nullable=False),
    Column("quality", JSONB, nullable=False),
    Column("dataset_refs", JSONB, nullable=False),
    Column("feature_vector", JSONB, nullable=False),
    CheckConstraint("horizon_hours >= 0", name="ck_prediction_result_horizon_nonnegative"),
    CheckConstraint("baseline_pm25 IS NULL OR baseline_pm25 >= 0", name="ck_prediction_result_baseline_pm25"),
    CheckConstraint("predicted_pm25 IS NULL OR predicted_pm25 >= 0", name="ck_prediction_result_pm25"),
    CheckConstraint("lower_pm25 IS NULL OR lower_pm25 >= 0", name="ck_prediction_result_lower_pm25"),
    CheckConstraint("upper_pm25 IS NULL OR upper_pm25 >= 0", name="ck_prediction_result_upper_pm25"),
    CheckConstraint("lower_pm25 IS NULL OR upper_pm25 IS NULL OR lower_pm25 <= upper_pm25", name="ck_prediction_result_interval_order"),
    CheckConstraint("input_kind IN ('observed', 'modeled', 'synthetic', 'derived')", name="ck_prediction_result_input_kind"),
    Index("ix_prediction_result_run_valid", "run_id", "valid_at"),
    Index("ix_prediction_result_cell_valid", "h3_cell", "valid_at"),
)

sensor_reading = Table(
    "sensor_reading",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("source", String(50), nullable=False),
    Column("external_sensor_id", String(100), nullable=False),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    # Derived from latitude/longitude at write time; used for spatial
    # queries and indexing only, never read back into the domain object.
    Column(
        "geom", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    ),
    Column("pollutant", String(20), nullable=False),
    Column("value", Float, nullable=False),
    Column("unit", String(20), nullable=False),
    Column("measured_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_sensor_reading_latitude"),
    CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_sensor_reading_longitude"),
    # Lets ingestion re-run safely: inserting the same reading twice is a
    # conflict, not a duplicate row (upsert semantics land with ingestion).
    UniqueConstraint(
        "source",
        "external_sensor_id",
        "pollutant",
        "measured_at",
        name="uq_sensor_reading_identity",
    ),
    Index("ix_sensor_reading_measured_at", "measured_at"),
    Index("ix_sensor_reading_pollutant_measured_at", "pollutant", "measured_at"),
    Index("ix_sensor_reading_geom", "geom", postgresql_using="gist"),
)

weather_reading = Table(
    "weather_reading",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column(
        "geom", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    ),
    Column("wind_speed", Float, nullable=False),
    Column("wind_direction", Float, nullable=False),
    Column("precipitation", Float, nullable=False),
    Column("boundary_layer_height", Float, nullable=True),
    # Nullable, "where available" — same idiom as boundary_layer_height:
    # not every provider/source has these (see app.ingestion.open_meteo
    # vs. e.g. a station that only reports wind).
    Column("temperature", Float, nullable=True),
    Column("humidity", Float, nullable=True),
    Column("measured_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("wind_speed >= 0", name="ck_weather_reading_wind_speed"),
    CheckConstraint(
        "wind_direction >= 0 AND wind_direction < 360", name="ck_weather_reading_wind_direction"
    ),
    CheckConstraint("precipitation >= 0", name="ck_weather_reading_precipitation"),
    CheckConstraint(
        "temperature IS NULL OR temperature BETWEEN -90 AND 60",
        name="ck_weather_reading_temperature",
    ),
    CheckConstraint(
        "humidity IS NULL OR humidity BETWEEN 0 AND 100", name="ck_weather_reading_humidity"
    ),
    UniqueConstraint("h3_cell", "measured_at", name="uq_weather_reading_cell_time"),
    Index("ix_weather_reading_measured_at", "measured_at"),
    Index("ix_weather_reading_h3_cell", "h3_cell"),
)

grid_state = Table(
    "grid_state",
    metadata,
    Column("h3_cell", String(H3_CELL_LENGTH), primary_key=True),
    Column("timestamp", DateTime(timezone=True), primary_key=True),
    # Nullable: a cell without enough nearby evidence gets no fabricated
    # value (see app.services.estimation). confidence stays NOT NULL -
    # 0.0 means "no evidence", not "unknown".
    Column("pm25", Float, nullable=True),
    Column("pdi", Float, nullable=True),
    # The normalized [0, 1] value of each factor behind `pdi` (see
    # app.domain.pdi.PDIResult.factors) - e.g. {"pm25": 0.81,
    # "fire_pressure": 0.36} - so the API can explain a cell's score
    # instead of only reporting it. Nullable: older rows predate the
    # column, and a PDI model may return no breakdown.
    Column("pdi_factors", JSONB, nullable=True),
    Column("confidence", Float, nullable=False),
    Column("wind_speed", Float, nullable=True),
    Column("wind_direction", Float, nullable=True),
    CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_grid_state_confidence"),
    CheckConstraint("pm25 IS NULL OR pm25 >= 0", name="ck_grid_state_pm25"),
    CheckConstraint(
        "wind_direction IS NULL OR (wind_direction >= 0 AND wind_direction < 360)",
        name="ck_grid_state_wind_direction",
    ),
    Index("ix_grid_state_timestamp", "timestamp"),
)

forecast = Table(
    "forecast",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    Column("forecast_time", DateTime(timezone=True), nullable=False),
    Column("forecast_hours", Float, nullable=False),
    Column("predicted_pm25", Float, nullable=False),
    Column("confidence", Float, nullable=False),
    CheckConstraint("forecast_hours > 0", name="ck_forecast_hours_positive"),
    CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_forecast_confidence"),
    CheckConstraint("forecast_time > generated_at", name="ck_forecast_time_after_generated"),
    CheckConstraint("predicted_pm25 >= 0", name="ck_forecast_predicted_pm25"),
    # One row per (cell, pipeline run, horizon) — reprocessing a run is a
    # conflict, not a duplicate forecast.
    UniqueConstraint("h3_cell", "generated_at", "forecast_hours", name="uq_forecast_run_horizon"),
    Index("ix_forecast_h3_cell_forecast_time", "h3_cell", "forecast_time"),
    Index("ix_forecast_generated_at", "generated_at"),
)

alert = Table(
    "alert",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    # Bound to the AlertSeverity enum's values so the DB's CHECK constraint
    # can never drift from the Python-side severities.
    Column(
        "severity",
        SAEnum(
            AlertSeverity,
            # Matches the ck_<table>_<column> convention every other CHECK
            # constraint in this schema uses (and the migration's own
            # name for this one) — previously "alert_severity", which
            # would have confused a future `alembic revision
            # --autogenerate` into thinking this constraint needed to be
            # dropped and recreated under the "correct" name.
            name="ck_alert_severity",
            native_enum=False,
            create_constraint=True,  # SQLAlchemy 2.0 defaults this to False
            length=20,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    ),
    Column("message", String(500), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # Context the alert was raised with (see app.domain.types.Alert) —
    # nullable because it's never fabricated: current_pm25 is null if the
    # cell had no current estimate, the forecast_* fields are null only
    # if the cell had no forecast at all.
    Column("current_pm25", Float, nullable=True),
    Column("forecast_pm25", Float, nullable=True),
    Column("forecast_hours", Float, nullable=True),
    Column("confidence", Float, nullable=True),
    Column("forecast_time", DateTime(timezone=True), nullable=True),
    CheckConstraint("current_pm25 IS NULL OR current_pm25 >= 0", name="ck_alert_current_pm25"),
    CheckConstraint("forecast_pm25 IS NULL OR forecast_pm25 >= 0", name="ck_alert_forecast_pm25"),
    CheckConstraint(
        "forecast_hours IS NULL OR forecast_hours > 0", name="ck_alert_forecast_hours_positive"
    ),
    CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_alert_confidence"),
    Index("ix_alert_h3_cell_created_at", "h3_cell", "created_at"),
    Index("ix_alert_severity_created_at", "severity", "created_at"),
)

fire_report = Table(
    "fire_report",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    # Derived from latitude/longitude at write time; used for spatial
    # queries only, never read back into the domain object.
    Column(
        "geom", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    ),
    Column("kind", String(30), nullable=False),
    Column("smoke_intensity", SmallInteger, nullable=False),
    Column("duration_hours", Float, nullable=False),
    Column("notes", String(280), nullable=True),
    Column("client_report_id", String(64), nullable=True),
    Column("reported_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_fire_report_latitude"),
    CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_fire_report_longitude"),
    CheckConstraint("smoke_intensity BETWEEN 1 AND 5", name="ck_fire_report_smoke_intensity"),
    CheckConstraint("duration_hours >= 0", name="ck_fire_report_duration_hours"),
    # Idempotent resubmission: a client retrying with the same generated id
    # must find the original report, not stack a second one. Nullable on
    # purpose (older clients / programmatic submissions may omit it), and
    # Postgres treats NULLs as distinct in a unique index.
    UniqueConstraint("client_report_id", name="uq_fire_report_client_report_id"),
    Index("ix_fire_report_reported_at", "reported_at"),
    Index("ix_fire_report_h3_cell", "h3_cell"),
    # --- F1 lifecycle (app.domain.report_lifecycle, migration 0016) ---
    # A report's standing. Server default 'submitted' is the safe direction: an
    # existing row that predates review is an unverified claim, and must not be
    # treated as a modeled point source.
    Column("status", String(20), nullable=False, server_default="submitted"),
    # Fixed at submission from FIRE_REPORT_MAX_AGE_HOURS, so the read side and
    # the plume model agree on what "active" means and a report cannot be kept
    # alive by re-reading it.
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("status_changed_at", DateTime(timezone=True), nullable=True),
    Column("reviewed_at", DateTime(timezone=True), nullable=True),
    Column("reviewed_by", String(80), nullable=True),
    Column("moderation_note", String(500), nullable=True),
    # Whether the India geofence was checked and passed at submission. Recorded
    # rather than inferred, so a report accepted with the fence disabled is still
    # honest about it.
    Column("india_geofence_verified", Boolean, nullable=False, server_default="false"),
    Column("cluster_id", String(40), nullable=True),
    Column("corroborating_report_count", Integer, nullable=False, server_default="0"),
    # F2 progress, reported to the citizen. Deliberately not an input to
    # qualification: a report with no photo is a first-class claim.
    Column("evidence_count", Integer, nullable=False, server_default="0"),
    # The rate limiter's unit of accounting: a /24 network prefix rather than a
    # full client address, stored truncated for that reason. Null when a report
    # arrives without an HTTP client (CLI seed, test, internal caller).
    Column("submitter_prefix", String(20), nullable=True),
    CheckConstraint(
        "status IN ('submitted','under_review','corroborated','rejected','expired')",
        name="ck_fire_report_status",
    ),
    CheckConstraint("corroborating_report_count >= 0", name="ck_fire_report_corroborating_count"),
    CheckConstraint("evidence_count >= 0", name="ck_fire_report_evidence_count"),
    CheckConstraint(
        "expires_at IS NULL OR expires_at >= reported_at",
        name="ck_fire_report_expiry_after_report",
    ),
    Index("ix_fire_report_status_reported", "status", "reported_at"),
    Index("ix_fire_report_cluster", "cluster_id"),
)

# Append-only. No update, no delete: this table is the answer to "who decided
# this report was real, and when", and a trail that can be rewritten is not one.
fire_report_event = Table(
    "fire_report_event",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "report_id",
        BigInteger,
        ForeignKey("fire_report.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String(20), nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("from_status", String(20), nullable=True),
    Column("to_status", String(20), nullable=True),
    Column("actor", String(80), nullable=False),
    Column("note", String(500), nullable=False),
    Column("detail", JSONB, nullable=True),
    CheckConstraint(
        "kind IN ('submitted','status_changed','clustered','expired','evidence_linked')",
        name="ck_fire_report_event_kind",
    ),
    Index("ix_fire_report_event_report", "report_id", "at"),
)

fire_hotspot = Table(
    "fire_hotspot",
    metadata,
    Column("detection_id", String(64), primary_key=True),
    Column("dataset_id", String(120), ForeignKey("dataset_version.id"), nullable=False),
    Column("ingestion_run_id", String(120), ForeignKey("ingestion_run.id"), nullable=False),
    Column("source", String(40), nullable=False),
    Column("product", String(80), nullable=False),
    Column("product_version", String(80), nullable=False),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    Column(
        "geom", Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    ),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("acquired_at", DateTime(timezone=True), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("satellite", String(30), nullable=False),
    Column("instrument", String(40), nullable=False),
    Column("confidence_raw", String(20), nullable=False),
    Column("confidence_class", String(20), nullable=False),
    Column("frp_mw", Float, nullable=False),
    Column("scan_km", Float, nullable=True),
    Column("track_km", Float, nullable=True),
    Column("brightness_ti4_k", Float, nullable=True),
    Column("brightness_ti5_k", Float, nullable=True),
    Column("daynight", String(1), nullable=True),
    Column("quality", JSONB, nullable=False),
    CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_fire_hotspot_latitude"),
    CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_fire_hotspot_longitude"),
    CheckConstraint("frp_mw >= 0", name="ck_fire_hotspot_frp_nonnegative"),
    CheckConstraint("scan_km IS NULL OR scan_km >= 0", name="ck_fire_hotspot_scan_nonnegative"),
    CheckConstraint("track_km IS NULL OR track_km >= 0", name="ck_fire_hotspot_track_nonnegative"),
    CheckConstraint(
        "brightness_ti4_k IS NULL OR brightness_ti4_k >= 0",
        name="ck_fire_hotspot_ti4_nonnegative",
    ),
    CheckConstraint(
        "brightness_ti5_k IS NULL OR brightness_ti5_k >= 0",
        name="ck_fire_hotspot_ti5_nonnegative",
    ),
    CheckConstraint("available_at >= acquired_at", name="ck_fire_hotspot_availability"),
    CheckConstraint(
        "confidence_class IN ('low', 'nominal', 'high', 'unknown')",
        name="ck_fire_hotspot_confidence_class",
    ),
    Index("ix_fire_hotspot_acquired_source", "source", "acquired_at"),
    Index("ix_fire_hotspot_h3_acquired", "h3_cell", "acquired_at"),
    Index("ix_fire_hotspot_run", "ingestion_run_id"),
)

traffic_observation = Table(
    "traffic_observation",
    metadata,
    Column("observation_id", String(64), primary_key=True),
    Column("dataset_id", String(120), ForeignKey("dataset_version.id"), nullable=False),
    Column("ingestion_run_id", String(120), ForeignKey("ingestion_run.id"), nullable=False),
    Column("source", String(80), nullable=False),
    Column("road_id", String(160), nullable=False),
    Column("h3_cell", String(H3_CELL_LENGTH), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("observed_speed_kph", Float, nullable=False),
    Column("free_flow_speed_kph", Float, nullable=False),
    Column("observed_free_flow_ratio", Float, nullable=False),
    Column("confidence", Float, nullable=True),
    Column("sampled_road_coverage_fraction", Float, nullable=False),
    Column("quality", JSONB, nullable=False),
    CheckConstraint("observed_speed_kph >= 0", name="ck_traffic_observed_speed_nonnegative"),
    CheckConstraint("free_flow_speed_kph > 0", name="ck_traffic_free_flow_speed_positive"),
    CheckConstraint("observed_free_flow_ratio >= 0", name="ck_traffic_speed_ratio_nonnegative"),
    CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="ck_traffic_confidence"),
    CheckConstraint(
        "sampled_road_coverage_fraction BETWEEN 0 AND 1",
        name="ck_traffic_coverage_fraction",
    ),
    CheckConstraint("available_at >= observed_at", name="ck_traffic_availability"),
    Index("ix_traffic_h3_observed", "h3_cell", "observed_at"),
    Index("ix_traffic_source_observed", "source", "observed_at"),
    Index("ix_traffic_run", "ingestion_run_id"),
)
