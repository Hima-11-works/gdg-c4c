"""Targeted MVP resilience test plan: 15 specific failure/edge-case
scenarios a hackathon demo (or a real deployment) can actually hit,
verified end to end rather than assumed from unit coverage alone.

The rule under test throughout: a failure must be EXPLICIT and
UNDERSTANDABLE (a clear error, a null field, a non-2xx response with a
readable message) — never hidden behind fabricated "successful" data.
`is_demo` is the one deliberate, clearly-flagged exception to that rule
(see docs/architecture.md); every test below that touches it asserts the
flag is set correctly, not just that *some* response came back.

Scenario -> where it's verified:
 1. OpenAQ unavailable            -> inline below + test_ingestion_resilience.py (fuller coverage)
 2. Open-Meteo unavailable        -> inline below + test_ingestion_resilience.py (fuller coverage)
 3. No sensors returned           -> inline below (estimator + full pipeline + API honesty)
 4. Malformed sensor reading      -> inline below (domain rejection) + test_openaq_provider.py
                                     (fuller: skip-and-continue at the HTTP boundary)
 5. Missing wind data             -> inline below + test_dispersion.py (fuller coverage)
 6. Zero wind                     -> inline below + test_dispersion.py (fuller coverage)
 7. Very strong wind              -> inline below + test_dispersion.py (fuller coverage)
 8. Rainfall                      -> inline below + test_dispersion.py (fuller coverage)
 9. Database unavailable          -> inline below (API request path) + app.cli/app.pipeline.run's
                                     own top-level catch (see their source)
10. Frontend API failure          -> NOT in this file (no frontend test runner exists) — verified
                                     separately by running frontend/src/lib/api.ts's real
                                     apiGet()/ApiError logic under plain Node with a mocked fetch
                                     (network failure, HTTP error envelope, malformed error body,
                                     success); all four cases behaved correctly. See the session's
                                     report for that run's output — not committed here since it
                                     needs no ongoing regression protection beyond what tsc/build
                                     already give the types.
11. Insufficient sensor coverage  -> inline below (estimator null + API honesty)
12. Empty forecast                -> inline below + test_api_grid.py / test_api_cells.py (fuller)
13. Unusually high PM2.5          -> inline below (estimate -> PDI -> alert end to end; no crash,
                                     no silent clamp-and-hide)
14. Invalid H3 cell               -> inline below + test_api_cells.py (fuller coverage)
15. Unsupported forecast horizon  -> inline below + test_api_grid.py (fuller coverage)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import h3
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.types import PM25, BoundingBox, GridState, SensorReading, WeatherReading
from app.services.alert_generation import AlertGenerationService
from app.services.dispersion import DeterministicH3DispersionModel
from app.services.estimation import IDWPollutionEstimator
from app.services.geospatial import GeospatialService
from app.services.grid_computation import GridComputationService
from app.services.ingestion import SensorIngestionService, WeatherIngestionService
from app.services.pdi import HeuristicPDIModel
from tests.conftest import FakeRepos
from tests.fakes import (
    FakePollutionDataProvider,
    FakeSensorReadingRepository,
    FakeWeatherProvider,
    FakeWeatherReadingRepository,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BBOX = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)
# Matches app.core.config.Settings.h3_resolution's own default (8) rather
# than hardcoding a different one: several scenarios below build a cell
# by hand and then also fetch it through /api/v1/cells/{h3_cell}, whose
# validation checks it against the *configured* resolution.
RESOLUTION = get_settings().h3_resolution
CELL = h3.latlng_to_cell(37.7749, -122.4194, RESOLUTION)


def _weather(cell: str, *, wind_speed: float, wind_direction: float, precipitation: float = 0.0):
    lat, lon = h3.cell_to_latlng(cell)
    return WeatherReading(
        h3_cell=cell,
        latitude=lat,
        longitude=lon,
        wind_speed=wind_speed,
        wind_direction=wind_direction,
        precipitation=precipitation,
        measured_at=NOW,
    )


def _dispersion_model(**overrides) -> DeterministicH3DispersionModel:
    defaults = dict(
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
    defaults.update(overrides)
    return DeterministicH3DispersionModel(**defaults)


# --- 1. OpenAQ unavailable ---


async def test_scenario_01_openaq_unavailable_is_reported_not_raised() -> None:
    service = SensorIngestionService(
        FakePollutionDataProvider(error="OpenAQ: 503 after 3 attempts"),
        FakeSensorReadingRepository(),
    )
    result = await service.run(BBOX, since=NOW)

    assert result.succeeded is False
    assert result.errors  # explicit, readable — see error text below
    assert "OpenAQ" in result.errors[0]
    assert result.saved == 0  # never partial-fabricated


# --- 2. Open-Meteo unavailable ---


async def test_scenario_02_open_meteo_unavailable_is_reported_not_raised() -> None:
    service = WeatherIngestionService(
        FakeWeatherProvider(error="Open-Meteo: connection timed out"),
        FakeWeatherReadingRepository(),
    )
    result = await service.run(BBOX)

    assert result.succeeded is False
    assert result.errors
    assert "Open-Meteo" in result.errors[0]
    assert result.saved == 0


# --- 3. No sensors returned (empty, not an error) ---


def test_scenario_03_no_sensors_produces_honest_nulls_not_a_crash() -> None:
    estimator = IDWPollutionEstimator(max_distance_km=15.0, min_sensors=2)
    [state] = estimator.estimate([CELL], [], timestamp=NOW)
    assert state.pm25 is None
    assert state.confidence == 0.0


def test_scenario_03_no_sensors_through_the_full_grid_computation_service(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """The pipeline ran, found zero sensors, and honestly persisted 'no
    evidence' rows — the API must show that as real (is_demo=False) data
    with pm25=null, not silently fall back to demo data (which would
    hide the fact that a real pipeline run actually happened)."""
    geospatial = GeospatialService(resolution=RESOLUTION)
    service = GridComputationService(
        IDWPollutionEstimator(max_distance_km=15.0, min_sensors=2),
        HeuristicPDIModel(
            pm25_reference=250.0,
            pm25_weight=0.7,
            road_pressure_weight=0.2,
            industrial_pressure_weight=0.1,
        ),
        geospatial,
        fake_repos.sensor,
        fake_repos.grid,
    )
    tiny_bbox = BoundingBox(min_lat=37.77, min_lon=-122.43, max_lat=37.78, max_lon=-122.41)
    result = service.run(tiny_bbox, timestamp=NOW, sensor_max_age=timedelta(hours=3))
    assert result.succeeded is True
    assert result.sensors_used == 0
    assert result.cells_saved > 0

    saved_cell = result.states[0].h3_cell
    response = api_client.get(f"/api/v1/cells/{saved_cell}")
    body = response.json()
    assert body["is_demo"] is False
    assert body["data"]["current"]["pm25"] is None
    assert body["data"]["current"]["confidence"] == 0.0


# --- 4. Malformed sensor reading ---


def test_scenario_04_malformed_reading_is_rejected_at_construction() -> None:
    """The boundary that actually parses raw OpenAQ JSON
    (app.ingestion.openaq.OpenAQProvider) skips a malformed record and
    keeps processing the rest of the batch — see
    test_openaq_provider.py::test_fetch_readings_skips_reading_with_no_coordinates_at_all
    for that exact HTTP-level repro. What's checked here is the
    lower-level guarantee that makes that skip-logic possible at all:
    the domain type itself refuses to silently accept nonsensical data.
    """
    with pytest.raises(ValueError, match="latitude"):
        SensorReading(
            source="openaq",
            external_sensor_id="bad",
            latitude=999.0,  # out of range — a malformed upstream record
            longitude=-122.42,
            pollutant=PM25,
            value=12.0,
            unit="ug/m3",
            measured_at=NOW,
        )


# --- 5. Missing wind data ---


def test_scenario_05_missing_wind_data_is_decay_only_not_fabricated_wind() -> None:
    model = _dispersion_model()
    state = GridState(h3_cell=CELL, timestamp=NOW, confidence=1.0, pm25=100.0)
    result = model.forecast([state], [], hours=(1,), generated_at=NOW)

    [forecast] = result.forecasts
    assert forecast.predicted_pm25 == pytest.approx(85.0)  # pure decay, 15%/h default
    assert forecast.confidence < 1.0  # missing-weather penalty applied, not hidden


# --- 6. Zero wind ---


def test_scenario_06_zero_wind_behaves_like_calm_not_a_division_error() -> None:
    model = _dispersion_model()
    state = GridState(h3_cell=CELL, timestamp=NOW, confidence=1.0, pm25=100.0)
    weather = [_weather(CELL, wind_speed=0.0, wind_direction=180.0)]
    result = model.forecast([state], weather, hours=(1,), generated_at=NOW)

    assert result.forecasts[0].predicted_pm25 == pytest.approx(85.0)
    assert result.domain_outflow_by_hour[1] == 0.0


# --- 7. Very strong wind ---


def test_scenario_07_very_strong_wind_is_capped_not_unbounded() -> None:
    model = _dispersion_model()
    ring = [c for c in h3.grid_disk(CELL, 1) if c != CELL]
    grid = [GridState(h3_cell=c, timestamp=NOW, confidence=1.0, pm25=0.0) for c in ring] + [
        GridState(h3_cell=CELL, timestamp=NOW, confidence=1.0, pm25=50.0)
    ]
    weather = [_weather(c, wind_speed=500.0, wind_direction=200.0) for c in [CELL, *ring]]
    result = model.forecast(grid, weather, hours=(1,), generated_at=NOW)

    total = sum(f.predicted_pm25 for f in result.forecasts) + result.domain_outflow_by_hour[1]
    assert total == pytest.approx(50.0 * 0.85)  # mass conserved, nothing exploded
    assert all(f.predicted_pm25 >= 0 for f in result.forecasts)


# --- 8. Rainfall ---


def test_scenario_08_rainfall_increases_removal_explicitly() -> None:
    model = _dispersion_model()
    state = GridState(h3_cell=CELL, timestamp=NOW, confidence=1.0, pm25=100.0)
    dry = model.forecast(
        [state],
        [_weather(CELL, wind_speed=0.0, wind_direction=0.0, precipitation=0.0)],
        hours=(1,),
        generated_at=NOW,
    )
    wet = model.forecast(
        [state],
        [_weather(CELL, wind_speed=0.0, wind_direction=0.0, precipitation=8.0)],
        hours=(1,),
        generated_at=NOW,
    )
    assert wet.forecasts[0].predicted_pm25 < dry.forecasts[0].predicted_pm25


# --- 9. Database unavailable ---


class _UnavailableGridStateRepository:
    """Simulates a database connection failure on every call."""

    def upsert(self, state):
        raise ConnectionError("could not connect to server: Connection refused")

    def get(self, h3_cell, timestamp):
        raise ConnectionError("could not connect to server: Connection refused")

    def latest(self):
        raise ConnectionError("could not connect to server: Connection refused")

    def latest_for_cell(self, h3_cell):
        raise ConnectionError("could not connect to server: Connection refused")


def test_scenario_09_database_unavailable_returns_a_clean_500_not_a_hang() -> None:
    """A request-time DB outage must not leak internals or hang — the
    global handler in app.api.errors converts ANY unhandled exception
    into the documented {"error": {...}} shape with a 500. (The CLI /
    full-pipeline path for the same failure is covered separately —
    see app.cli.main / app.pipeline.run.main's own top-level catch,
    verified live by pointing POSTGRES_HOST at an unresolvable host.)

    Needs its own TestClient with raise_server_exceptions=False: the
    default (True, what the shared api_client fixture uses) re-raises an
    unhandled exception into the *test* even after the app's own handler
    already turned it into a response — useful for catching a handler
    that doesn't exist, but this test is specifically checking the one
    that does.
    """
    from app.api.deps import get_grid_service
    from app.main import create_app
    from app.services.grid import GridService
    from tests.fakes import FakeForecastRepository

    app = create_app()
    app.dependency_overrides[get_grid_service] = lambda: GridService(
        _UnavailableGridStateRepository(), FakeForecastRepository()
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/v1/grid/current")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "unexpected error" in body["error"]["message"].lower()
    # Explicitly NOT a fabricated success — no "data" key, no is_demo.
    assert "data" not in body


# --- 11. Cell without sufficient sensor coverage ---


def test_scenario_11_insufficient_sensor_coverage_at_the_model_layer() -> None:
    estimator = IDWPollutionEstimator(max_distance_km=15.0, min_sensors=2)
    far_sensor = SensorReading(
        source="openaq",
        external_sensor_id="1",
        latitude=10.0,
        longitude=10.0,
        pollutant=PM25,
        value=50.0,
        unit="ug/m3",
        measured_at=NOW,
    )
    [state] = estimator.estimate([CELL], [far_sensor], timestamp=NOW)
    assert state.pm25 is None
    assert state.confidence == 0.0


def test_scenario_11_insufficient_sensor_coverage_at_the_api(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    fake_repos.grid.upsert(GridState(h3_cell=CELL, timestamp=NOW, confidence=0.0, pm25=None))

    response = api_client.get(f"/api/v1/cells/{CELL}")

    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is False  # real pipeline output, not a demo fallback
    assert body["data"]["current"]["pm25"] is None
    assert body["data"]["current"]["confidence"] == 0.0


# --- 12. Empty forecast ---


def test_scenario_12_empty_forecast_for_a_real_cell_is_an_empty_list_not_demo(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    fake_repos.grid.upsert(GridState(h3_cell=CELL, timestamp=NOW, confidence=0.8, pm25=20.0))

    response = api_client.get(f"/api/v1/cells/{CELL}")

    body = response.json()
    assert body["is_demo"] is False
    assert body["data"]["forecasts"] == []


def test_scenario_12_empty_forecast_grid_falls_back_to_demo(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/grid/forecast?hours=3")
    assert response.status_code == 200
    assert response.json()["is_demo"] is True


# --- 13. Unusually high PM2.5 ---


def test_scenario_13_unusually_high_pm25_flows_through_end_to_end_without_crashing() -> None:
    """A wildfire-scale reading (900 ug/m3, well past every configured
    threshold) must not crash the estimator, get silently clamped away
    by PDI, or fail to raise the alert it obviously warrants."""
    extreme_reading = SensorReading(
        source="openaq",
        external_sensor_id="1",
        latitude=h3.cell_to_latlng(CELL)[0],
        longitude=h3.cell_to_latlng(CELL)[1],
        pollutant=PM25,
        value=900.0,
        unit="ug/m3",
        measured_at=NOW,
    )
    estimator = IDWPollutionEstimator(max_distance_km=15.0, min_sensors=1)
    [state] = estimator.estimate([CELL], [extreme_reading], timestamp=NOW)
    assert state.pm25 == pytest.approx(900.0)  # not silently capped

    pdi_model = HeuristicPDIModel(
        pm25_reference=250.0,
        pm25_weight=0.7,
        road_pressure_weight=0.2,
        industrial_pressure_weight=0.1,
    )
    from app.domain.pdi import CellContext

    pdi_result = pdi_model.calculate(CellContext(h3_cell=CELL, pm25=state.pm25))
    assert pdi_result.pdi == pytest.approx(100.0)  # clamped, not an error
    assert pdi_result.factors["pm25"] == 1.0

    from tests.fakes import FakeAlertRepository

    alert_repo = FakeAlertRepository()
    alert_service = AlertGenerationService(
        alert_repo,
        warning_threshold_ugm3=55.0,
        critical_threshold_ugm3=150.0,
        sharp_increase_threshold_ugm3=25.0,
        pdi_high_threshold=60.0,
        pdi_worsening_min_increase_ugm3=5.0,
        active_lookback=timedelta(hours=24),
    )
    from dataclasses import replace

    finalized_state = replace(state, pdi=pdi_result.pdi)
    result = alert_service.run([finalized_state], [], generated_at=NOW)
    assert result.alerts_created == 1
    assert alert_repo.alerts[0].severity.value == "critical"
    assert alert_repo.alerts[0].current_pm25 == pytest.approx(900.0)


# --- 14. Invalid H3 cell ---


def test_scenario_14_invalid_h3_cell_is_a_422_not_a_500(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/cells/not-a-real-cell")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- 15. Unsupported forecast horizon ---


def test_scenario_15_unsupported_forecast_horizon_is_a_422_not_silently_rounded(
    api_client: TestClient,
) -> None:
    response = api_client.get("/api/v1/grid/forecast?hours=2")
    assert response.status_code == 422
