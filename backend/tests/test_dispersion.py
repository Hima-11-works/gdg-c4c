"""Deterministic tests for app.services.dispersion.DeterministicH3DispersionModel,
using synthetic pollution/weather layouts on a real H3 grid (no database,
no randomness).
"""

from __future__ import annotations

from datetime import UTC, datetime

import h3
import pytest

from app.domain.types import Coordinate, GridState, WeatherReading
from app.services.dispersion import DeterministicH3DispersionModel

GENERATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
RESOLUTION = 9
CENTER = h3.latlng_to_cell(37.7749, -122.4194, RESOLUTION)
RING = [cell for cell in h3.grid_disk(CENTER, 1) if cell != CENTER]

_DEFAULTS = dict(
    decay_rate_per_hour=0.15,
    wet_removal_rate_per_hour=0.25,
    precipitation_reference_mm=4.0,
    max_transport_fraction=0.6,
    wind_transport_reference_ms=8.0,
    calm_wind_threshold_ms=0.5,
    wind_cone_half_angle_deg=50.0,
    confidence_decay_per_hour=0.9,
    missing_weather_confidence_penalty=0.5,
)


def _model(**overrides) -> DeterministicH3DispersionModel:
    kwargs = {**_DEFAULTS, **overrides}
    return DeterministicH3DispersionModel(**kwargs)


def _state(cell: str, pm25: float | None, confidence: float = 1.0) -> GridState:
    return GridState(h3_cell=cell, timestamp=GENERATED_AT, confidence=confidence, pm25=pm25)


def _weather(
    cell: str, *, wind_speed: float, wind_direction: float, precipitation: float = 0.0
) -> WeatherReading:
    lat, lon = h3.cell_to_latlng(cell)
    return WeatherReading(
        h3_cell=cell,
        latitude=lat,
        longitude=lon,
        wind_speed=wind_speed,
        wind_direction=wind_direction,
        precipitation=precipitation,
        measured_at=GENERATED_AT,
    )


def _bearing(source: str, target: str) -> float:
    source_lat, source_lon = h3.cell_to_latlng(source)
    target_lat, target_lon = h3.cell_to_latlng(target)
    return Coordinate(source_lat, source_lon).bearing_to(Coordinate(target_lat, target_lon))


def _angular_diff(bearing_a: float, bearing_b: float) -> float:
    return abs((bearing_a - bearing_b + 180.0) % 360.0 - 180.0)


def _downwind_bearing_from(source: str, target: str) -> float:
    """The wind_direction (meteorological, 'blowing from') that pushes
    mass from `source` straight toward `target`."""
    return (_bearing(source, target) + 180.0) % 360.0


def _by_cell(result, hours: int) -> dict[str, float]:
    return {f.h3_cell: f.predicted_pm25 for f in result.forecasts if f.forecast_hours == hours}


def _confidence_by_cell(result, hours: int) -> dict[str, float]:
    return {f.h3_cell: f.confidence for f in result.forecasts if f.forecast_hours == hours}


# --- construction / validation ---


@pytest.mark.parametrize(
    "override",
    [
        {"decay_rate_per_hour": -0.1},
        {"decay_rate_per_hour": 1.1},
        {"wet_removal_rate_per_hour": -0.1},
        {"wet_removal_rate_per_hour": 1.1},
        {"precipitation_reference_mm": 0},
        {"max_transport_fraction": 0},
        {"max_transport_fraction": 1},
        {"max_transport_fraction": 1.1},
        {"wind_transport_reference_ms": 0},
        {"calm_wind_threshold_ms": -1},
        {"wind_cone_half_angle_deg": 0},
        {"wind_cone_half_angle_deg": 181},
        {"confidence_decay_per_hour": 0},
        {"confidence_decay_per_hour": 1.1},
        {"missing_weather_confidence_penalty": 0},
        {"missing_weather_confidence_penalty": 1.1},
    ],
)
def test_rejects_invalid_configuration(override: dict) -> None:
    with pytest.raises(ValueError):
        _model(**override)


def test_empty_current_state_returns_empty_result() -> None:
    result = _model().forecast([], [], hours=(1, 3, 6), generated_at=GENERATED_AT)
    assert result.forecasts == []
    assert result.domain_outflow_by_hour == {}


def test_duplicate_cells_in_current_state_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _model().forecast(
            [_state(CENTER, 10.0), _state(CENTER, 20.0)], [], hours=(1,), generated_at=GENERATED_AT
        )


@pytest.mark.parametrize("hours", [(), (0,), (-1,), (1, 0)])
def test_non_positive_hours_are_rejected(hours: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="hours"):
        _model().forecast([_state(CENTER, 10.0)], [], hours=hours, generated_at=GENERATED_AT)


# --- no wind ---


def test_no_wind_isolated_cell_decays_geometrically() -> None:
    result = _model().forecast(
        [_state(CENTER, 100.0)], [], hours=(1, 3, 6), generated_at=GENERATED_AT
    )
    retention = 1.0 - _DEFAULTS["decay_rate_per_hour"]
    for hours in (1, 3, 6):
        predicted = _by_cell(result, hours)[CENTER]
        assert predicted == pytest.approx(100.0 * retention**hours)


def test_calm_wind_below_threshold_behaves_like_no_wind() -> None:
    weather = [_weather(CENTER, wind_speed=0.1, wind_direction=90.0)]
    result = _model().forecast(
        [_state(CENTER, 100.0)], weather, hours=(1,), generated_at=GENERATED_AT
    )
    assert _by_cell(result, 1)[CENTER] == pytest.approx(85.0)


def test_no_wind_produces_no_domain_outflow() -> None:
    result = _model().forecast(
        [_state(CENTER, 100.0)], [], hours=(1, 3, 6), generated_at=GENERATED_AT
    )
    assert all(loss == 0.0 for loss in result.domain_outflow_by_hour.values())


# --- strong eastward wind ---


def test_strong_wind_biases_transport_toward_the_downwind_neighbors() -> None:
    # wind_direction=270 (blowing FROM the west) pushes mass eastward
    # (downwind bearing 90).
    grid = [_state(CENTER, 1000.0)] + [_state(cell, 0.0) for cell in RING]
    weather = [_weather(cell, wind_speed=10.0, wind_direction=270.0) for cell in [CENTER, *RING]]
    result = _model().forecast(grid, weather, hours=(1,), generated_at=GENERATED_AT)
    by_cell = _by_cell(result, 1)

    ranked = sorted(RING, key=lambda cell: _angular_diff(_bearing(CENTER, cell), 90.0))
    most_downwind, *_, least_downwind = ranked
    assert by_cell[most_downwind] > 0
    assert by_cell[most_downwind] > by_cell[least_downwind]
    # A neighbor almost directly upwind gets nothing (outside the cone).
    assert by_cell[least_downwind] == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("wind_direction", "expect_south", "expect_north", "expect_east", "expect_west"),
    [
        (0.0, True, False, False, False),  # wind FROM the north -> blows south
        (90.0, False, False, False, True),  # wind FROM the east -> blows west
        (180.0, False, True, False, False),  # wind FROM the south -> blows north
        (270.0, False, False, True, False),  # wind FROM the west -> blows east
    ],
)
def test_cardinal_wind_directions_move_pollution_the_physically_correct_way(
    wind_direction: float,
    expect_south: bool,
    expect_north: bool,
    expect_east: bool,
    expect_west: bool,
) -> None:
    # Deliberately checks the *geometric* lat/lon of the top-recipient
    # neighbor directly (not via Coordinate.bearing_to, which the model
    # itself uses) so this can't pass merely because a bug is consistent
    # between the model and the test's own bearing math.
    center_lat, center_lon = h3.cell_to_latlng(CENTER)
    grid = [_state(CENTER, 1000.0)] + [_state(cell, 0.0) for cell in RING]
    weather = [_weather(CENTER, wind_speed=8.0, wind_direction=wind_direction)]
    result = _model(decay_rate_per_hour=0.0, wet_removal_rate_per_hour=0.0).forecast(
        grid, weather, hours=(1,), generated_at=GENERATED_AT
    )
    by_cell = _by_cell(result, 1)
    top = max(RING, key=lambda cell: by_cell[cell])
    top_lat, top_lon = h3.cell_to_latlng(top)

    if expect_south:
        assert top_lat < center_lat
    if expect_north:
        assert top_lat > center_lat
    if expect_east:
        assert top_lon > center_lon
    if expect_west:
        assert top_lon < center_lon


def test_strong_wind_transport_conserves_mass_with_full_neighbor_ring() -> None:
    grid = [_state(cell, 50.0 if cell == CENTER else 0.0) for cell in [CENTER, *RING]]
    weather = [_weather(cell, wind_speed=5.0, wind_direction=200.0) for cell in [CENTER, *RING]]
    result = _model(decay_rate_per_hour=0.0, wet_removal_rate_per_hour=0.0).forecast(
        grid, weather, hours=(1,), generated_at=GENERATED_AT
    )
    total = sum(_by_cell(result, 1).values())
    assert total == pytest.approx(50.0)
    assert result.domain_outflow_by_hour[1] == pytest.approx(0.0)


def test_extreme_wind_speed_is_capped_and_mass_does_not_explode() -> None:
    grid = [_state(cell, 50.0 if cell == CENTER else 0.0) for cell in [CENTER, *RING]]
    weather = [_weather(cell, wind_speed=500.0, wind_direction=200.0) for cell in [CENTER, *RING]]
    result = _model().forecast(grid, weather, hours=(1,), generated_at=GENERATED_AT)
    total = sum(_by_cell(result, 1).values()) + result.domain_outflow_by_hour[1]
    # Total (grid + boundary loss) must equal the post-removal mass, never more.
    assert total == pytest.approx(50.0 * (1 - _DEFAULTS["decay_rate_per_hour"]))
    assert all(value >= 0 for value in _by_cell(result, 1).values())


# --- rainfall ---


def test_rainfall_increases_removal_relative_to_dry_conditions() -> None:
    wet = _weather(CENTER, wind_speed=0.0, wind_direction=0.0, precipitation=8.0)
    dry = _weather(CENTER, wind_speed=0.0, wind_direction=0.0, precipitation=0.0)
    model = _model()
    wet_result = model.forecast(
        [_state(CENTER, 100.0)], [wet], hours=(1,), generated_at=GENERATED_AT
    )
    dry_result = model.forecast(
        [_state(CENTER, 100.0)], [dry], hours=(1,), generated_at=GENERATED_AT
    )
    assert _by_cell(wet_result, 1)[CENTER] < _by_cell(dry_result, 1)[CENTER]
    # precipitation=8mm is double the 4mm reference -> wet removal clamps at its max.
    expected = 100.0 * (1.0 - (0.15 + 0.25))
    assert _by_cell(wet_result, 1)[CENTER] == pytest.approx(expected)


def test_rainfall_does_not_change_the_directional_transport_split() -> None:
    grid = [_state(CENTER, 1000.0)] + [_state(cell, 0.0) for cell in RING]
    dry_weather = [
        _weather(cell, wind_speed=10.0, wind_direction=270.0) for cell in [CENTER, *RING]
    ]
    wet_weather = [
        _weather(cell, wind_speed=10.0, wind_direction=270.0, precipitation=6.0)
        for cell in [CENTER, *RING]
    ]
    model = _model()
    dry_result = model.forecast(grid, dry_weather, hours=(1,), generated_at=GENERATED_AT)
    wet_result = model.forecast(grid, wet_weather, hours=(1,), generated_at=GENERATED_AT)
    dry_by_cell = _by_cell(dry_result, 1)
    wet_by_cell = _by_cell(wet_result, 1)

    dry_total_transported = sum(v for k, v in dry_by_cell.items() if k != CENTER)
    wet_total_transported = sum(v for k, v in wet_by_cell.items() if k != CENTER)
    for cell in RING:
        if dry_total_transported == 0:
            continue
        dry_share = dry_by_cell[cell] / dry_total_transported
        if wet_total_transported == 0:
            continue
        wet_share = wet_by_cell[cell] / wet_total_transported
        assert dry_share == pytest.approx(wet_share, abs=1e-9)


# --- single pollution hotspot ---


def test_single_hotspot_ramps_up_a_downwind_neighbor_over_the_horizon() -> None:
    # Only the source cell has wind; the receiving neighbor is calm, so it
    # simply accumulates what arrives instead of re-transporting it
    # onward — isolating "does a hotspot's plume reach and build up in a
    # downwind cell" from "how far does the whole plume travel".
    downwind = RING[0]
    wind_direction = _downwind_bearing_from(CENTER, downwind)
    grid = [_state(CENTER, 200.0)] + [_state(cell, 0.0) for cell in RING]
    weather = [_weather(CENTER, wind_speed=6.0, wind_direction=wind_direction)]
    result = _model().forecast(grid, weather, hours=(1, 3, 6), generated_at=GENERATED_AT)

    values = [_by_cell(result, hours)[downwind] for hours in (1, 3, 6)]
    assert values[0] > 0
    assert values[1] > values[0]
    assert all(value >= 0 for value in values)


# --- multiple pollution hotspots ---


def test_multiple_hotspots_contribute_additively_to_a_shared_downwind_cell() -> None:
    source_a, source_b = RING[0], RING[1]
    wind_a = _downwind_bearing_from(source_a, CENTER)
    wind_b = _downwind_bearing_from(source_b, CENTER)
    model = _model()

    combined_grid = [_state(CENTER, 0.0), _state(source_a, 500.0), _state(source_b, 500.0)] + [
        _state(cell, 0.0) for cell in RING if cell not in (source_a, source_b)
    ]
    combined_weather = [
        _weather(source_a, wind_speed=10.0, wind_direction=wind_a),
        _weather(source_b, wind_speed=10.0, wind_direction=wind_b),
        _weather(CENTER, wind_speed=0.0, wind_direction=0.0),
    ]
    combined = model.forecast(
        combined_grid, combined_weather, hours=(1,), generated_at=GENERATED_AT
    )
    center_combined = _by_cell(combined, 1)[CENTER]

    def _alone(source: str, wind_direction: float) -> float:
        grid = [_state(CENTER, 0.0), _state(source, 500.0)] + [
            _state(cell, 0.0) for cell in RING if cell != source
        ]
        weather = [_weather(source, wind_speed=10.0, wind_direction=wind_direction)]
        result = model.forecast(grid, weather, hours=(1,), generated_at=GENERATED_AT)
        return _by_cell(result, 1)[CENTER]

    center_alone_sum = _alone(source_a, wind_a) + _alone(source_b, wind_b)
    assert center_combined == pytest.approx(center_alone_sum)
    assert center_combined > 0


def test_multiple_hotspots_far_apart_decay_independently_under_calm_wind() -> None:
    far_cell = h3.latlng_to_cell(37.6, -122.6, RESOLUTION)  # nowhere near CENTER
    grid = [_state(CENTER, 100.0), _state(far_cell, 40.0)]
    result = _model().forecast(grid, [], hours=(1, 3), generated_at=GENERATED_AT)
    retention = 1.0 - _DEFAULTS["decay_rate_per_hour"]
    for hours in (1, 3):
        by_cell = _by_cell(result, hours)
        assert by_cell[CENTER] == pytest.approx(100.0 * retention**hours)
        assert by_cell[far_cell] == pytest.approx(40.0 * retention**hours)


# --- boundary cells ---


def test_boundary_cell_loses_transported_mass_to_untracked_neighbors() -> None:
    # Domain contains only CENTER: every neighbor it would transport into
    # is outside the modeled grid.
    weather = [_weather(CENTER, wind_speed=10.0, wind_direction=270.0)]
    result = _model().forecast(
        [_state(CENTER, 100.0)], weather, hours=(1,), generated_at=GENERATED_AT
    )

    remaining = 100.0 * (1.0 - _DEFAULTS["decay_rate_per_hour"])
    expected_retained = remaining * (1.0 - _DEFAULTS["max_transport_fraction"])
    expected_outflow = remaining * _DEFAULTS["max_transport_fraction"]
    assert _by_cell(result, 1)[CENTER] == pytest.approx(expected_retained)
    assert result.domain_outflow_by_hour[1] == pytest.approx(expected_outflow)


def test_partial_domain_outflow_plus_retained_equals_post_removal_mass() -> None:
    # Only CENTER and one neighbor are tracked; the wind points at a
    # *different*, untracked neighbor, so some mass legitimately leaves.
    tracked_neighbor = RING[0]
    untracked_target = RING[1]
    wind_direction = _downwind_bearing_from(CENTER, untracked_target)
    grid = [_state(CENTER, 80.0), _state(tracked_neighbor, 0.0)]
    weather = [_weather(CENTER, wind_speed=6.0, wind_direction=wind_direction)]
    result = _model().forecast(grid, weather, hours=(1,), generated_at=GENERATED_AT)

    remaining = 80.0 * (1.0 - _DEFAULTS["decay_rate_per_hour"])
    total_after = sum(_by_cell(result, 1).values()) + result.domain_outflow_by_hour[1]
    assert total_after == pytest.approx(remaining)


# --- mass conservation / never explodes ---


def test_total_grid_mass_never_increases_hour_over_hour() -> None:
    grid = [_state(cell, 30.0 if cell == CENTER else 5.0) for cell in [CENTER, *RING]]
    weather = [
        _weather(cell, wind_speed=7.0, wind_direction=225.0, precipitation=2.0)
        for cell in [CENTER, *RING]
    ]
    result = _model().forecast(grid, weather, hours=(1, 2, 3, 4, 5, 6), generated_at=GENERATED_AT)

    totals = {hours: sum(_by_cell(result, hours).values()) for hours in range(1, 7)}
    previous_total = sum(30.0 if cell == CENTER else 5.0 for cell in [CENTER, *RING])
    for hours in range(1, 7):
        assert totals[hours] <= previous_total + 1e-9
        previous_total = totals[hours]


def test_predicted_pm25_is_never_negative_across_a_stress_scenario() -> None:
    grid = [_state(cell, 0.0) for cell in [CENTER, *RING]]
    weather = [
        _weather(cell, wind_speed=50.0, wind_direction=45.0, precipitation=20.0)
        for cell in [CENTER, *RING]
    ]
    result = _model().forecast(grid, weather, hours=(1, 3, 6), generated_at=GENERATED_AT)
    assert all(f.predicted_pm25 >= 0 for f in result.forecasts)


# --- missing data ---


def test_missing_weather_for_a_cell_is_decay_only_with_confidence_penalty() -> None:
    result = _model().forecast([_state(CENTER, 100.0)], [], hours=(1,), generated_at=GENERATED_AT)
    assert _by_cell(result, 1)[CENTER] == pytest.approx(85.0)
    expected_confidence = (
        1.0
        * _DEFAULTS["confidence_decay_per_hour"]
        * (_DEFAULTS["missing_weather_confidence_penalty"])
    )
    assert _confidence_by_cell(result, 1)[CENTER] == pytest.approx(expected_confidence)


def test_none_pm25_seed_cell_can_receive_nonzero_inflow() -> None:
    downwind = RING[0]
    wind_direction = _downwind_bearing_from(CENTER, downwind)
    grid = [_state(CENTER, 200.0, confidence=1.0), _state(downwind, None, confidence=0.0)] + [
        _state(cell, 0.0) for cell in RING if cell != downwind
    ]
    weather = [
        _weather(cell, wind_speed=6.0, wind_direction=wind_direction) for cell in [CENTER, *RING]
    ]
    result = _model().forecast(grid, weather, hours=(1,), generated_at=GENERATED_AT)
    assert _by_cell(result, 1)[downwind] > 0
    assert 0.0 < _confidence_by_cell(result, 1)[downwind] <= 1.0


def test_cell_with_no_evidence_at_all_gets_no_forecast_rather_than_zero() -> None:
    """A null-PM2.5, zero-confidence cell that nothing flows into must not be
    published as predicted_pm25=0.0 — that would render as "Good" air
    where the honest answer is "no estimate"."""
    no_evidence = RING[0]
    grid = [_state(CENTER, 80.0, confidence=1.0), _state(no_evidence, None, confidence=0.0)]
    calm = [_weather(cell, wind_speed=0.0, wind_direction=0.0) for cell in (CENTER, no_evidence)]

    result = _model().forecast(grid, calm, hours=(1, 3, 6), generated_at=GENERATED_AT)

    for hours in (1, 3, 6):
        assert no_evidence not in _by_cell(result, hours)
        assert _by_cell(result, hours)[CENTER] > 0


# --- confidence ---


def test_confidence_decays_purely_with_horizon_when_isolated() -> None:
    weather = [_weather(CENTER, wind_speed=0.0, wind_direction=0.0)]
    result = _model().forecast(
        [_state(CENTER, 100.0, confidence=1.0)], weather, hours=(1, 3, 6), generated_at=GENERATED_AT
    )
    decay = _DEFAULTS["confidence_decay_per_hour"]
    for hours in (1, 3, 6):
        assert _confidence_by_cell(result, hours)[CENTER] == pytest.approx(decay**hours)


def test_confidence_strictly_decreases_with_longer_horizons() -> None:
    weather = [_weather(CENTER, wind_speed=0.0, wind_direction=0.0)]
    result = _model().forecast(
        [_state(CENTER, 100.0, confidence=1.0)], weather, hours=(1, 3, 6), generated_at=GENERATED_AT
    )
    c1 = _confidence_by_cell(result, 1)[CENTER]
    c3 = _confidence_by_cell(result, 3)[CENTER]
    c6 = _confidence_by_cell(result, 6)[CENTER]
    assert c1 > c3 > c6 > 0


def test_receiving_cell_confidence_is_a_weighted_average_not_an_overwrite() -> None:
    downwind = RING[0]
    wind_direction = _downwind_bearing_from(CENTER, downwind)
    # The receiving cell starts at pm25=0, so all of its post-transport
    # mass (and therefore confidence) comes from the source cell. It gets
    # its own (calm) weather reading too, so the missing-weather penalty
    # doesn't confound the blending arithmetic being tested here.
    grid = [_state(CENTER, 100.0, confidence=0.9), _state(downwind, 0.0, confidence=0.1)]
    weather = [
        _weather(CENTER, wind_speed=6.0, wind_direction=wind_direction),
        _weather(downwind, wind_speed=0.0, wind_direction=0.0),
    ]
    result = _model().forecast(grid, weather, hours=(1,), generated_at=GENERATED_AT)

    decay = _DEFAULTS["confidence_decay_per_hour"]
    assert _confidence_by_cell(result, 1)[downwind] == pytest.approx(0.9 * decay)


# --- consistency across requested horizons ---


def test_multi_horizon_result_matches_the_stepwise_simulation() -> None:
    grid = [_state(cell, 40.0 if cell == CENTER else 0.0) for cell in [CENTER, *RING]]
    weather = [_weather(cell, wind_speed=4.0, wind_direction=310.0) for cell in [CENTER, *RING]]
    model = _model()

    full_run = model.forecast(grid, weather, hours=(1, 3, 6), generated_at=GENERATED_AT)
    six_hour_only = model.forecast(grid, weather, hours=(6,), generated_at=GENERATED_AT)

    assert _by_cell(full_run, 6) == pytest.approx(_by_cell(six_hour_only, 6))
    assert _confidence_by_cell(full_run, 6) == pytest.approx(_confidence_by_cell(six_hour_only, 6))


# --- determinism / configuration sensitivity ---


def test_forecast_is_deterministic() -> None:
    grid = [_state(cell, 40.0 if cell == CENTER else 5.0) for cell in [CENTER, *RING]]
    weather = [_weather(cell, wind_speed=4.0, wind_direction=100.0) for cell in [CENTER, *RING]]
    model = _model()
    first = model.forecast(grid, weather, hours=(1, 3, 6), generated_at=GENERATED_AT)
    second = model.forecast(grid, weather, hours=(1, 3, 6), generated_at=GENERATED_AT)
    assert first == second


@pytest.mark.parametrize(
    "override",
    [
        {"decay_rate_per_hour": 0.5},
        {"wet_removal_rate_per_hour": 0.9},
        {"max_transport_fraction": 0.1},
        {"wind_cone_half_angle_deg": 10.0},
        {"confidence_decay_per_hour": 0.2},
        {"missing_weather_confidence_penalty": 0.9},
    ],
)
def test_each_configured_parameter_changes_the_result(override: dict) -> None:
    # Only CENTER has a weather reading (wind + precipitation): the ring
    # cells are left without one, so missing_weather_confidence_penalty
    # has something to act on too, alongside every other parameter.
    grid = [_state(CENTER, 100.0)] + [_state(cell, 0.0) for cell in RING]
    weather = [_weather(CENTER, wind_speed=6.0, wind_direction=270.0, precipitation=2.0)]
    baseline = _model().forecast(grid, weather, hours=(3,), generated_at=GENERATED_AT)
    changed = _model(**override).forecast(grid, weather, hours=(3,), generated_at=GENERATED_AT)

    def _snapshot(result) -> dict[str, tuple[float, float]]:
        return {f.h3_cell: (f.predicted_pm25, f.confidence) for f in result.forecasts}

    assert _snapshot(baseline) != _snapshot(changed)
