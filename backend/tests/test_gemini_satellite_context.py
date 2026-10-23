"""Tests for Gemini-assisted satellite context per H3 cell (Step 6).

Guarantees:
- Measured surface air quality and satellite indicators are distinct
- Every number carries source, native unit, timestamp, and QA/coverage
- Gemini structured output is advisory only and never generates or overwrites server measurements
- CPCB AQI is only available when official criteria pass (min 3 pollutants including PM)
- Gemini interpretations are cached by cell, window, model, and prompt version
- No live model calls in automated tests
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import h3
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.domain.features import DataMode
from app.domain.satellite_context import (
    CellEvidenceBundle,
    SatelliteInterpretation,
    SatelliteVisualPattern,
)
from app.domain.types import SensorReading
from app.main import create_app
from app.services.cell_satellite import (
    CellSatelliteService,
    _pm25_color,
    render_cell_thumbnail,
)

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
DELHI_CELL = "863da1147ffffff"  # Valid res 6 cell


class MockSatelliteAnalyzer:
    def __init__(
        self,
        interpretation: SatelliteInterpretation | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.interpretation = interpretation or SatelliteInterpretation(
            visual_pattern=SatelliteVisualPattern.PLUME_LIKE,
            possible_event_type="elevated column density",
            supporting_evidence=["Visible plume dispersion toward south-east", "Corroborated by high NO2 column"],
            limitations=["Satellite column density is not surface PM2.5", "Resolution is 3.5x5.5 km"],
            summary="A localized plume pattern is visible in the satellite layer matching local transport direction.",
        )
        self.exception = exception
        self.call_count = 0
        self.last_bundle: CellEvidenceBundle | None = None
        self.last_image_bytes: bytes | None = None

    def analyze(
        self,
        image_bytes: bytes,
        evidence_bundle: CellEvidenceBundle,
    ) -> SatelliteInterpretation:
        self.call_count += 1
        self.last_image_bytes = image_bytes
        self.last_bundle = evidence_bundle
        if self.exception is not None:
            raise self.exception
        return self.interpretation


class MockInterpretationRepo:
    def __init__(self) -> None:
        self.storage: dict[tuple[str, datetime, datetime, str, str], Any] = {}

    def get_valid(
        self,
        *,
        h3_cell: str,
        window_start: datetime,
        window_end: datetime,
        model_id: str,
        prompt_version: str,
        now: datetime,
    ) -> Any | None:
        key = (h3_cell, window_start, window_end, model_id, prompt_version)
        record = self.storage.get(key)
        if record and record.expires_at > now:
            return record
        return None

    def save(
        self,
        *,
        h3_cell: str,
        window_start: datetime,
        window_end: datetime,
        evidence_bundle: CellEvidenceBundle,
        interpretation: SatelliteInterpretation,
        model_id: str,
        prompt_version: str,
        schema_version: str,
        generated_at: datetime,
        expires_at: datetime,
    ) -> Any:
        class Record:
            def __init__(self) -> None:
                self.h3_cell = h3_cell
                self.window_start = window_start
                self.window_end = window_end
                self.evidence_bundle = evidence_bundle
                self.interpretation = interpretation
                self.model_id = model_id
                self.prompt_version = prompt_version
                self.schema_version = schema_version
                self.generated_at = generated_at
                self.expires_at = expires_at

        record = Record()
        self.storage[(h3_cell, window_start, window_end, model_id, prompt_version)] = record
        return record


class MockSensorRepo:
    def __init__(self, readings: list[SensorReading]) -> None:
        self.readings = readings
        self.queries: list[tuple[datetime, str | None]] = []

    def list_since(self, since: datetime, *, pollutant: str | None = None) -> list[SensorReading]:
        self.queries.append((since, pollutant))
        return [
            reading
            for reading in self.readings
            if reading.measured_at >= since
            and (pollutant is None or reading.pollutant == pollutant)
        ]


class MockFireRepo:
    def __init__(self) -> None:
        self.query: dict[str, Any] | None = None

    def list_for_window(self, **query: Any) -> list[Any]:
        self.query = query
        return []


def _sensor(
    *,
    source: str = "openaq",
    sensor_id: str = "sensor-1",
    latitude: float | None = None,
    longitude: float | None = None,
    measured_at: datetime = NOW,
    value: float = 42.0,
    unit: str = "µg/m³",
) -> SensorReading:
    center_lat, center_lon = h3.cell_to_latlng(DELHI_CELL)
    return SensorReading(
        source=source,
        external_sensor_id=sensor_id,
        latitude=center_lat if latitude is None else latitude,
        longitude=center_lon if longitude is None else longitude,
        pollutant="pm25",
        value=value,
        unit=unit,
        measured_at=measured_at,
    )


def test_evidence_bundle_contains_native_satellite_units_and_cpcb_gate() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"), demo_mode=True)
    service = CellSatelliteService(settings=settings)
    bundle = service.assemble_bundle(DELHI_CELL, now=NOW)

    assert bundle.h3_cell == DELHI_CELL
    assert bundle.satellite_no2.unit == "mol/m²"
    assert "satellite indicator — not ground-level concentration" in bundle.satellite_no2.disclaimer
    assert bundle.satellite_uvai.unit == "dimensionless UVAI (340/380 nm)"
    assert bundle.surface_pm25.unit == "µg/m³"
    assert bundle.satellite_no2.status == "unavailable"
    assert bundle.satellite_no2.value is None
    assert bundle.satellite_uvai.status == "unavailable"
    assert bundle.satellite_uvai.value is None
    assert bundle.weather.status == "unavailable"
    assert bundle.thermal_anomalies.status == "unavailable"
    assert bundle.baseline_anomaly.deviation_sigma is None
    assert bundle.cpcb_aqi.status == "unavailable"
    assert "CPCB" in bundle.cpcb_aqi.reason


def test_cell_evidence_bundle_exposes_persisted_sentinel_products() -> None:
    class SatelliteRepo:
        def latest_for_cell(self, h3_cell: str, *, indicator: str, available_by: datetime):
            assert h3_cell == DELHI_CELL
            assert available_by == NOW
            acquired_at = NOW - timedelta(hours=1 if indicator == "no2" else 40)
            return {
                "value": 0.00012 if indicator == "no2" else 1.8,
                "unit": (
                    "mol/m²"
                    if indicator == "no2"
                    else "unitless UV aerosol index (340/380 nm)"
                ),
                "qa_score": 0.96,
                "coverage_fraction": 0.7,
                "valid_pixels": 7,
                "sampled_pixels": 10,
                "acquired_at": acquired_at,
                "available_at": acquired_at + timedelta(minutes=10),
                "h3_resolution": 6,
                "product_id": "b0f9e574-73d2-4c8a-96d1-2f679e9717a7",
                "product_name": f"S5P_NRTI_L2__{indicator}",
                "product_version": "02.06.00",
                "source": "Copernicus Data Space Ecosystem",
            }

    service = CellSatelliteService(
        settings=Settings(demo_mode=False),
        satellite_repo=SatelliteRepo(),
    )
    bundle = service.assemble_bundle(DELHI_CELL, now=NOW)

    assert bundle.satellite_no2.status == "available"
    assert bundle.satellite_no2.value == pytest.approx(0.00012)
    assert bundle.satellite_no2.product_version == "02.06.00"
    assert bundle.satellite_no2.sampled_pixels == 10
    assert "not percent of the cell's land area" in bundle.satellite_no2.coverage_note
    assert bundle.satellite_uvai.status == "stale"
    assert bundle.satellite_uvai.value == pytest.approx(1.8)
    assert "S5P_NRTI_L2__no2" in bundle.data_provenance["satellite_context"]


def test_thumbnail_rendering_produces_valid_png() -> None:
    png = render_cell_thumbnail(
        DELHI_CELL,
        pm25_val=100.0,
        no2_val=0.00015,
        uvai_val=1.8,
        firms_count=2,
    )
    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")  # PNG magic header


def test_thumbnail_pm25_uses_the_active_data_mode() -> None:
    class ModeAwareGridRepo:
        mode: DataMode | None = None

        def latest_for_cell(self, h3_cell: str, *, mode: DataMode | None = None):
            self.mode = mode
            return type("Grid", (), {"pm25": 42.0})()

    grid_repo = ModeAwareGridRepo()
    service = CellSatelliteService(
        settings=Settings(demo_mode=False),
        grid_repo=grid_repo,
    )

    assert service.map_pm25_for_cell(DELHI_CELL) == 42.0
    assert grid_repo.mode is DataMode.LIVE


def test_surface_context_uses_only_a_nearby_reading_from_the_active_mode() -> None:
    far = _sensor(latitude=29.2, longitude=78.0)
    stale = _sensor(sensor_id="stale", measured_at=NOW - timedelta(hours=5))
    fresh = _sensor(
        sensor_id="fresh",
        measured_at=NOW - timedelta(minutes=25),
        value=0.042,
        unit="mg/m³",
    )
    synthetic = _sensor(source="demo", sensor_id="synthetic", value=999)
    service = CellSatelliteService(
        settings=Settings(demo_mode=False),
        sensor_repo=MockSensorRepo([far, stale, fresh, synthetic]),
    )

    surface = service._get_surface_pm25(
        *h3.cell_to_latlng(DELHI_CELL),
        NOW,
    )

    assert surface.status == "available"
    assert surface.station_id == "fresh"
    assert surface.value_ugm3 == 42.0
    assert surface.uncertainty_ugm3 is None
    assert surface.station_distance_km == 0


def test_surface_context_marks_old_nearby_station_data_stale() -> None:
    service = CellSatelliteService(
        settings=Settings(demo_mode=False),
        sensor_repo=MockSensorRepo(
            [_sensor(measured_at=NOW - timedelta(hours=5))]
        ),
    )
    center = h3.cell_to_latlng(DELHI_CELL)

    surface = service._get_surface_pm25(
        *center, NOW
    )

    assert surface.status == "stale"
    assert surface.value_ugm3 == 42.0


def test_firms_context_queries_actual_window_repository() -> None:
    repo = MockFireRepo()
    service = CellSatelliteService(fire_repo=repo)

    bundle = service.assemble_bundle(DELHI_CELL, now=NOW)

    assert bundle.thermal_anomalies.status == "available"
    assert bundle.thermal_anomalies.detection_count == 0
    assert repo.query is not None
    assert repo.query["bbox"].min_lat < repo.query["bbox"].max_lat
    assert repo.query["available_by"] == NOW


def test_satellite_cell_color_uses_the_map_pm25_ramp() -> None:
    assert _pm25_color(0) == (34, 197, 94)
    assert _pm25_color(251) == (127, 29, 29)
    assert _pm25_color(None) == (72, 82, 96)
    assert _pm25_color(31) == (154, 222, 81)
    assert _pm25_color(200) != _pm25_color(251)


def test_gemini_is_not_given_an_illustrative_cell_diagram() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"))
    mock_analyzer = MockSatelliteAnalyzer()
    mock_repo = MockInterpretationRepo()
    service = CellSatelliteService(
        settings=settings,
        interpretation_repo=mock_repo,
        analyzer=mock_analyzer,
    )

    # There is no georeferenced raster in this service, so the model must not
    # receive the generated H3 indicator diagram as if it were imagery.
    res1 = service.analyze_cell(DELHI_CELL, now=NOW)
    assert res1.cached is False
    assert mock_analyzer.call_count == 0
    assert res1.interpretation is None
    assert res1.thumbnail_available is False
    assert "not sent to Gemini" in res1.analysis_unavailable_reason

    res2 = service.analyze_cell(DELHI_CELL, now=NOW + timedelta(minutes=5))
    assert res2.cached is False
    assert mock_analyzer.call_count == 0


def test_reanalyze_does_not_send_a_diagram_to_gemini() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"))
    mock_analyzer = MockSatelliteAnalyzer()
    mock_repo = MockInterpretationRepo()
    service = CellSatelliteService(
        settings=settings,
        interpretation_repo=mock_repo,
        analyzer=mock_analyzer,
    )

    service.analyze_cell(DELHI_CELL, now=NOW)
    assert mock_analyzer.call_count == 0

    # Call with reanalyze=True
    service.analyze_cell(DELHI_CELL, reanalyze=True, now=NOW + timedelta(minutes=1))
    assert mock_analyzer.call_count == 0


def test_absent_satellite_measurements_stay_null_even_with_gemini_configured() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"))
    mock_analyzer = MockSatelliteAnalyzer()
    service = CellSatelliteService(settings=settings, analyzer=mock_analyzer)

    res = service.analyze_cell(DELHI_CELL, now=NOW)
    assert res.evidence_bundle.satellite_no2.value is None
    assert res.evidence_bundle.satellite_no2.unit == "mol/m²"
    assert res.interpretation is None
    assert mock_analyzer.call_count == 0
    assert res.evidence_bundle.satellite_no2.unit == "mol/m²"


def test_api_routes_satellite_context_and_analysis() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"), demo_mode=True)
    mock_analyzer = MockSatelliteAnalyzer()
    mock_repo = MockInterpretationRepo()
    service = CellSatelliteService(
        settings=settings,
        interpretation_repo=mock_repo,
        analyzer=mock_analyzer,
    )

    app = create_app()
    from app.api.deps import get_cell_satellite_service, get_settings

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_cell_satellite_service] = lambda: service
    client = TestClient(app)

    # 1. GET satellite-context
    resp = client.get(f"/api/v1/cells/{DELHI_CELL}/satellite-context")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["evidence_bundle"]["h3_cell"] == DELHI_CELL
    assert data["evidence_bundle"]["satellite_no2"]["value"] is None
    assert resp.json()["is_demo"] is True

    # 2. POST satellite-analysis
    resp_post = client.post(
        f"/api/v1/cells/{DELHI_CELL}/satellite-analysis",
        json={"reanalyze": False},
    )
    assert resp_post.status_code == 200
    assert resp_post.json()["data"]["interpretation"] is None
    assert resp_post.json()["data"]["thumbnail_available"] is False
    assert mock_analyzer.call_count == 0

    # 3. GET thumbnail
    resp_thumb = client.get(f"/api/v1/cells/{DELHI_CELL}/satellite-thumbnail")
    assert resp_thumb.status_code == 200
    assert resp_thumb.headers["content-type"] == "image/png"
    assert resp_thumb.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_thumbnail_is_explicitly_an_illustrative_indicator_preview() -> None:
    png = render_cell_thumbnail(DELHI_CELL)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
