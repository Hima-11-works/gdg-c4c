"""Deterministic tests for app.services.estimation.IDWPollutionEstimator,
using synthetic sensor layouts placed at hand-computable distances from a
real H3 cell's center (no real sensor data, no database, no randomness).
"""

from __future__ import annotations

from datetime import UTC, datetime

import h3
import pytest

from app.domain.types import PM25, Coordinate, SensorReading
from app.services.estimation import IDWPollutionEstimator

TIMESTAMP = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
CELL = h3.latlng_to_cell(37.7749, -122.4194, 9)
CELL_LAT, CELL_LON = h3.cell_to_latlng(CELL)
CENTER = Coordinate(CELL_LAT, CELL_LON)

_KM_PER_DEGREE_LAT = 111.19


def _point_km_north(km: float) -> Coordinate:
    """A point due north of the cell center at (approximately) `km`
    kilometers — due-north offsets avoid longitude-scaling complications,
    so the actual distance (computed the same way the estimator does,
    via Coordinate.distance_km) comes out predictably close to `km`.
    """
    return Coordinate(CELL_LAT + km / _KM_PER_DEGREE_LAT, CELL_LON)


def _sensor(external_sensor_id: str, point: Coordinate, value: float) -> SensorReading:
    return SensorReading(
        source="openaq",
        external_sensor_id=external_sensor_id,
        latitude=point.latitude,
        longitude=point.longitude,
        pollutant=PM25,
        value=value,
        unit="ug/m3",
        measured_at=TIMESTAMP,
    )


def _idw(*, max_distance_km: float = 20.0, min_sensors: int = 1, power: float = 2.0):
    return IDWPollutionEstimator(
        max_distance_km=max_distance_km, min_sensors=min_sensors, power=power
    )


# --- construction / validation ---


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_distance_km": 0, "min_sensors": 1},
        {"max_distance_km": -5, "min_sensors": 1},
        {"max_distance_km": 10, "min_sensors": 0},
        {"max_distance_km": 10, "min_sensors": -1},
        {"max_distance_km": 10, "min_sensors": 1, "power": 0},
        {"max_distance_km": 10, "min_sensors": 1, "power": -1},
    ],
)
def test_rejects_invalid_configuration(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        IDWPollutionEstimator(**kwargs)


# --- empty inputs ---


def test_empty_grid_returns_empty_list() -> None:
    sensors = [_sensor("a", _point_km_north(1.0), 10.0)]
    assert _idw().estimate([], sensors, timestamp=TIMESTAMP) == []


def test_no_sensors_gives_every_cell_null_pollution_and_zero_confidence() -> None:
    [result] = _idw(min_sensors=1).estimate([CELL], [], timestamp=TIMESTAMP)

    assert result.h3_cell == CELL
    assert result.timestamp == TIMESTAMP
    assert result.pm25 is None
    assert result.pdi is None
    assert result.confidence == 0.0


# --- insufficient evidence: null rather than fabricated ---


def test_fewer_than_min_sensors_within_range_gives_null_not_fabricated() -> None:
    sensors = [_sensor("a", _point_km_north(2.0), 10.0), _sensor("b", _point_km_north(3.0), 20.0)]

    [result] = _idw(max_distance_km=20.0, min_sensors=3).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )

    assert result.pm25 is None
    assert result.confidence == 0.0


def test_sensors_beyond_max_distance_are_excluded_from_the_count() -> None:
    sensors = [_sensor("far", _point_km_north(50.0), 999.0)]

    [result] = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )

    assert result.pm25 is None
    assert result.confidence == 0.0


def test_exactly_min_sensors_within_range_produces_a_value() -> None:
    sensors = [_sensor("a", _point_km_north(2.0), 10.0), _sensor("b", _point_km_north(3.0), 20.0)]

    [result] = _idw(max_distance_km=20.0, min_sensors=2).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )

    assert result.pm25 is not None
    assert result.confidence > 0.0


def test_sensor_exactly_at_max_distance_boundary_is_included() -> None:
    point = _point_km_north(10.0)
    exact_distance = CENTER.distance_km(point)
    sensor = _sensor("boundary", point, 42.0)

    [result] = _idw(max_distance_km=exact_distance, min_sensors=1).estimate(
        [CELL], [sensor], timestamp=TIMESTAMP
    )

    assert result.pm25 == pytest.approx(42.0)


# --- IDW weighting math ---


def test_two_equidistant_sensors_average_equally() -> None:
    """Same distance -> equal weights -> the plain mean."""
    same_distance = 5.0
    sensors = [
        _sensor("a", _point_km_north(same_distance), 10.0),
        # A second point at the same distance but a different bearing
        # (south instead of north) so the two aren't literally the same
        # point.
        _sensor("b", Coordinate(CELL_LAT - same_distance / _KM_PER_DEGREE_LAT, CELL_LON), 30.0),
    ]

    [result] = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )

    assert result.pm25 == pytest.approx(20.0, abs=0.01)  # (10 + 30) / 2


def test_idw_matches_hand_computed_weighted_average() -> None:
    """The core IDW formula, checked against a manual computation using
    the exact distances Coordinate.distance_km produces (not the nominal
    km offsets, which the haversine formula only approximates)."""
    near, far = _point_km_north(5.0), _point_km_north(10.0)
    d_near, d_far = CENTER.distance_km(near), CENTER.distance_km(far)
    sensors = [_sensor("near", near, 10.0), _sensor("far", far, 50.0)]

    [result] = _idw(max_distance_km=20.0, min_sensors=1, power=2.0).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )

    w_near, w_far = 1.0 / d_near**2, 1.0 / d_far**2
    expected = (w_near * 10.0 + w_far * 50.0) / (w_near + w_far)
    assert result.pm25 == pytest.approx(expected)


def test_closer_sensor_has_more_influence_than_farther_sensor() -> None:
    sensors = [
        _sensor("near", _point_km_north(1.0), 10.0),
        _sensor("far", _point_km_north(15.0), 100.0),
    ]

    [result] = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )

    # Much closer to the near sensor's value than the midpoint (55.0).
    assert result.pm25 < 55.0
    assert result.pm25 == pytest.approx(10.0, abs=5.0)


def test_higher_power_increases_the_nearer_sensors_dominance() -> None:
    sensors = [
        _sensor("near", _point_km_north(2.0), 10.0),
        _sensor("far", _point_km_north(10.0), 100.0),
    ]

    low_power = _idw(max_distance_km=20.0, min_sensors=1, power=1.0).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )[0]
    high_power = _idw(max_distance_km=20.0, min_sensors=1, power=4.0).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )[0]

    assert high_power.pm25 < low_power.pm25  # higher power pulls closer to the near sensor


# --- divide-by-zero avoidance ---


def test_sensor_at_the_exact_cell_center_does_not_raise() -> None:
    sensor = _sensor("exact", CENTER, 42.0)

    [result] = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], [sensor], timestamp=TIMESTAMP
    )

    assert result.pm25 == 42.0
    assert result.confidence == 1.0


def test_sensor_very_near_but_not_exactly_at_center_does_not_raise() -> None:
    # A few meters away — inside the numerical-stability floor, but not
    # bit-for-bit identical to the center coordinate.
    barely_off = Coordinate(CELL_LAT + 1e-6, CELL_LON)
    sensor = _sensor("near-exact", barely_off, 42.0)

    [result] = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], [sensor], timestamp=TIMESTAMP
    )

    assert result.pm25 == 42.0
    assert result.confidence == 1.0


def test_an_exact_match_among_several_sensors_wins_outright() -> None:
    """IDW's textbook handling of a zero-distance point: use it directly,
    not blended with the others (whose weights would be finite but tiny
    by comparison anyway)."""
    sensors = [
        _sensor("exact", CENTER, 42.0),
        _sensor("far", _point_km_north(15.0), 999.0),
    ]

    [result] = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )

    assert result.pm25 == 42.0


# --- negative sensor readings (calibration noise near true zero) ---


def test_negative_exact_match_reading_is_treated_as_zero_not_negative() -> None:
    # Low-cost PM2.5 sensors commonly report small negative values right
    # around true-zero concentration; SensorReading.value has no >= 0
    # check (the raw station reading is preserved as ingested), so the
    # estimator must not let this reach GridState's >= 0 validation and
    # raise, or report a physically meaningless negative concentration.
    sensor = _sensor("noisy", CENTER, -2.5)

    [result] = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], [sensor], timestamp=TIMESTAMP
    )

    assert result.pm25 == 0.0
    assert result.confidence == 1.0


def test_negative_weighted_reading_does_not_pull_the_estimate_below_zero() -> None:
    sensors = [
        _sensor("noisy", _point_km_north(2.0), -1.0),
        _sensor("clean", _point_km_north(2.0), 3.0),
    ]

    [result] = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )

    # Equidistant, so this is the plain average of the two *clamped*
    # values (0.0 and 3.0), not the raw values (-1.0 and 3.0).
    assert result.pm25 == pytest.approx(1.5)
    assert result.pm25 >= 0.0


# --- confidence ---


def test_confidence_is_bounded_between_zero_and_one() -> None:
    sensors = [_sensor(f"s{i}", _point_km_north(i + 0.5), float(i)) for i in range(5)]

    for result in _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    ):
        assert 0.0 <= result.confidence <= 1.0


def test_confidence_decreases_as_the_nearest_sensor_gets_farther() -> None:
    close = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], [_sensor("a", _point_km_north(1.0), 10.0)], timestamp=TIMESTAMP
    )[0]
    far = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], [_sensor("a", _point_km_north(15.0), 10.0)], timestamp=TIMESTAMP
    )[0]

    assert close.confidence > far.confidence


def test_confidence_increases_with_more_corroborating_sensors() -> None:
    one_sensor = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], [_sensor("a", _point_km_north(5.0), 10.0)], timestamp=TIMESTAMP
    )[0]
    three_sensors = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL],
        [_sensor(f"s{i}", _point_km_north(5.0 + i), 10.0) for i in range(3)],
        timestamp=TIMESTAMP,
    )[0]

    assert three_sensors.confidence > one_sensor.confidence


# --- multi-cell grids ---


def test_each_cell_in_the_grid_gets_its_own_independent_result() -> None:
    covered_cell = CELL
    # A cell far away from every sensor, in a different part of the world.
    empty_cell = h3.latlng_to_cell(-33.8688, 151.2093, 9)  # Sydney
    sensors = [_sensor("a", _point_km_north(2.0), 10.0)]

    results = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [covered_cell, empty_cell], sensors, timestamp=TIMESTAMP
    )

    by_cell = {r.h3_cell: r for r in results}
    assert len(results) == 2
    assert by_cell[covered_cell].pm25 is not None
    assert by_cell[empty_cell].pm25 is None
    assert by_cell[empty_cell].confidence == 0.0


# --- scope: PDI and wind are never fabricated by this estimator ---


def test_pdi_and_wind_are_always_none() -> None:
    sensor = _sensor("a", CENTER, 42.0)
    [result] = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], [sensor], timestamp=TIMESTAMP
    )

    assert result.pdi is None
    assert result.wind_speed is None
    assert result.wind_direction is None


# --- determinism ---


def test_estimate_is_deterministic_across_repeated_calls() -> None:
    sensors = [
        _sensor("a", _point_km_north(3.0), 12.5),
        _sensor("b", _point_km_north(7.0), 27.3),
    ]
    estimator = _idw(max_distance_km=20.0, min_sensors=1)

    first = estimator.estimate([CELL], sensors, timestamp=TIMESTAMP)
    second = estimator.estimate([CELL], sensors, timestamp=TIMESTAMP)

    assert first == second


def test_configuration_changes_the_result_for_identical_inputs() -> None:
    """The same synthetic layout, only the configured thresholds differ —
    demonstrating max_distance_km and min_sensors are genuinely
    load-bearing, not decorative constructor arguments."""
    sensors = [_sensor("a", _point_km_north(5.0), 10.0), _sensor("b", _point_km_north(8.0), 20.0)]

    permissive = _idw(max_distance_km=20.0, min_sensors=1).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )[0]
    strict_distance = _idw(max_distance_km=6.0, min_sensors=1).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )[0]
    strict_count = _idw(max_distance_km=20.0, min_sensors=3).estimate(
        [CELL], sensors, timestamp=TIMESTAMP
    )[0]

    assert permissive.pm25 is not None
    assert strict_distance.pm25 is not None  # only the near sensor qualifies now
    assert strict_distance.pm25 == pytest.approx(10.0)
    assert strict_count.pm25 is None  # only 2 sensors found, 3 required
