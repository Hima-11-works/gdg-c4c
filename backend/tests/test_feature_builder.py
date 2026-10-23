"""Acceptance tests for M2 as-of feature construction."""

from datetime import UTC, datetime, timedelta

import h3

from app.domain.features import CellStaticFeatures, WeatherFeature
from app.domain.types import PM25, Coordinate, SensorReading
from app.services.features import FeatureBuilder, feature_snapshot_to_dict

NOW = datetime(2025, 1, 15, 12, tzinfo=UTC)
CELL = h3.latlng_to_cell(28.6139, 77.2090, 8)


def _weather(
    *,
    cell: str = CELL,
    rain: float = 0.0,
    valid_at: datetime = NOW,
    wind_speed: float = 2.0,
) -> WeatherFeature:
    return WeatherFeature(
        h3_cell=cell,
        issued_at=NOW,
        valid_at=valid_at,
        wind_u_ms=2.0,
        wind_v_ms=0.0,
        wind_speed_ms=wind_speed,
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


def _reading(
    value: float,
    measured_at: datetime,
    *,
    source: str = "test",
    external_sensor_id: str = "station-1",
    canonical_station_id: str | None = None,
    received_at: datetime | None = None,
) -> SensorReading:
    latitude, longitude = h3.cell_to_latlng(CELL)
    return SensorReading(
        source=source,
        external_sensor_id=external_sensor_id,
        latitude=latitude,
        longitude=longitude,
        pollutant=PM25,
        value=value,
        unit="ug/m3",
        measured_at=measured_at,
        canonical_station_id=canonical_station_id,
        received_at=received_at,
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


def test_traffic_ratio_is_observed_over_free_flow_and_zero_speed_is_valid() -> None:
    snapshots = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[_weather()],
        static_features=[_static()],
        traffic_observations=[
            {"h3_cell": CELL, "valid_at": NOW, "speed_kph": 30.0, "free_flow_kph": 60.0}
        ],
        fire_detections=[],
    )
    standstill = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[_weather()],
        static_features=[_static()],
        traffic_observations=[
            {"h3_cell": CELL, "valid_at": NOW, "speed_kph": 0.0, "free_flow_kph": 60.0}
        ],
        fire_detections=[],
    )

    assert snapshots[0].vector.traffic_congestion_ratio == 0.5
    assert standstill[0].vector.traffic_congestion_ratio == 0.0


def test_traffic_observation_unavailable_at_issue_time_is_not_used() -> None:
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[_weather()],
        static_features=[_static()],
        traffic_observations=[
            {
                "h3_cell": CELL,
                "valid_at": NOW,
                "available_at": NOW + timedelta(minutes=10),
                "speed_kph": 10.0,
                "free_flow_kph": 60.0,
            }
        ],
        fire_detections=[],
    )[0]

    assert snapshot.vector.traffic_congestion_ratio is None
    assert "traffic" in snapshot.quality.missing_fields


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


def test_weather_fallback_selects_nearest_spatial_sample() -> None:
    neighbors = [cell for cell in h3.grid_disk(CELL, 2) if cell != CELL]
    target = Coordinate(*h3.cell_to_latlng(CELL))
    ordered = sorted(
        neighbors,
        key=lambda cell: target.distance_km(Coordinate(*h3.cell_to_latlng(cell))),
    )
    nearest, farther = ordered[:2]

    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[
            _weather(cell=farther, wind_speed=9.0),
            _weather(cell=nearest, wind_speed=1.0),
        ],
    )[0]

    assert snapshot.vector.wind_speed_ms == 1.0


def test_exact_weather_cell_matches_skip_spatial_distance_search(monkeypatch) -> None:
    cells = list(h3.grid_disk(CELL, 2))
    distance_calls = 0

    def counted_distance(self: Coordinate, other: Coordinate) -> float:
        nonlocal distance_calls
        distance_calls += 1
        return 0.0

    monkeypatch.setattr(Coordinate, "distance_km", counted_distance)
    snapshots = FeatureBuilder().build(
        cells=cells,
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[
            _weather(cell=cell, wind_speed=float(index))
            for index, cell in enumerate(cells)
        ],
    )

    assert len(snapshots) == len(cells)
    assert distance_calls == 0


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


def test_missing_optional_static_features_do_not_zero_core_coverage() -> None:
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        sensor_readings=[_reading(24.0, NOW)],
        weather_features=[_weather()],
    )[0]

    assert snapshot.quality.observed_station_count == 1
    assert snapshot.quality.coverage_fraction == 1.0
    assert {"population", "roads", "land_cover"}.issubset(
        snapshot.quality.missing_fields
    )


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


def test_future_weather_with_unknown_issue_and_receipt_times_is_not_used() -> None:
    future_valid = NOW + timedelta(hours=3)
    unversioned = WeatherFeature(
        h3_cell=CELL,
        issued_at=None,
        valid_at=future_valid,
        precipitation_mm=2.0,
    )

    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=future_valid,
        horizon_hours=3,
        weather_features=[unversioned],
    )[0]

    assert snapshot.vector.wind_speed_ms is None
    assert snapshot.vector.rain_1h_mm is None
    assert "weather" in snapshot.quality.missing_fields


def test_max_observation_age_does_not_include_weather_valid_time_age() -> None:
    old_weather = WeatherFeature(
        h3_cell=CELL,
        issued_at=NOW - timedelta(hours=50),
        valid_at=NOW - timedelta(hours=50),
        wind_speed_ms=2.0,
    )
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        sensor_readings=[_reading(30.0, NOW - timedelta(hours=1))],
        weather_features=[old_weather],
    )[0]

    assert snapshot.quality.max_observation_age_hours == 1.0
    assert "weather_stale" in snapshot.quality.warnings


def test_canonical_station_count_deduplicates_across_sources() -> None:
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        sensor_readings=[
            _reading(
                20.0,
                NOW - timedelta(minutes=10),
                source="openaq",
                external_sensor_id="openaq-id",
                canonical_station_id="shared-station-1",
            ),
            _reading(
                21.0,
                NOW - timedelta(minutes=5),
                source="cpcb",
                external_sensor_id="cpcb-id",
                canonical_station_id="shared-station-1",
            ),
        ],
    )[0]

    assert snapshot.quality.observed_station_count == 1


def test_duplicate_canonical_station_observation_is_not_double_weighted() -> None:
    measured_at = NOW - timedelta(minutes=10)
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        sensor_readings=[
            _reading(
                10.0,
                measured_at,
                source="openaq",
                external_sensor_id="openaq-id",
                canonical_station_id="shared-station-1",
                received_at=NOW - timedelta(minutes=8),
            ),
            _reading(
                30.0,
                measured_at,
                source="cpcb",
                external_sensor_id="cpcb-id",
                canonical_station_id="shared-station-1",
                received_at=NOW - timedelta(minutes=2),
            ),
            _reading(20.0, NOW - timedelta(minutes=5)),
        ],
    )[0]

    assert snapshot.vector.pm25_mean_6h == 25.0


def _fire(*, longitude: float, available_at: datetime | None = NOW) -> dict[str, object]:
    latitude, _ = h3.cell_to_latlng(CELL)
    fire: dict[str, object] = {
        "latitude": latitude,
        "longitude": longitude,
        "acquired_at": NOW - timedelta(minutes=20),
        "frp_mw": 12.5,
        "confidence_class": "nominal",
        "quality_flags": (),
    }
    if available_at is not None:
        fire["available_at"] = available_at
    return fire


def test_fire_features_only_count_available_upwind_detections() -> None:
    _, longitude = h3.cell_to_latlng(CELL)
    west = _fire(longitude=longitude - 0.08)
    east = _fire(longitude=longitude + 0.08)
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[_weather()],
        fire_detections=[west, east],
    )[0]

    assert snapshot.vector.fire_count_upwind == 1.0
    assert snapshot.vector.fire_frp_upwind_mw == 12.5


def test_fire_detections_available_after_issue_time_do_not_leak() -> None:
    _, longitude = h3.cell_to_latlng(CELL)
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[_weather()],
        fire_detections=[
            _fire(
                longitude=longitude - 0.08,
                available_at=NOW + timedelta(minutes=1),
            )
        ],
    )[0]

    assert snapshot.vector.fire_count_upwind == 0.0
    assert snapshot.vector.fire_frp_upwind_mw == 0.0


def test_fire_detection_without_availability_time_is_missing_not_zero() -> None:
    _, longitude = h3.cell_to_latlng(CELL)
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[_weather()],
        fire_detections=[_fire(longitude=longitude - 0.08, available_at=None)],
    )[0]

    assert snapshot.vector.fire_count_upwind is None
    assert snapshot.vector.fire_frp_upwind_mw is None
    assert "fire_availability" in snapshot.quality.missing_fields
    assert "fire_availability_unknown" in snapshot.quality.warnings


def test_stale_fire_detection_is_missing_not_zero() -> None:
    _, longitude = h3.cell_to_latlng(CELL)
    stale = _fire(longitude=longitude - 0.08)
    stale["quality_flags"] = ("stale_detection",)
    snapshot = FeatureBuilder().build(
        cells=[CELL],
        issued_at=NOW,
        valid_at=NOW,
        weather_features=[_weather()],
        fire_detections=[stale],
    )[0]

    assert snapshot.vector.fire_count_upwind is None
    assert snapshot.vector.fire_frp_upwind_mw is None
    assert "fires" in snapshot.quality.missing_fields
    assert "fire_detections_stale" in snapshot.quality.warnings
