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
from app.db.repositories import forecast as forecast_repo
from app.db.repositories import grid_state as grid_state_repo
from app.db.repositories import sensor_reading as sensor_reading_repo
from app.db.repositories import weather_reading as weather_reading_repo
from app.domain.types import (
    Alert,
    AlertSeverity,
    Forecast,
    GridState,
    SensorReading,
    WeatherReading,
)

DIALECT = postgresql.dialect()
NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 1, 3, tzinfo=UTC)
CELL = h3.latlng_to_cell(37.7749, -122.4194, 8)


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
