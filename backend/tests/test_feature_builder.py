"""Acceptance tests for M2 as-of feature construction."""

from datetime import UTC, datetime, timedelta

import h3

from app.domain.features import CellStaticFeatures, WeatherFeature
from app.domain.types import PM25, SensorReading
from app.services.features import FeatureBuilder, feature_snapshot_to_dict

NOW = datetime(2025, 1, 15, 12, tzinfo=UTC)
CELL = h3.latlng_to_cell(28.6139, 77.2090, 8)


def _weather(*, rain: float = 0.0, valid_at: datetime = NOW) -> WeatherFeature:
    return WeatherFeature(
        h3_cell=CELL,
        issued_at=NOW,
        valid_at=valid_at,
        wind_u_ms=2.0,
        wind_v_ms=0.0,
        wind_speed_ms=2.0,
        wind_direction_deg=270.0,
        precipitation_mm=rain,
        boundary_layer_height_m=450.0,
        temperature_c=20.0,
        relative_humidity_pct=70.0,
    )


def _static(*, population: float | None = 1200.0) -> CellStaticFeatures:
    return CellStaticFeatures(
        h3_cell=CELL,
        population_count=population,
        population_density_per_km2=None if population is None else population / 0.7,
        road_length_km_by_class={"primary": 2.0},
        major_road_distance_km=0.4,
        built_up_fraction=0.5,
        vegetation_fraction=0.3,
        bare_soil_fraction=0.2,
        industrial_fraction=0.1,
        available_at=NOW,
    )


def _reading(value: float, measured_at: datetime) -> SensorReading:
    latitude, longitude = h3.cell_to_latlng(CELL)
    return SensorReading(
        source="test",
        external_sensor_id="station-1",
        latitude=latitude,
        longitude=longitude,
        pollutant=PM25,
        value=value,
        unit="ug/m3",
        measured_at=measured_at,
    )


def test_as_of_cutoff_excludes_future_observations_and_computes_lags() -> None:
    readings = [
        _reading(20.0, NOW - timedelta(hours=3)),
        _reading(30.0, NOW - timedelta(hours=1)),
        _reading(99.0, NOW + timedelta(hours=1)),
    ]
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        sensor_readings=readings,
        weather_features=[_weather()],
        static_features=[_static()],
        traffic_observations=[
            {"h3_cell": CELL, "valid_at": NOW, "congestion_ratio": 0.0}
        ],
        fire_detections=[],
    )[0]

    assert snapshot.vector.current_pm25 == 30.0
    assert snapshot.vector.pm25_lag_1h == 30.0
    assert snapshot.vector.pm25_lag_3h == 20.0
    assert snapshot.vector.traffic_congestion_ratio == 0.0
    assert "pollution" not in snapshot.quality.missing_fields


def test_zero_rain_is_observed_but_missing_weather_stays_null() -> None:
    builder = FeatureBuilder()
    observed = builder.build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[_weather(rain=0.0)],
        static_features=[_static()],
        fire_detections=[],
    )[0]
    missing = builder.build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=None,
        static_features=[_static()],
        fire_detections=[],
    )[0]

    assert observed.vector.rain_1h_mm == 0.0
    assert "weather" not in observed.quality.missing_fields
    assert missing.vector.rain_1h_mm is None
    assert "weather" in missing.quality.missing_fields


def test_calendar_and_static_features_are_derived_without_zero_fabrication() -> None:
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        static_features=[_static(population=0.0)],
        fire_detections=[],
    )[0]
    payload = feature_snapshot_to_dict(snapshot)

    assert snapshot.vector.population_count == 0.0
    assert "population" not in snapshot.quality.missing_fields
    assert snapshot.vector.road_length_km_per_km2 is not None
    assert snapshot.vector.is_weekend == 0.0
    assert payload["vector"]["population_count"] == 0.0


def test_future_weather_is_allowed_only_when_issued_before_the_cutoff() -> None:
    future_valid = NOW + timedelta(hours=3)
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=future_valid,
        horizon_hours=3,
        weather_features=[_weather(rain=2.0, valid_at=future_valid)],
        static_features=[_static()],
        fire_detections=[],
    )[0]

    assert snapshot.vector.rain_1h_mm == 2.0
    assert snapshot.valid_at == future_valid
