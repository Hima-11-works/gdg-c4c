"""Tests for Demo Mode: the two substituted providers
(app.ingestion.demo), the factory that chooses them (app.ingestion.factory),
and — the real point of Demo Mode — an end-to-end run proving the actual
pipeline logic (IDW, PDI, dispersion, alerts) produces a visible hotspot,
visible downwind movement over 1h/3h/6h, and at least one alert from this
data, with no demo-specific branching anywhere past ingestion.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import h3
import httpx
import pytest

from app.core.config import Settings
from app.domain.types import BoundingBox, Coordinate, WeatherReading
from app.ingestion.demo import DemoPollutionDataProvider, DemoWeatherProvider
from app.ingestion.factory import build_pollution_provider, build_weather_provider
from app.ingestion.open_meteo import OpenMeteoProvider
from app.ingestion.openaq import OpenAQProvider
from app.services.alert_generation import AlertGenerationService
from app.services.dispersion import DeterministicH3DispersionModel
from app.services.estimation import IDWPollutionEstimator
from app.services.geospatial import GeospatialService
from app.services.grid_computation import GridComputationService
from app.services.pdi import HeuristicPDIModel
from tests.fakes import FakeAlertRepository, FakeGridStateRepository, FakeSensorReadingRepository

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BBOX = BoundingBox(min_lat=28.40, min_lon=76.80, max_lat=28.90, max_lon=77.50)


def _settings(**overrides) -> Settings:
    defaults = dict(postgres_user="u", postgres_password="p", postgres_db="d")
    defaults.update(overrides)
    return Settings(**defaults)


def _angular_diff(bearing_a: float, bearing_b: float) -> float:
    return abs((bearing_a - bearing_b + 180.0) % 360.0 - 180.0)


def _nearest_neighbor_to_bearing(origin: str, target_bearing: float) -> str:
    origin_center = Coordinate(*h3.cell_to_latlng(origin))
    neighbors = [cell for cell in h3.grid_disk(origin, 1) if cell != origin]
    return min(
        neighbors,
        key=lambda cell: _angular_diff(
            origin_center.bearing_to(Coordinate(*h3.cell_to_latlng(cell))), target_bearing
        ),
    )


# --- DemoPollutionDataProvider ---


async def test_demo_pollution_provider_returns_a_hotspot_and_background_readings() -> None:
    provider = DemoPollutionDataProvider()
    readings = await provider.fetch_readings(BBOX, since=NOW - timedelta(hours=3))

    assert len(readings) == 5
    values = sorted(r.value for r in readings)
    assert values[-1] == 280.0  # the hotspot, unmistakably above every other reading
    assert all(v < 30.0 for v in values[:-1])  # background readings stay low
    assert {r.pollutant for r in readings} == {"pm25"}
    assert {r.source for r in readings} == {"demo"}


async def test_demo_pollution_provider_is_deterministic_in_content() -> None:
    provider = DemoPollutionDataProvider()
    first = await provider.fetch_readings(BBOX, since=NOW)
    second = await provider.fetch_readings(BBOX, since=NOW)

    # Same coordinates/values every call — only the timestamp moves (see
    # the next test), which is why this compares everything else.
    first_shape = [(r.external_sensor_id, r.latitude, r.longitude, r.value) for r in first]
    second_shape = [(r.external_sensor_id, r.latitude, r.longitude, r.value) for r in second]
    assert first_shape == second_shape


async def test_demo_pollution_provider_ignores_bbox_and_stamps_current_time() -> None:
    """Demo Mode always shows the same scenario regardless of the
    configured region, and its readings must never look stale — both
    are checked directly rather than assumed."""
    tiny_far_away_bbox = BoundingBox(min_lat=1.0, min_lon=1.0, max_lat=1.1, max_lon=1.1)
    before = datetime.now(UTC)
    readings = await DemoPollutionDataProvider().fetch_readings(tiny_far_away_bbox, since=NOW)
    after = datetime.now(UTC)

    assert len(readings) == 5
    assert all(before <= r.measured_at <= after for r in readings)


# --- DemoWeatherProvider ---


async def test_demo_weather_provider_returns_one_sample_per_point() -> None:
    points = [Coordinate(37.7, -122.4), Coordinate(37.8, -122.3), Coordinate(37.6, -122.5)]
    samples = await DemoWeatherProvider().fetch_weather(points)

    assert len(samples) == len(points)
    assert all(sample is not None for sample in samples)
    speeds = {sample.wind_speed for sample in samples}
    directions = {sample.wind_direction for sample in samples}
    assert speeds == {6.0}
    assert directions == {270.0}  # a steady westerly, shared by the whole region


async def test_demo_weather_provider_wind_speed_is_clearly_above_calm() -> None:
    [sample] = await DemoWeatherProvider().fetch_weather([Coordinate(37.7, -122.4)])
    settings = _settings()
    assert sample.wind_speed > settings.dispersion_calm_wind_threshold_ms * 2


# --- factory: the one place demo vs. live is decided ---


async def test_factory_selects_demo_pollution_provider_when_demo_mode_is_on() -> None:
    settings = _settings(demo_mode=True, openaq_api_key=None)
    async with httpx.AsyncClient() as client:
        provider = build_pollution_provider(settings, client)
    assert isinstance(provider, DemoPollutionDataProvider)


async def test_factory_selects_demo_weather_provider_when_demo_mode_is_on() -> None:
    settings = _settings(demo_mode=True)
    async with httpx.AsyncClient() as client:
        provider = build_weather_provider(settings, client)
    assert isinstance(provider, DemoWeatherProvider)


async def test_factory_selects_real_providers_when_demo_mode_is_off() -> None:
    settings = _settings(demo_mode=False, openaq_api_key="a-real-key")
    async with httpx.AsyncClient() as client:
        pollution = build_pollution_provider(settings, client)
        weather = build_weather_provider(settings, client)
    assert isinstance(pollution, OpenAQProvider)
    assert isinstance(weather, OpenMeteoProvider)


async def test_factory_returns_none_for_pollution_in_live_mode_without_an_api_key() -> None:
    settings = _settings(demo_mode=False, openaq_api_key=None)
    async with httpx.AsyncClient() as client:
        provider = build_pollution_provider(settings, client)
    assert provider is None


async def test_factory_demo_mode_needs_no_api_key() -> None:
    settings = _settings(demo_mode=True, openaq_api_key=None)
    async with httpx.AsyncClient() as client:
        provider = build_pollution_provider(settings, client)
    assert provider is not None


# --- end to end: the real pipeline, fed by Demo Mode's dataset ---


async def test_demo_mode_end_to_end_produces_a_hotspot_downwind_movement_and_an_alert() -> None:
    """The actual point of Demo Mode: run the REAL GridComputationService,
    DeterministicH3DispersionModel, and AlertGenerationService — no
    demo-specific branching anywhere in this test past the two providers
    — against Demo Mode's fixed dataset, and confirm every requirement:
    a visible hotspot, forecasts that visibly carry it downwind (east,
    since the demo wind blows from the west) over 1h/3h/6h, and at least
    one generated alert.
    """
    settings = _settings(demo_mode=True)
    resolution = settings.h3_resolution
    hotspot_cell = h3.latlng_to_cell(28.6139, 77.2090, resolution)
    downwind_cell = _nearest_neighbor_to_bearing(hotspot_cell, target_bearing=90.0)  # east
    upwind_cell = _nearest_neighbor_to_bearing(hotspot_cell, target_bearing=270.0)  # west

    # --- ingestion: the only demo-mode-aware step in this whole test ---
    sensor_repo = FakeSensorReadingRepository()
    pollution_provider = DemoPollutionDataProvider()
    for reading in await pollution_provider.fetch_readings(BBOX, since=NOW - timedelta(hours=3)):
        sensor_repo.add(reading)

    # --- grid computation: the real estimator + PDI, unaware of demo mode ---
    grid_repo = FakeGridStateRepository()
    geospatial = GeospatialService(resolution=resolution)
    grid_service = GridComputationService(
        IDWPollutionEstimator(
            max_distance_km=settings.idw_max_distance_km, min_sensors=settings.idw_min_sensors
        ),
        HeuristicPDIModel(
            pm25_reference=settings.pdi_pm25_reference_ugm3,
            pm25_weight=settings.pdi_pm25_weight,
            road_pressure_weight=settings.pdi_road_pressure_weight,
            industrial_pressure_weight=settings.pdi_industrial_pressure_weight,
        ),
        geospatial,
        sensor_repo,
        grid_repo,
    )
    grid_result = grid_service.run(
        BBOX, timestamp=NOW, sensor_max_age=timedelta(hours=settings.ingest_max_reading_age_hours)
    )
    assert grid_result.succeeded is True

    hotspot_state = grid_repo.get(hotspot_cell, NOW)
    assert hotspot_state is not None
    assert hotspot_state.pm25 == pytest.approx(280.0, abs=5.0)  # the hotspot is clearly visible

    # --- forecasting: the real dispersion model, unaware of demo mode ---
    weather_provider = DemoWeatherProvider()
    cells = [state.h3_cell for state in grid_result.states]
    points = [Coordinate(*h3.cell_to_latlng(cell)) for cell in cells]
    weather_samples = await weather_provider.fetch_weather(points)
    weather_readings = [
        WeatherReading(
            h3_cell=cell,
            latitude=point.latitude,
            longitude=point.longitude,
            wind_speed=sample.wind_speed,
            wind_direction=sample.wind_direction,
            precipitation=sample.precipitation,
            boundary_layer_height=sample.boundary_layer_height,
            measured_at=NOW,
        )
        for cell, point, sample in zip(cells, points, weather_samples, strict=True)
    ]
    dispersion_model = DeterministicH3DispersionModel(
        decay_rate_per_hour=settings.dispersion_decay_rate_per_hour,
        wet_removal_rate_per_hour=settings.dispersion_wet_removal_rate_per_hour,
        precipitation_reference_mm=settings.dispersion_precipitation_reference_mm,
        max_transport_fraction=settings.dispersion_max_transport_fraction,
        wind_transport_reference_ms=settings.dispersion_wind_transport_reference_ms,
        calm_wind_threshold_ms=settings.dispersion_calm_wind_threshold_ms,
        wind_cone_half_angle_deg=settings.dispersion_wind_cone_half_angle_deg,
        confidence_decay_per_hour=settings.dispersion_confidence_decay_per_hour,
        missing_weather_confidence_penalty=settings.dispersion_missing_weather_confidence_penalty,
    )
    forecast_result = dispersion_model.forecast(
        grid_result.states, weather_readings, hours=(1, 3, 6), generated_at=NOW
    )
    predicted = {(f.h3_cell, f.forecast_hours): f.predicted_pm25 for f in forecast_result.forecasts}

    # At H3 resolution, IDW already gives every immediate neighbor of the
    # hotspot a value close to the hotspot itself (interpolation, not
    # transport) — so "upwind ends up near zero" isn't a realistic bar.
    # The thing Demo Mode actually needs to show is the wind's directional
    # signature on top of that shared baseline: the downwind neighbor
    # consistently carries more than the upwind one, at every horizon.
    downwind_values = [predicted[(downwind_cell, hours)] for hours in (1, 3, 6)]
    upwind_values = [predicted[(upwind_cell, hours)] for hours in (1, 3, 6)]
    assert all(value > 0 for value in downwind_values), (
        "the hotspot's plume never visibly reaches the downwind neighbor"
    )
    assert all(d > u for d, u in zip(downwind_values, upwind_values, strict=True)), (
        "wind transport isn't visibly carrying pollution downwind more than upwind"
    )

    # --- alerts: the real threshold rules, unaware of demo mode ---
    alert_repo = FakeAlertRepository()
    alert_service = AlertGenerationService(
        alert_repo,
        warning_threshold_ugm3=settings.alert_warning_threshold_ugm3,
        critical_threshold_ugm3=settings.alert_critical_threshold_ugm3,
        sharp_increase_threshold_ugm3=settings.alert_sharp_increase_threshold_ugm3,
        pdi_high_threshold=settings.alert_pdi_high_threshold,
        pdi_worsening_min_increase_ugm3=settings.alert_pdi_worsening_min_increase_ugm3,
        active_lookback=timedelta(hours=settings.alert_active_lookback_hours),
    )
    alert_result = alert_service.run(
        grid_result.states, forecast_result.forecasts, generated_at=NOW
    )

    assert alert_result.alerts_created >= 1
    assert any(alert.h3_cell == hotspot_cell for alert in alert_repo.alerts)
    hotspot_alert = next(alert for alert in alert_repo.alerts if alert.h3_cell == hotspot_cell)
    assert hotspot_alert.severity.value == "critical"
