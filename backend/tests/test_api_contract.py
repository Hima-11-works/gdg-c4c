"""Tests for the API contract itself — not any one endpoint's business
logic (see test_api_grid.py, test_api_weather.py, test_api_cells.py for
that), but the cross-cutting guarantee the whole point of Envelope/
is_demo exists for: the frontend can never tell, from a response alone,
whether a value came from OpenAQ, Open-Meteo, seeded demo data, or a
future provider (satellite retrievals, a government sensor feed, ...).
See docs/architecture.md's "Source-agnostic contract" section.

Two layers of guard against the same regression:
  - test_public_schemas_never_name_a_data_source inspects the Pydantic
    schemas directly — catches a leaking field the moment someone adds
    it, before any endpoint test would even need updating.
  - the *_shape_identical_for_demo_and_real_data tests hit real routes
    both ways and diff the actual JSON keys — belt-and-suspenders in
    case a route ever bypasses response_model (a raw dict return, a
    different serialization path) in a way the schema check alone
    wouldn't see.
"""

from __future__ import annotations

from datetime import UTC, datetime

import h3
from fastapi.testclient import TestClient

from app.api.schemas import (
    AlertOut,
    CellDetailOut,
    Envelope,
    ForecastOut,
    GridStateOut,
    WeatherReadingOut,
)
from app.core.config import get_settings
from app.domain.types import Alert, AlertSeverity, Forecast, GridState, WeatherReading
from app.services import demo_data
from tests.conftest import FakeRepos

# Every schema a map/grid-facing endpoint returns. SensorReadingOut is
# deliberately excluded: GET /api/v1/sensors is a raw ingestion audit
# trail (it does carry `source`/`external_sensor_id` on purpose), not
# part of this contract, and the frontend never calls it — see
# docs/architecture.md's "Source-agnostic contract" section.
_PUBLIC_SCHEMAS = [GridStateOut, ForecastOut, WeatherReadingOut, CellDetailOut, AlertOut]

# Checked against field NAMES, not values — a field called "source" is
# the leak; a `message` value that happens to mention a provider isn't
# what these tests are for.
_FORBIDDEN_FIELD_SUBSTRINGS = ("source", "provider", "origin", "vendor", "openaq", "meteo")


def _assert_no_source_leak(field_names: set[str], *, where: str) -> None:
    for name in field_names:
        lowered = name.lower()
        assert not any(bad in lowered for bad in _FORBIDDEN_FIELD_SUBSTRINGS), (
            f"{where}.{name} looks like it names a data source"
        )


def test_public_schemas_never_name_a_data_source() -> None:
    for schema in _PUBLIC_SCHEMAS:
        _assert_no_source_leak(set(schema.model_fields), where=schema.__name__)


def test_envelope_is_demo_is_the_only_provenance_signal() -> None:
    """Envelope itself only ever adds generated_at/is_demo/data — is_demo
    answers "illustrative or measured?", never "which system?"."""
    field_names = set(Envelope.model_fields) - {"data"}
    assert field_names == {"generated_at", "is_demo"}


def test_grid_current_shape_identical_for_demo_and_real_data(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    demo_body = api_client.get("/api/v1/grid/current").json()
    assert demo_body["is_demo"] is True
    demo_keys = set(demo_body["data"][0].keys())

    fake_repos.grid.upsert(
        GridState(
            h3_cell="8828308281fffff",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            pm25=30.0,
            pdi=50.0,
            confidence=0.9,
            wind_speed=2.0,
            wind_direction=180.0,
        )
    )
    real_body = api_client.get("/api/v1/grid/current").json()
    assert real_body["is_demo"] is False
    real_keys = set(real_body["data"][0].keys())

    assert demo_keys == real_keys
    _assert_no_source_leak(demo_keys, where="GET /grid/current")


def test_grid_forecast_shape_identical_for_demo_and_real_data(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    demo_body = api_client.get("/api/v1/grid/forecast", params={"hours": 3}).json()
    assert demo_body["is_demo"] is True
    demo_keys = set(demo_body["data"][0].keys())

    fake_repos.forecast.add(
        Forecast(
            h3_cell="8828308281fffff",
            generated_at=datetime(2026, 1, 1, tzinfo=UTC),
            forecast_time=datetime(2026, 1, 1, 3, tzinfo=UTC),
            forecast_hours=3,
            predicted_pm25=25.0,
            confidence=0.7,
        )
    )
    real_body = api_client.get("/api/v1/grid/forecast", params={"hours": 3}).json()
    assert real_body["is_demo"] is False
    real_keys = set(real_body["data"][0].keys())

    assert demo_keys == real_keys
    _assert_no_source_leak(demo_keys, where="GET /grid/forecast")


def test_weather_shape_identical_for_demo_and_real_data(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    demo_body = api_client.get("/api/v1/weather").json()
    assert demo_body["is_demo"] is True
    demo_keys = set(demo_body["data"][0].keys())

    fake_repos.weather.add(
        WeatherReading(
            h3_cell="8828308281fffff",
            latitude=1.0,
            longitude=2.0,
            wind_speed=4.0,
            wind_direction=90.0,
            precipitation=0.5,
            measured_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    real_body = api_client.get("/api/v1/weather").json()
    assert real_body["is_demo"] is False
    real_keys = set(real_body["data"][0].keys())

    assert demo_keys == real_keys
    _assert_no_source_leak(demo_keys, where="GET /weather")


def test_cell_detail_shape_identical_for_demo_and_real_data(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    resolution = get_settings().h3_resolution
    demo_cell = demo_data.demo_cells(resolution)[0]
    demo_body = api_client.get(f"/api/v1/cells/{demo_cell}").json()
    assert demo_body["is_demo"] is True
    demo_keys = set(demo_body["data"].keys())
    demo_current_keys = set(demo_body["data"]["current"].keys())
    demo_weather_keys = set(demo_body["data"]["weather"].keys())

    real_cell = h3.latlng_to_cell(10.0, 20.0, resolution)
    fake_repos.grid.upsert(
        GridState(
            h3_cell=real_cell,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            pm25=12.0,
            pdi=10.0,
            confidence=0.95,
            wind_speed=1.0,
            wind_direction=0.0,
        )
    )
    fake_repos.weather.add(
        WeatherReading(
            h3_cell=real_cell,
            latitude=10.0,
            longitude=20.0,
            wind_speed=1.0,
            wind_direction=0.0,
            precipitation=0.0,
            measured_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    real_body = api_client.get(f"/api/v1/cells/{real_cell}").json()
    assert real_body["is_demo"] is False
    real_keys = set(real_body["data"].keys())
    real_current_keys = set(real_body["data"]["current"].keys())
    real_weather_keys = set(real_body["data"]["weather"].keys())

    assert demo_keys == real_keys
    assert demo_current_keys == real_current_keys
    assert demo_weather_keys == real_weather_keys
    _assert_no_source_leak(demo_keys, where="GET /cells/{h3_cell}")
    _assert_no_source_leak(demo_current_keys, where="GET /cells/{h3_cell}.current")
    _assert_no_source_leak(demo_weather_keys, where="GET /cells/{h3_cell}.weather")


def test_alerts_shape_identical_for_demo_and_real_data(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    demo_body = api_client.get("/api/v1/alerts").json()
    assert demo_body["is_demo"] is True
    demo_keys = set(demo_body["data"][0].keys())

    fake_repos.alert.add(
        Alert(
            h3_cell="8828308281fffff",
            severity=AlertSeverity.WARNING,
            message="Real alert",
            created_at=datetime.now(UTC),
        )
    )
    real_body = api_client.get("/api/v1/alerts").json()
    assert real_body["is_demo"] is False
    real_keys = set(real_body["data"][0].keys())

    assert demo_keys == real_keys
    _assert_no_source_leak(demo_keys, where="GET /alerts")
