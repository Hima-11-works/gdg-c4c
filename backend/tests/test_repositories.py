"""Round-trip tests for app.db.repositories against real PostgreSQL/PostGIS.

Skipped unless RUN_DB_TESTS=1:

    docker compose up -d db
    RUN_DB_TESTS=1 pytest tests/test_repositories.py
"""

import os
from datetime import UTC, datetime, timedelta

import h3
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.repositories import (
    SqlAlertRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlSensorReadingRepository,
    SqlWeatherReadingRepository,
)
from app.domain.types import (
    Alert,
    AlertSeverity,
    Forecast,
    GridState,
    SensorReading,
    WeatherReading,
)
from app.models.tables import sensor_reading as sensor_reading_table

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 with PostgreSQL/PostGIS running",
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def cell() -> str:
    return h3.latlng_to_cell(37.7749, -122.4194, get_settings().h3_resolution)


def test_sensor_reading_round_trip(db_session) -> None:
    repo = SqlSensorReadingRepository(db_session)
    reading = SensorReading(
        source="openaq",
        external_sensor_id="abc",
        latitude=37.7749,
        longitude=-122.4194,
        pollutant="pm25",
        value=12.3,
        unit="ug/m3",
        measured_at=NOW,
    )

    saved = repo.add(reading)

    assert saved.id is not None
    assert saved in repo.list_since(NOW - timedelta(hours=1))
    assert repo.list_since(NOW - timedelta(hours=1), pollutant="no2") == []


def test_sensor_reading_duplicate_identity_is_rejected(db_session) -> None:
    repo = SqlSensorReadingRepository(db_session)
    reading = SensorReading(
        source="openaq",
        external_sensor_id="dup",
        latitude=0.0,
        longitude=0.0,
        pollutant="pm25",
        value=1.0,
        unit="ug/m3",
        measured_at=NOW,
    )
    repo.add(reading)

    with pytest.raises(IntegrityError):
        repo.add(reading)


def test_sensor_reading_check_constraint_rejects_bad_latitude(db_session) -> None:
    # The domain dataclass already rejects this before it reaches SQL; this
    # proves the database CHECK constraint is a real backstop, not just
    # documentation, for rows written some other way.
    with pytest.raises(IntegrityError):
        db_session.execute(
            sensor_reading_table.insert().values(
                source="x",
                external_sensor_id="x",
                latitude=999.0,
                longitude=0.0,
                geom="POINT(0 0)",
                pollutant="pm25",
                value=1.0,
                unit="ug/m3",
                measured_at=NOW,
            )
        )
        db_session.commit()


def test_weather_reading_round_trip_and_latest_for_cell(db_session, cell: str) -> None:
    repo = SqlWeatherReadingRepository(db_session)
    reading = WeatherReading(
        h3_cell=cell,
        latitude=37.7749,
        longitude=-122.4194,
        wind_speed=3.2,
        wind_direction=270.0,
        precipitation=0.0,
        measured_at=NOW,
    )

    saved = repo.add(reading)

    assert saved.id is not None
    assert repo.latest_for_cell(cell) == saved
    assert repo.latest_for_cell("8828308281fffff") != saved  # different cell


def test_weather_reading_rejects_cell_at_wrong_resolution(db_session) -> None:
    wrong_cell = h3.latlng_to_cell(37.7749, -122.4194, get_settings().h3_resolution + 1)
    repo = SqlWeatherReadingRepository(db_session)
    reading = WeatherReading(
        h3_cell=wrong_cell,
        latitude=0.0,
        longitude=0.0,
        wind_speed=1.0,
        wind_direction=0.0,
        precipitation=0.0,
        measured_at=NOW,
    )

    with pytest.raises(ValueError, match="resolution"):
        repo.add(reading)


def test_grid_state_upsert_updates_existing_row(db_session, cell: str) -> None:
    repo = SqlGridStateRepository(db_session)
    repo.upsert(
        GridState(
            h3_cell=cell,
            timestamp=NOW,
            pm25=10.0,
            pdi=20.0,
            confidence=0.5,
            wind_speed=1.0,
            wind_direction=180.0,
        )
    )

    result = repo.upsert(
        GridState(
            h3_cell=cell,
            timestamp=NOW,
            pm25=15.0,
            pdi=40.0,
            confidence=0.9,
            wind_speed=2.0,
            wind_direction=190.0,
        )
    )

    assert result.pm25 == 15.0
    assert repo.get(cell, NOW) == result


def test_grid_state_latest_returns_one_row_per_cell(db_session, cell: str) -> None:
    repo = SqlGridStateRepository(db_session)
    repo.upsert(
        GridState(
            h3_cell=cell,
            timestamp=NOW,
            pm25=10.0,
            pdi=20.0,
            confidence=0.5,
            wind_speed=1.0,
            wind_direction=180.0,
        )
    )
    later = NOW + timedelta(hours=1)
    repo.upsert(
        GridState(
            h3_cell=cell,
            timestamp=later,
            pm25=12.0,
            pdi=25.0,
            confidence=0.6,
            wind_speed=1.5,
            wind_direction=185.0,
        )
    )

    matching = [state for state in repo.latest() if state.h3_cell == cell]

    assert len(matching) == 1
    assert matching[0].timestamp == later


def test_forecast_round_trip_and_latest_for_cell(db_session, cell: str) -> None:
    repo = SqlForecastRepository(db_session)
    for hours in (1, 3, 6):
        repo.add(
            Forecast(
                h3_cell=cell,
                generated_at=NOW,
                forecast_time=NOW + timedelta(hours=hours),
                forecast_hours=hours,
                predicted_pm25=10.0 + hours,
                confidence=0.7,
            )
        )

    latest = repo.latest_for_cell(cell)

    assert [f.forecast_hours for f in latest] == [1, 3, 6]
    assert repo.list_for_cell(cell) == latest


def test_forecast_duplicate_run_and_horizon_is_rejected(db_session, cell: str) -> None:
    repo = SqlForecastRepository(db_session)
    forecast = Forecast(
        h3_cell=cell,
        generated_at=NOW,
        forecast_time=NOW + timedelta(hours=1),
        forecast_hours=1,
        predicted_pm25=10.0,
        confidence=0.7,
    )
    repo.add(forecast)

    with pytest.raises(IntegrityError):
        repo.add(forecast)


def test_alert_round_trip_and_list_active(db_session, cell: str) -> None:
    repo = SqlAlertRepository(db_session)
    alert = Alert(
        h3_cell=cell,
        severity=AlertSeverity.WARNING,
        message="PM2.5 rising",
        created_at=NOW,
    )

    saved = repo.add(alert)

    assert saved.id is not None
    assert saved in repo.list_active(since=NOW - timedelta(hours=1))
    assert repo.list_active(since=NOW + timedelta(hours=1)) == []
