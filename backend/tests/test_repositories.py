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
from app.domain.repositories import DuplicateReadingError
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

    with pytest.raises(DuplicateReadingError):
        repo.add(reading)


def test_sensor_reading_list_latest_returns_one_per_sensor(db_session) -> None:
    repo = SqlSensorReadingRepository(db_session)
    older = SensorReading(
        source="openaq",
        external_sensor_id="s1",
        latitude=0.0,
        longitude=0.0,
        pollutant="pm25",
        value=10.0,
        unit="ug/m3",
        measured_at=NOW - timedelta(hours=1),
    )
    newer = SensorReading(
        source="openaq",
        external_sensor_id="s1",
        latitude=0.0,
        longitude=0.0,
        pollutant="pm25",
        value=20.0,
        unit="ug/m3",
        measured_at=NOW,
    )
    repo.add(older)
    repo.add(newer)

    latest = repo.list_latest()

    matching = [r for r in latest if r.external_sensor_id == "s1"]
    assert len(matching) == 1
    assert matching[0].value == 20.0


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


def test_weather_reading_duplicate_identity_is_rejected(db_session, cell: str) -> None:
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
    repo.add(reading)

    with pytest.raises(DuplicateReadingError):
        repo.add(reading)


def test_weather_reading_list_latest_returns_one_per_cell(db_session, cell: str) -> None:
    repo = SqlWeatherReadingRepository(db_session)
    repo.add(
        WeatherReading(
            h3_cell=cell,
            latitude=37.7749,
            longitude=-122.4194,
            wind_speed=1.0,
            wind_direction=0.0,
            precipitation=0.0,
            measured_at=NOW - timedelta(hours=1),
        )
    )
    repo.add(
        WeatherReading(
            h3_cell=cell,
            latitude=37.7749,
            longitude=-122.4194,
            wind_speed=2.0,
            wind_direction=90.0,
            precipitation=0.0,
            measured_at=NOW,
        )
    )

    latest = repo.list_latest()

    matching = [r for r in latest if r.h3_cell == cell]
    assert len(matching) == 1
    assert matching[0].wind_speed == 2.0


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


def test_grid_state_with_null_pollution_fields_round_trips(db_session, cell: str) -> None:
    """A cell with insufficient evidence (see app.services.estimation) has
    pm25/pdi/wind as None, not a fabricated value — the columns must
    actually be nullable, not just accepted by the domain type."""
    repo = SqlGridStateRepository(db_session)

    result = repo.upsert(GridState(h3_cell=cell, timestamp=NOW, confidence=0.0))

    assert result.pm25 is None
    assert result.pdi is None
    assert result.wind_speed is None
    assert result.wind_direction is None
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


def test_grid_state_latest_for_cell_returns_most_recent_row(db_session, cell: str) -> None:
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

    result = repo.latest_for_cell(cell)

    assert result is not None
    assert result.timestamp == later
    assert repo.latest_for_cell("8828308281fffff") is None


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


def test_forecast_latest_for_horizon_returns_one_row_per_cell(db_session) -> None:
    repo = SqlForecastRepository(db_session)
    resolution = get_settings().h3_resolution
    cell_a = h3.latlng_to_cell(37.7749, -122.4194, resolution)
    cell_b = h3.latlng_to_cell(37.8044, -122.2712, resolution)

    repo.add(
        Forecast(
            h3_cell=cell_a,
            generated_at=NOW,
            forecast_time=NOW + timedelta(hours=1),
            forecast_hours=1,
            predicted_pm25=10.0,
            confidence=0.7,
        )
    )
    repo.add(
        Forecast(
            h3_cell=cell_a,
            generated_at=NOW + timedelta(minutes=30),
            forecast_time=NOW + timedelta(hours=1, minutes=30),
            forecast_hours=1,
            predicted_pm25=11.0,
            confidence=0.7,
        )
    )
    repo.add(
        Forecast(
            h3_cell=cell_b,
            generated_at=NOW,
            forecast_time=NOW + timedelta(hours=1),
            forecast_hours=1,
            predicted_pm25=20.0,
            confidence=0.7,
        )
    )
    # A different horizon must not show up in the hours=1 result.
    repo.add(
        Forecast(
            h3_cell=cell_a,
            generated_at=NOW,
            forecast_time=NOW + timedelta(hours=3),
            forecast_hours=3,
            predicted_pm25=99.0,
            confidence=0.7,
        )
    )

    results = {f.h3_cell: f for f in repo.latest_for_horizon(1)}

    assert set(results) == {cell_a, cell_b}
    assert results[cell_a].predicted_pm25 == 11.0  # the later run for cell_a wins
    assert results[cell_b].predicted_pm25 == 20.0


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
