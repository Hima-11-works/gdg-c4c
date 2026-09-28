"""Compiles every repository statement against the PostgreSQL dialect.

No database connection is made. This exercises the exact statement-building
functions used by app.db.repositories (insert/select/upsert, including the
PostgreSQL-specific ON CONFLICT and DISTINCT ON constructs) without needing
a live PostgreSQL/PostGIS instance.
"""

from datetime import UTC, datetime

import h3
from sqlalchemy.dialects import postgresql

from app.db.repositories import alert as alert_repo
from app.db.repositories import dataset_version as dataset_repo
from app.db.repositories import feature_snapshot as feature_repo
from app.db.repositories import fire_report as fire_report_repo
from app.db.repositories import forecast as forecast_repo
from app.db.repositories import grid_state as grid_state_repo
from app.db.repositories import ingestion_run as run_repo
from app.db.repositories import model_version as model_version_repo
from app.db.repositories import prediction_publication as prediction_publication_repo
from app.db.repositories import sensor_reading as sensor_reading_repo
from app.db.repositories import weather_reading as weather_reading_repo
from app.domain.features import CellFeatureVector, FeatureSnapshot, InputKind
from app.domain.report_lifecycle import AuditEventKind, ReportAuditEvent, ReportStatus
from app.domain.scenario import DatasetVersion, IngestionRun, IngestionRunStatus
from app.domain.training import ModelStatus, ModelVersion
from app.domain.types import (
    Alert,
    AlertSeverity,
    FireKind,
    FireReport,
    Forecast,
    GridState,
    SensorReading,
    WeatherReading,
)

DIALECT = postgresql.dialect()
NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 1, 3, tzinfo=UTC)
LAT, LON = 37.7749, -122.4194
CELL = h3.latlng_to_cell(LAT, LON, 8)


def _sql(stmt) -> str:
    """Compiled SQL text with literal values inlined, for readable assertions.

    Statements holding a geoalchemy2 WKTElement (the insert()s below) can't
    use literal_binds — there is no PostgreSQL literal renderer for it — so
    those are compiled with bound parameters instead via _sql_and_params().
    """
    return str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True}))


def _sql_and_params(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=DIALECT)
    return str(compiled), dict(compiled.params)


def test_sensor_reading_statements() -> None:
    reading = SensorReading(
        source="openaq",
        external_sensor_id="123",
        latitude=37.7749,
        longitude=-122.4194,
        pollutant="pm25",
        value=12.3,
        unit="ug/m3",
        measured_at=NOW,
    )
    insert_sql, params = _sql_and_params(sensor_reading_repo._insert_stmt(reading))
    assert "INSERT INTO sensor_reading" in insert_sql
    assert "RETURNING" in insert_sql
    assert params["geom"].data == "POINT(-122.4194 37.7749)"

    select_sql = _sql(sensor_reading_repo._list_since_stmt(NOW, "pm25"))
    assert "FROM sensor_reading" in select_sql
    assert "sensor_reading.pollutant" in select_sql

    latest_sql = _sql(sensor_reading_repo._list_latest_stmt())
    assert "DISTINCT ON" in latest_sql
    assert "sensor_reading.source, sensor_reading.external_sensor_id" in latest_sql


def test_weather_reading_statements() -> None:
    reading = WeatherReading(
        h3_cell=CELL,
        latitude=37.7749,
        longitude=-122.4194,
        wind_speed=3.2,
        wind_direction=270.0,
        precipitation=0.0,
        measured_at=NOW,
    )
    insert_sql, params = _sql_and_params(weather_reading_repo._insert_stmt(reading))
    assert "INSERT INTO weather_reading" in insert_sql
    assert params["geom"].data == "POINT(-122.4194 37.7749)"
    assert "FROM weather_reading" in _sql(weather_reading_repo._list_since_stmt(NOW))

    latest_sql = _sql(weather_reading_repo._latest_for_cell_stmt(CELL))
    assert "ORDER BY weather_reading.measured_at DESC" in latest_sql
    assert "LIMIT" in latest_sql

    list_latest_sql = _sql(weather_reading_repo._list_latest_stmt())
    assert "DISTINCT ON" in list_latest_sql
    assert "weather_reading.h3_cell" in list_latest_sql

    other_cell = h3.latlng_to_cell(37.8044, -122.2712, 8)
    in_cells_sql = _sql(weather_reading_repo._list_latest_in_cells_stmt([CELL, other_cell]))
    assert "DISTINCT ON" in in_cells_sql
    assert "weather_reading.h3_cell IN" in in_cells_sql


def test_grid_state_statements() -> None:
    state = GridState(
        h3_cell=CELL,
        timestamp=NOW,
        pm25=15.0,
        pdi=42.0,
        confidence=0.8,
        wind_speed=3.2,
        wind_direction=270.0,
    )
    upsert_sql = _sql(grid_state_repo._upsert_stmt(state))
    assert "INSERT INTO grid_state" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert "DO UPDATE SET" in upsert_sql
    # The conflict target columns must never be in the SET clause.
    assert "SET h3_cell" not in upsert_sql
    assert "SET timestamp" not in upsert_sql

    assert "DISTINCT ON" in _sql(grid_state_repo._latest_stmt())
    assert "FROM grid_state" in _sql(grid_state_repo._get_stmt(CELL, NOW))

    latest_for_cell_sql = _sql(grid_state_repo._latest_for_cell_stmt(CELL))
    assert "ORDER BY grid_state.timestamp DESC" in latest_for_cell_sql
    assert "LIMIT" in latest_for_cell_sql

    other_cell = h3.latlng_to_cell(37.8044, -122.2712, 8)
    upsert_many_sql = _sql(
        grid_state_repo._upsert_many_stmt(
            [state, GridState(h3_cell=other_cell, timestamp=NOW, confidence=0.5)]
        )
    )
    assert "INSERT INTO grid_state" in upsert_many_sql
    assert "ON CONFLICT" in upsert_many_sql
    assert "DO UPDATE SET" in upsert_many_sql
    assert "SET h3_cell" not in upsert_many_sql
    assert "SET timestamp" not in upsert_many_sql
    # Both rows' values are present in one statement, not two.
    assert upsert_many_sql.count("INSERT INTO grid_state") == 1
    assert CELL in upsert_many_sql and other_cell in upsert_many_sql

    in_cells_sql = _sql(grid_state_repo._latest_in_cells_stmt([CELL, other_cell]))
    assert "DISTINCT ON" in in_cells_sql
    assert "grid_state.h3_cell IN" in in_cells_sql


def test_forecast_statements() -> None:
    forecast = Forecast(
        h3_cell=CELL,
        generated_at=NOW,
        forecast_time=LATER,
        forecast_hours=3,
        predicted_pm25=18.0,
        confidence=0.6,
    )
    assert "INSERT INTO forecast" in _sql(forecast_repo._insert_stmt(forecast))
    assert "FROM forecast" in _sql(forecast_repo._list_for_cell_stmt(CELL, None))
    assert "FROM forecast" in _sql(forecast_repo._list_for_cell_stmt(CELL, NOW))

    latest_sql = _sql(forecast_repo._latest_for_cell_stmt(CELL)).lower()
    assert "max(forecast.generated_at)" in latest_sql

    horizon_sql = _sql(forecast_repo._latest_for_horizon_stmt(3))
    assert "JOIN" in horizon_sql
    assert "GROUP BY forecast.h3_cell" in horizon_sql
    assert horizon_sql.count("forecast.forecast_hours = 3") == 2  # subquery + join condition

    second_forecast = Forecast(
        h3_cell=CELL,
        generated_at=NOW,
        forecast_time=LATER,
        forecast_hours=6,
        predicted_pm25=9.0,
        confidence=0.4,
    )
    insert_many_sql = _sql(forecast_repo._insert_many_stmt([forecast, second_forecast]))
    assert insert_many_sql.count("INSERT INTO forecast") == 1  # one statement, not two
    assert "RETURNING" in insert_many_sql

    other_cell = h3.latlng_to_cell(37.8044, -122.2712, 8)
    horizon_in_cells_sql = _sql(
        forecast_repo._latest_for_horizon_in_cells_stmt(3, [CELL, other_cell])
    )
    assert "JOIN" in horizon_in_cells_sql
    assert "forecast.h3_cell IN" in horizon_in_cells_sql


def test_alert_statements() -> None:
    alert = Alert(
        h3_cell=CELL,
        severity=AlertSeverity.WARNING,
        message="PM2.5 rising",
        created_at=NOW,
    )
    insert_sql = _sql(alert_repo._insert_stmt(alert))
    assert "INSERT INTO alert" in insert_sql
    assert "'warning'" in insert_sql  # the enum's .value, not .name

    assert "FROM alert" in _sql(alert_repo._list_active_stmt(NOW))

    alert_page_sql = _sql(
        prediction_publication_repo._alert_candidates_stmt(
            "published-run", 91.0, limit=100, offset=200
        )
    )
    assert "ORDER BY forecast_prediction.predicted_pm25 DESC" in alert_page_sql
    assert "LIMIT 100 OFFSET 200" in alert_page_sql


def test_fire_report_statements() -> None:
    report = FireReport(
        h3_cell=CELL,
        latitude=LAT,
        longitude=LON,
        kind=FireKind.CROP_BURNING,
        smoke_intensity=4,
        duration_hours=1.5,
        reported_at=NOW,
        client_report_id="client-123",
    )
    insert_sql, params = _sql_and_params(fire_report_repo._insert_stmt(report))
    assert "INSERT INTO fire_report" in insert_sql
    # The kind goes in as the enum's .value, not .name (bound, not literal:
    # this insert also carries a WKTElement, which can't literal-bind).
    assert "crop_burning" in params.values()
    assert params["geom"].data == f"POINT({LON} {LAT})"

    assert "FROM fire_report" in _sql(fire_report_repo._list_active_stmt(NOW))
    by_client = _sql(fire_report_repo._by_client_report_id_stmt("client-123"))
    assert "client_report_id" in by_client


def test_fire_report_lifecycle_statements() -> None:
    """The F1 lifecycle SQL.

    The one that matters most is `list_active_qualified`: it is the query the
    plume model is fed from, so its status filter is the difference between
    "only corroborated reports reach the model" and "every anonymous claim does".
    Asserting the filter is compiled into the statement - not just present in
    Python - is what keeps that from being quietly dropped.
    """
    report = FireReport(
        h3_cell=CELL,
        latitude=LAT,
        longitude=LON,
        kind=FireKind.CROP_BURNING,
        smoke_intensity=4,
        duration_hours=1.5,
        reported_at=NOW,
        client_report_id="client-123",
        id=7,
        submitter_prefix="198.51.100.0/24",
    )

    insert_sql, params = _sql_and_params(fire_report_repo._insert_stmt(report))
    # Every new column is bound on insert: a lifecycle field with no default in
    # the INSERT would rely on the server default and drift from the model.
    for column in (
        "status",
        "expires_at",
        "status_changed_at",
        "india_geofence_verified",
        "cluster_id",
        "corroborating_report_count",
        "evidence_count",
        "submitter_prefix",
    ):
        assert column in insert_sql, f"fire_report insert omits {column}"
    assert params["submitter_prefix"] == "198.51.100.0/24"

    qualified = _sql(fire_report_repo._list_active_qualified_stmt(NOW))
    assert "FROM fire_report" in qualified
    assert "status IN ('corroborated')" in qualified

    def _where(sql: str) -> str:
        """The WHERE clause alone, so a SELECT-list column cannot fake a filter."""
        return sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]

    # The unfiltered read must NOT gain a status filter: the citizen list is meant
    # to include unverified claims, and only the model's read is restricted.
    assert "status" not in _where(_sql(fire_report_repo._list_active_stmt(NOW)))
    assert "status" in _where(qualified)

    # The rate limiter's two counts. The per-source one must include the prefix
    # filter, or every source would share one budget.
    per_source = _sql(fire_report_repo._count_since_stmt(NOW, "198.51.100.0/24"))
    assert "submitter_prefix" in per_source
    assert "count(*)" in per_source.lower()
    assert "submitter_prefix" not in _sql(fire_report_repo._count_since_stmt(NOW, None))

    # Clustering looks only at still-open claims, so a rejected report does not
    # hand its count to a genuinely new event.
    cluster = _sql(
        fire_report_repo._find_recent_in_cell_stmt(
            h3_cell=CELL, kind="crop_burning", since=NOW
        )
    )
    assert "h3_cell" in cluster and "kind" in cluster
    assert "submitted" in cluster and "under_review" in cluster
    assert "rejected" not in cluster and "expired" not in cluster

    # The expiry sweep's work list: open claims whose window has already closed.
    open_claims = _sql(fire_report_repo._list_open_claims_stmt(NOW))
    assert "expires_at" in open_claims

    # A review writes only lifecycle columns - never the submission facts, which
    # are a record of what someone saw.
    update_sql, update_params = _sql_and_params(fire_report_repo._update_lifecycle_stmt(report))
    assert "UPDATE fire_report" in update_sql
    for column in (
        "latitude",
        "longitude",
        "kind",
        "smoke_intensity",
        "duration_hours",
        "notes",
        "reported_at",
    ):
        assert f"SET {column}" not in update_sql, f"a review may not rewrite {column}"
    assert "status" in update_sql and "corroborating_report_count" in update_params

    by_id = _sql(fire_report_repo._by_id_stmt(7))
    assert "fire_report.id" in by_id

    event_sql, event_params = _sql_and_params(
        fire_report_repo._insert_event_stmt(
            ReportAuditEvent(
                report_id=7,
                kind=AuditEventKind.STATUS_CHANGED,
                at=NOW,
                from_status=ReportStatus.SUBMITTED,
                to_status=ReportStatus.CORROBORATED,
                actor="reviewer-7",
                note="matched a FIRMS detection",
            )
        )
    )
    assert "INSERT INTO fire_report_event" in event_sql
    # The enums go in as .value, never .name.
    assert "submitted" in event_params.values()
    assert "corroborated" in event_params.values()
    assert "reviewer-7" in event_params.values()

    events = _sql(fire_report_repo._list_events_stmt(7))
    assert "FROM fire_report_event" in events
    # Oldest first, so a history reads in the order it happened.
    assert "ORDER BY fire_report_event.at" in events


def test_environmental_metadata_statements() -> None:
    dataset = DatasetVersion(
        dataset_id="synthetic-v1",
        source="Air Health",
        product="scenario",
        version="m1-1",
        kind=InputKind.SYNTHETIC,
        region="delhi-ncr",
        attribution="Air Health synthetic scenario",
        license="test fixture",
        available_at=NOW,
    )
    dataset_sql = _sql(dataset_repo._upsert_stmt(dataset))
    assert "INSERT INTO dataset_version" in dataset_sql
    assert "ON CONFLICT" in dataset_sql
    assert "DO UPDATE SET" in dataset_sql

    run = IngestionRun(
        run_id="run-1",
        dataset_id=dataset.dataset_id,
        started_at=NOW,
        finished_at=LATER,
        fetched_at=LATER,
        status=IngestionRunStatus.SUCCEEDED,
        simulation_id="tiny-ci:winter_stagnation:42",
    )
    run_sql, params = _sql_and_params(run_repo._upsert_stmt(run))
    assert "INSERT INTO ingestion_run" in run_sql
    assert "ON CONFLICT" in run_sql
    assert "DO UPDATE SET" in run_sql
    assert params["errors"] == []
    assert params["metrics"] == {}


def test_feature_snapshot_statements() -> None:
    snapshot = FeatureSnapshot(
        h3_cell=CELL,
        issued_at=NOW,
        valid_at=LATER,
        horizon_hours=3,
        feature_schema_version="environmental-v1",
        vector=CellFeatureVector(current_pm25=18.0, rain_1h_mm=0.0),
    )
    sql, params = _sql_and_params(feature_repo._upsert_stmt("run-1", [snapshot]))
    assert "INSERT INTO cell_feature_snapshot" in sql
    assert "ON CONFLICT" in sql
    assert "ON CONFLICT (run_id, h3_cell, horizon_hours)" in sql
    assert any(
        isinstance(value, dict) and value.get("current_pm25") == 18.0
        for value in params.values()
    )
    assert "FROM cell_feature_snapshot" in _sql(feature_repo._list_for_run_stmt("run-1"))


def test_model_version_statements() -> None:
    version = ModelVersion(
        model_id="candidate-1h",
        artifact_uri="models/candidate.json",
        artifact_sha256="a" * 64,
        feature_schema_version="environmental-v1",
        feature_names=("rain_1h_mm", "wind_speed_ms"),
        trained_at=NOW,
        training_start=NOW,
        training_end=LATER,
        region="delhi-ncr",
        horizon_hours=1.0,
        metrics={"mae": 12.3},
        synthetic_only=True,
        status=ModelStatus.CANDIDATE,
    )
    upsert = _sql(model_version_repo._upsert_stmt(version))

    assert "INSERT INTO model_version" in upsert
    assert "ON CONFLICT (id) DO UPDATE" in upsert
    assert "feature_schema_version" in upsert
    assert "synthetic_only" in upsert
    assert "FROM model_version" in _sql(model_version_repo._get_stmt("candidate-1h"))
    listing = _sql(model_version_repo._list_stmt("delhi-ncr", 1.0, "candidate"))
    assert "model_version.region = 'delhi-ncr'" in listing
    assert "model_version.horizon_hours = 1.0" in listing
    assert "model_version.status = 'candidate'" in listing
