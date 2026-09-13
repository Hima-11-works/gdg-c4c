"""Pure validation tests for app.domain.types — no database involved."""

from datetime import UTC, datetime

import pytest

from app.domain.types import (
    Alert,
    AlertSeverity,
    BoundingBox,
    Coordinate,
    Forecast,
    GridState,
    SensorReading,
    WeatherReading,
    WeatherSample,
)

UTC_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
NAIVE_NOW = datetime(2026, 1, 1, 12, 0)


def test_bounding_box_accepts_valid_data() -> None:
    BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)


def test_bounding_box_rejects_inverted_latitude() -> None:
    with pytest.raises(ValueError, match="min_lat"):
        BoundingBox(min_lat=38.0, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)


def test_bounding_box_rejects_inverted_longitude() -> None:
    with pytest.raises(ValueError, match="min_lon"):
        BoundingBox(min_lat=37.6, min_lon=-122.0, max_lat=37.9, max_lon=-122.1)


@pytest.mark.parametrize(
    ("field", "value"),
    [("min_lat", 91.0), ("max_lat", -91.0), ("min_lon", 181.0), ("max_lon", -181.0)],
)
def test_bounding_box_rejects_out_of_range_coordinates(field: str, value: float) -> None:
    kwargs = {"min_lat": 37.6, "min_lon": -122.6, "max_lat": 37.9, "max_lon": -122.1, field: value}
    with pytest.raises(ValueError, match=field):
        BoundingBox(**kwargs)


def test_coordinate_accepts_valid_data() -> None:
    Coordinate(latitude=0.0, longitude=0.0)  # equator/prime meridian: not falsy-invalid


@pytest.mark.parametrize(
    ("field", "value"), [("latitude", 91.0), ("latitude", -91.0), ("longitude", 181.0)]
)
def test_coordinate_rejects_out_of_range(field: str, value: float) -> None:
    kwargs = {"latitude": 0.0, "longitude": 0.0, field: value}
    with pytest.raises(ValueError, match=field):
        Coordinate(**kwargs)


def test_weather_sample_accepts_valid_data() -> None:
    sample = WeatherSample(
        wind_speed=3.5, wind_direction=270.0, precipitation=0.0, measured_at=UTC_NOW
    )
    assert sample.boundary_layer_height is None


def test_weather_sample_rejects_negative_wind_speed() -> None:
    with pytest.raises(ValueError, match="wind_speed"):
        WeatherSample(wind_speed=-1.0, wind_direction=0.0, precipitation=0.0, measured_at=UTC_NOW)


def test_weather_sample_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="UTC"):
        WeatherSample(wind_speed=1.0, wind_direction=0.0, precipitation=0.0, measured_at=NAIVE_NOW)


def test_sensor_reading_accepts_valid_data() -> None:
    reading = SensorReading(
        source="openaq",
        external_sensor_id="123",
        latitude=37.7749,
        longitude=-122.4194,
        pollutant="pm25",
        value=12.3,
        unit="ug/m3",
        measured_at=UTC_NOW,
    )
    assert reading.id is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("latitude", 91.0), ("latitude", -91.0), ("longitude", 181.0), ("longitude", -181.0)],
)
def test_sensor_reading_rejects_out_of_range_coordinates(field: str, value: float) -> None:
    kwargs = {
        "source": "openaq",
        "external_sensor_id": "123",
        "latitude": 0.0,
        "longitude": 0.0,
        "pollutant": "pm25",
        "value": 12.3,
        "unit": "ug/m3",
        "measured_at": UTC_NOW,
        field: value,
    }
    with pytest.raises(ValueError, match=field):
        SensorReading(**kwargs)


def test_sensor_reading_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="UTC"):
        SensorReading(
            source="openaq",
            external_sensor_id="123",
            latitude=0.0,
            longitude=0.0,
            pollutant="pm25",
            value=1.0,
            unit="ug/m3",
            measured_at=NAIVE_NOW,
        )


def test_weather_reading_rejects_negative_wind_speed() -> None:
    with pytest.raises(ValueError, match="wind_speed"):
        WeatherReading(
            h3_cell="8828308281fffff",
            latitude=0.0,
            longitude=0.0,
            wind_speed=-1.0,
            wind_direction=180.0,
            precipitation=0.0,
            measured_at=UTC_NOW,
        )


def test_weather_reading_rejects_wind_direction_out_of_range() -> None:
    with pytest.raises(ValueError, match="wind_direction"):
        WeatherReading(
            h3_cell="8828308281fffff",
            latitude=0.0,
            longitude=0.0,
            wind_speed=1.0,
            wind_direction=360.0,
            precipitation=0.0,
            measured_at=UTC_NOW,
        )


def test_weather_reading_boundary_layer_height_is_optional() -> None:
    reading = WeatherReading(
        h3_cell="8828308281fffff",
        latitude=0.0,
        longitude=0.0,
        wind_speed=1.0,
        wind_direction=180.0,
        precipitation=0.0,
        measured_at=UTC_NOW,
    )
    assert reading.boundary_layer_height is None


def test_grid_state_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValueError, match="confidence"):
        GridState(
            h3_cell="8828308281fffff",
            timestamp=UTC_NOW,
            pm25=10.0,
            pdi=50.0,
            confidence=1.5,
            wind_speed=1.0,
            wind_direction=180.0,
        )


def test_grid_state_pollution_and_wind_fields_default_to_none() -> None:
    state = GridState(h3_cell="8828308281fffff", timestamp=UTC_NOW, confidence=0.0)

    assert state.pm25 is None
    assert state.pdi is None
    assert state.wind_speed is None
    assert state.wind_direction is None


def test_grid_state_rejects_negative_pm25() -> None:
    with pytest.raises(ValueError, match="pm25"):
        GridState(h3_cell="8828308281fffff", timestamp=UTC_NOW, confidence=0.5, pm25=-1.0)


def test_grid_state_rejects_negative_wind_speed() -> None:
    with pytest.raises(ValueError, match="wind_speed"):
        GridState(h3_cell="8828308281fffff", timestamp=UTC_NOW, confidence=0.5, wind_speed=-1.0)


def test_grid_state_rejects_wind_direction_out_of_range() -> None:
    with pytest.raises(ValueError, match="wind_direction"):
        GridState(
            h3_cell="8828308281fffff", timestamp=UTC_NOW, confidence=0.5, wind_direction=360.0
        )


@pytest.mark.parametrize("field", ["pm25", "pdi", "wind_speed", "wind_direction"])
def test_grid_state_rejects_non_finite_values(field: str) -> None:
    kwargs = {
        "h3_cell": "8828308281fffff",
        "timestamp": UTC_NOW,
        "confidence": 0.5,
        field: float("nan"),
    }
    with pytest.raises(ValueError, match="finite"):
        GridState(**kwargs)


def test_coordinate_distance_km_to_self_is_zero() -> None:
    point = Coordinate(37.7749, -122.4194)
    assert point.distance_km(point) == 0.0


def test_coordinate_distance_km_is_symmetric() -> None:
    a = Coordinate(37.7749, -122.4194)
    b = Coordinate(37.8044, -122.2712)
    assert a.distance_km(b) == pytest.approx(b.distance_km(a))


def test_coordinate_distance_km_matches_known_reference() -> None:
    # One degree of latitude is ~111.19 km everywhere; longitude has no
    # effect on this pair since they share a meridian.
    a = Coordinate(0.0, 0.0)
    b = Coordinate(1.0, 0.0)
    assert a.distance_km(b) == pytest.approx(111.19, abs=0.5)


def test_forecast_rejects_forecast_time_before_generated_at() -> None:
    with pytest.raises(ValueError, match="forecast_time"):
        Forecast(
            h3_cell="8828308281fffff",
            generated_at=UTC_NOW,
            forecast_time=UTC_NOW,
            forecast_hours=3,
            predicted_pm25=10.0,
            confidence=0.5,
        )


def test_forecast_rejects_non_positive_hours() -> None:
    with pytest.raises(ValueError, match="forecast_hours"):
        Forecast(
            h3_cell="8828308281fffff",
            generated_at=UTC_NOW,
            forecast_time=datetime(2026, 1, 1, 15, tzinfo=UTC),
            forecast_hours=0,
            predicted_pm25=10.0,
            confidence=0.5,
        )


def test_alert_rejects_empty_message() -> None:
    with pytest.raises(ValueError, match="message"):
        Alert(
            h3_cell="8828308281fffff",
            severity=AlertSeverity.WARNING,
            message="   ",
            created_at=UTC_NOW,
        )


def test_alert_forecast_time_is_optional() -> None:
    alert = Alert(
        h3_cell="8828308281fffff",
        severity=AlertSeverity.CRITICAL,
        message="PM2.5 rising fast",
        created_at=UTC_NOW,
    )
    assert alert.forecast_time is None


def test_alert_context_fields_default_to_none() -> None:
    alert = Alert(
        h3_cell="8828308281fffff",
        severity=AlertSeverity.WARNING,
        message="PM2.5 rising fast",
        created_at=UTC_NOW,
    )
    assert alert.current_pm25 is None
    assert alert.forecast_pm25 is None
    assert alert.forecast_hours is None
    assert alert.confidence is None


def test_alert_accepts_all_context_fields() -> None:
    alert = Alert(
        h3_cell="8828308281fffff",
        severity=AlertSeverity.WARNING,
        message="PM2.5 rising fast",
        created_at=UTC_NOW,
        current_pm25=60.0,
        forecast_pm25=90.0,
        forecast_hours=3,
        confidence=0.7,
    )
    assert alert.current_pm25 == 60.0
    assert alert.forecast_pm25 == 90.0
    assert alert.forecast_hours == 3
    assert alert.confidence == 0.7


def test_alert_rejects_negative_current_pm25() -> None:
    with pytest.raises(ValueError, match="current_pm25"):
        Alert(
            h3_cell="8828308281fffff",
            severity=AlertSeverity.WARNING,
            message="x",
            created_at=UTC_NOW,
            current_pm25=-1.0,
        )


def test_alert_rejects_negative_forecast_pm25() -> None:
    with pytest.raises(ValueError, match="forecast_pm25"):
        Alert(
            h3_cell="8828308281fffff",
            severity=AlertSeverity.WARNING,
            message="x",
            created_at=UTC_NOW,
            forecast_pm25=-1.0,
        )


@pytest.mark.parametrize("forecast_hours", [0, -1])
def test_alert_rejects_non_positive_forecast_hours(forecast_hours: int) -> None:
    with pytest.raises(ValueError, match="forecast_hours"):
        Alert(
            h3_cell="8828308281fffff",
            severity=AlertSeverity.WARNING,
            message="x",
            created_at=UTC_NOW,
            forecast_hours=forecast_hours,
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_alert_rejects_confidence_out_of_range(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        Alert(
            h3_cell="8828308281fffff",
            severity=AlertSeverity.WARNING,
            message="x",
            created_at=UTC_NOW,
            confidence=confidence,
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_sensor_reading_rejects_non_finite_value(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        SensorReading(
            source="openaq",
            external_sensor_id="1",
            latitude=0.0,
            longitude=0.0,
            pollutant="pm25",
            value=bad,
            unit="ug/m3",
            measured_at=UTC_NOW,
        )


@pytest.mark.parametrize("field", ["wind_speed", "wind_direction", "precipitation"])
def test_weather_sample_rejects_non_finite_values(field: str) -> None:
    kwargs = {
        "wind_speed": 1.0,
        "wind_direction": 90.0,
        "precipitation": 0.0,
        "measured_at": UTC_NOW,
        field: float("nan"),
    }
    with pytest.raises(ValueError, match="finite"):
        WeatherSample(**kwargs)
