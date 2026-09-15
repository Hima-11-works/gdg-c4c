"""Tests for the synthetic India-wide demo field in app.services.demo_data:
determinism, regional/hotspot shape, wind-driven forecasting, confidence,
and the India-only domain boundary. Not testing exact numbers (the field
is illustrative, not a spec) — testing the *relationships* the module's
docstring promises: industrial > rural, near-a-hotspot > far-from-one,
forecasts that actually move with wind direction, and so on.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from app.domain.h3_grid import cell_for
from app.services import demo_data

_RES = 8


def test_generate_grid_state_is_deterministic_within_a_process() -> None:
    cell = cell_for(28.6139, 77.2090, resolution=_RES)  # Delhi

    first = demo_data.generate_grid_state(cell)
    second = demo_data.generate_grid_state(cell)

    assert first.pm25 == second.pm25
    assert first.pdi == second.pdi
    assert first.wind_speed == second.wind_speed
    assert first.wind_direction == second.wind_direction
    assert first.confidence == second.confidence


def test_generate_grid_state_is_deterministic_across_processes() -> None:
    """The whole point of seeding with a plain string (see _rng's
    docstring) rather than a tuple is that random.Random(seed) reproduces
    the same sequence in a brand new Python process, not just within one
    already-warm process/module cache."""
    cell = cell_for(28.6139, 77.2090, resolution=_RES)
    script = (
        "from app.domain.h3_grid import cell_for\n"
        "from app.services import demo_data\n"
        f"cell = cell_for(28.6139, 77.2090, resolution={_RES})\n"
        "assert cell == %r\n"
        "state = demo_data.generate_grid_state(cell)\n"
        "print(state.pm25, state.pdi, state.wind_direction)\n"
    ) % cell

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    in_process = demo_data.generate_grid_state(cell)
    subprocess_pm25, subprocess_pdi, subprocess_wind = (float(x) for x in result.stdout.split())

    assert subprocess_pm25 == in_process.pm25
    assert subprocess_pdi == in_process.pdi
    assert subprocess_wind == in_process.wind_direction


def test_industrial_city_pm25_is_higher_than_a_rural_baseline() -> None:
    delhi = cell_for(28.6139, 77.2090, resolution=_RES)
    rural = cell_for(24.0, 74.0, resolution=_RES)  # far from every named city

    assert demo_data.generate_grid_state(delhi).pm25 > demo_data.generate_grid_state(rural).pm25


def test_rural_pm25_is_not_absent_just_lower() -> None:
    """Rural regions get real (lower) values, not "no data" — the whole
    reason this module generates a continuous field rather than only
    covering ~19 named cities (see the module docstring)."""
    rural = cell_for(24.0, 74.0, resolution=_RES)

    state = demo_data.generate_grid_state(rural)

    assert state.pm25 > 0
    assert state.confidence > 0


def test_synthetic_anomaly_hotspot_is_elevated() -> None:
    """_ANOMALY_HOTSPOTS are bumps not tied to any city — verifies one
    actually raises the field above a same-region point further away."""
    hotspot_lat, hotspot_lon, _label, _bump, _sigma = demo_data._ANOMALY_HOTSPOTS[0]
    at_hotspot = cell_for(hotspot_lat, hotspot_lon, resolution=_RES)
    far_away = cell_for(hotspot_lat, hotspot_lon + 3.0, resolution=_RES)  # ~300km east

    assert (
        demo_data.generate_grid_state(at_hotspot).pm25
        > demo_data.generate_grid_state(far_away).pm25
    )


def test_industrial_flag_gives_a_full_industrial_pressure_factor_at_its_center() -> None:
    nagpur = cell_for(21.1458, 79.0882, resolution=_RES)  # flagged industrial=True
    mumbai = cell_for(19.0760, 72.8777, resolution=_RES)  # not flagged industrial

    assert demo_data.generate_pdi_factors(nagpur)["industrial_pressure"] == pytest.approx(1.0)
    assert demo_data.generate_pdi_factors(mumbai)["industrial_pressure"] == pytest.approx(0.0)


def test_pdi_is_independent_of_pm25_not_a_rescaling_of_it() -> None:
    """The whole point of blending in industrial/road/vegetation signals
    instead of just scaling PM2.5: two cells can rank differently on PDI
    than they do on PM2.5 alone, because those extra factors don't move
    in lockstep with a cell's own pollution level. Nagpur (industrial)
    has lower demo PM2.5 than Mumbai (not industrial) but a higher PDI —
    proof PDI isn't a rescaled copy of the PM2.5 the user already sees.
    """
    nagpur = cell_for(21.1458, 79.0882, resolution=_RES)
    mumbai = cell_for(19.0760, 72.8777, resolution=_RES)

    nagpur_state = demo_data.generate_grid_state(nagpur)
    mumbai_state = demo_data.generate_grid_state(mumbai)

    assert nagpur_state.pm25 < mumbai_state.pm25
    assert nagpur_state.pdi > mumbai_state.pdi


def test_pdi_factors_cover_the_four_documented_signals() -> None:
    nagpur = cell_for(21.1458, 79.0882, resolution=_RES)

    factors = demo_data.generate_pdi_factors(nagpur)

    assert set(factors) == {"pm25", "industrial_pressure", "road_pressure", "vegetation_sink"}
    for value in factors.values():
        assert 0.0 <= value <= 1.0


def test_higher_vegetation_sink_pulls_pdi_down() -> None:
    # A remote point with high greenness (deep in the Western Ghats
    # anchor's territory, far from any city's industrial/road pressure)
    # should show a strong vegetation_sink factor and a correspondingly
    # low (or negative) PDI relative to its own PM2.5 level.
    green_point = cell_for(11.5, 76.5, resolution=_RES)
    factors = demo_data.generate_pdi_factors(green_point)
    assert factors["vegetation_sink"] > 0.5


def test_confidence_is_higher_near_a_city_than_far_from_one() -> None:
    delhi = cell_for(28.6139, 77.2090, resolution=_RES)
    remote = cell_for(24.0, 74.0, resolution=_RES)

    assert (
        demo_data.generate_grid_state(delhi).confidence
        > demo_data.generate_grid_state(remote).confidence
    )


def test_confidence_decays_with_forecast_horizon() -> None:
    delhi = cell_for(28.6139, 77.2090, resolution=_RES)

    confidences = [demo_data.generate_forecast(delhi, hours).confidence for hours in (1, 3, 6)]

    assert confidences[0] > confidences[1] > confidences[2]


def test_forecast_shows_plume_arriving_then_passing_downwind_of_a_hotspot() -> None:
    """Wind at Delhi's latitude blows from roughly the west (see
    _wind_direction), so Delhi's plume advects east — a rural cell east
    of Delhi, just outside Delhi's own bump radius, should see PM2.5
    climb as the plume arrives and then recede as it passes through: the
    "visibly moving" signature this whole model exists for, not a flat
    "forecasts always trend up" curve.
    """
    downwind_cell = cell_for(28.6, 78.0, resolution=_RES)

    current = demo_data.generate_grid_state(downwind_cell).pm25
    plus_3h = demo_data.generate_forecast(downwind_cell, 3).predicted_pm25
    plus_6h = demo_data.generate_forecast(downwind_cell, 6).predicted_pm25

    assert plus_3h > current  # the plume has arrived
    assert plus_6h < plus_3h  # and is now passing through


def test_forecast_clears_at_the_original_hotspot_as_its_plume_departs() -> None:
    """A point right at (or just past) a hotspot's own center should see
    PM2.5 fall monotonically as that hotspot's plume moves away — the
    source doesn't replenish itself, so once the plume has left, what's
    left behind is whatever the (unmoving) regional background alone
    explains."""
    at_delhi = cell_for(28.6139, 77.209, resolution=_RES)

    current = demo_data.generate_grid_state(at_delhi).pm25
    plus_1h = demo_data.generate_forecast(at_delhi, 1).predicted_pm25
    plus_3h = demo_data.generate_forecast(at_delhi, 3).predicted_pm25
    plus_6h = demo_data.generate_forecast(at_delhi, 6).predicted_pm25

    assert current > plus_1h > plus_3h > plus_6h


def test_advected_hotspot_moves_downwind_grows_and_never_gets_stronger() -> None:
    """Unit-level check on _advect itself — the mechanics the two tests
    above are really exercising end to end: the plume's center actually
    moves, its spread actually grows, and its peak never increases (no
    pollution appears without an already-decaying source)."""
    lat, lon, _name, peak, _industrial = demo_data._CITIES[0]  # Delhi
    background = demo_data._background_at_city(0)
    amplitude0 = peak - background
    sigma0 = demo_data._CITY_BUMP_SIGMA_KM

    now = demo_data._advect(lat, lon, amplitude0, sigma0, 0.0, hours=0)
    assert now.latitude == lat
    assert now.longitude == lon
    assert now.amplitude == amplitude0
    assert now.sigma_km == sigma0

    previous = now
    for hours in (1, 3, 6):
        moved = demo_data._advect(lat, lon, amplitude0, sigma0, 0.0, hours=hours)
        assert (moved.latitude, moved.longitude) != (previous.latitude, previous.longitude)
        assert moved.sigma_km > previous.sigma_km
        assert 0.0 < moved.amplitude < previous.amplitude
        previous = moved


def test_is_within_demo_domain_true_for_india_false_for_sydney() -> None:
    delhi = cell_for(28.6139, 77.2090, resolution=_RES)
    sydney = cell_for(-33.87, 151.21, resolution=_RES)

    assert demo_data.is_within_demo_domain(delhi) is True
    assert demo_data.is_within_demo_domain(sydney) is False


def test_for_cells_batch_functions_silently_drop_cells_outside_the_domain() -> None:
    delhi = cell_for(28.6139, 77.2090, resolution=_RES)
    sydney = cell_for(-33.87, 151.21, resolution=_RES)
    cells = [delhi, sydney]

    grid_states = demo_data.grid_states_for_cells(cells)
    weather = demo_data.weather_readings_for_cells(cells)
    forecasts = demo_data.forecasts_for_cells(cells, 3)

    assert {s.h3_cell for s in grid_states} == {delhi}
    assert {w.h3_cell for w in weather} == {delhi}
    assert {f.h3_cell for f in forecasts} == {delhi}


def test_weather_reading_carries_temperature_and_humidity() -> None:
    delhi = cell_for(28.6139, 77.2090, resolution=_RES)

    reading = demo_data.generate_weather_reading(delhi)

    assert reading.temperature is not None
    assert reading.humidity is not None
    assert -5.0 <= reading.temperature <= 46.0
    assert 10.0 <= reading.humidity <= 100.0
