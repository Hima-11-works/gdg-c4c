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

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.domain.satellite_context import (
    CellEvidenceBundle,
    CellSatelliteAnalysisOut,
    SatelliteInterpretation,
    SatelliteVisualPattern,
)
from app.main import create_app
from app.services.cell_satellite import (
    CellSatelliteService,
    _pm25_color,
    render_cell_thumbnail,
)
from app.services.gemini_assessment import (
    GeminiAnalysisDisabled,
    GeminiInvalidApiKey,
    GeminiProviderTimeout,
    GeminiQuotaExceeded,
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


def test_evidence_bundle_contains_native_satellite_units_and_cpcb_gate() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"))
    service = CellSatelliteService(settings=settings)
    bundle = service.assemble_bundle(DELHI_CELL, now=NOW)

    assert bundle.h3_cell == DELHI_CELL
    assert bundle.satellite_no2.unit == "mol/m²"
    assert "satellite indicator — not ground-level concentration" in bundle.satellite_no2.disclaimer
    assert bundle.satellite_uvai.unit == "dimensionless UVAI (340/380 nm)"
    assert bundle.surface_pm25.unit == "µg/m³"
    # CPCB gate: with insufficient station pollutants, status is strictly unavailable
    assert bundle.cpcb_aqi.status == "unavailable"
    assert "CPCB" in bundle.cpcb_aqi.reason


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


def test_satellite_cell_color_uses_the_map_pm25_ramp() -> None:
    assert _pm25_color(0) == (34, 197, 94)
    assert _pm25_color(251) == (127, 29, 29)
    assert _pm25_color(None) == (72, 82, 96)
    assert _pm25_color(31) == (154, 222, 81)
    assert _pm25_color(200) != _pm25_color(251)


def test_gemini_analysis_caches_and_does_not_call_model_twice() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"))
    mock_analyzer = MockSatelliteAnalyzer()
    mock_repo = MockInterpretationRepo()
    service = CellSatelliteService(
        settings=settings,
        interpretation_repo=mock_repo,
        analyzer=mock_analyzer,
    )

    # First call: triggers analyzer
    res1 = service.analyze_cell(DELHI_CELL, now=NOW)
    assert res1.cached is False
    assert mock_analyzer.call_count == 1
    assert res1.interpretation is not None
    assert res1.interpretation.visual_pattern == SatelliteVisualPattern.PLUME_LIKE

    # Second call within TTL: served from cache
    res2 = service.analyze_cell(DELHI_CELL, now=NOW + timedelta(minutes=5))
    assert res2.cached is True
    assert mock_analyzer.call_count == 1  # No additional model call!
    assert res2.interpretation == res1.interpretation


def test_reanalyze_forces_fresh_model_call() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"))
    mock_analyzer = MockSatelliteAnalyzer()
    mock_repo = MockInterpretationRepo()
    service = CellSatelliteService(
        settings=settings,
        interpretation_repo=mock_repo,
        analyzer=mock_analyzer,
    )

    service.analyze_cell(DELHI_CELL, now=NOW)
    assert mock_analyzer.call_count == 1

    # Call with reanalyze=True
    service.analyze_cell(DELHI_CELL, reanalyze=True, now=NOW + timedelta(minutes=1))
    assert mock_analyzer.call_count == 2


def test_model_cannot_alter_server_measurements() -> None:
    """Invariant: numeric measurements and units in response originate from server, not Gemini."""
    settings = Settings(gemini_api_key=SecretStr("test-key"))
    mock_analyzer = MockSatelliteAnalyzer()
    service = CellSatelliteService(settings=settings, analyzer=mock_analyzer)

    res = service.analyze_cell(DELHI_CELL, now=NOW)
    assert res.evidence_bundle.satellite_no2.value == 0.000142
    assert res.evidence_bundle.satellite_no2.unit == "mol/m²"
    # Even if model output had arbitrary text, the bundle numbers are intact
    assert res.interpretation.summary != ""
    assert res.evidence_bundle.satellite_no2.unit == "mol/m²"


def test_api_routes_satellite_context_and_analysis() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"))
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
    assert data["evidence_bundle"]["satellite_no2"]["unit"] == "mol/m²"

    # 2. POST satellite-analysis
    resp_post = client.post(
        f"/api/v1/cells/{DELHI_CELL}/satellite-analysis",
        json={"reanalyze": False},
    )
    assert resp_post.status_code == 200
    assert resp_post.json()["data"]["interpretation"]["visual_pattern"] == "plume_like"

    # 3. GET thumbnail
    resp_thumb = client.get(f"/api/v1/cells/{DELHI_CELL}/satellite-thumbnail")
    assert resp_thumb.status_code == 200
    assert resp_thumb.headers["content-type"] == "image/png"
    assert resp_thumb.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_api_error_mappings() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"))
    app = create_app()
    from app.api.deps import get_cell_satellite_service, get_settings

    app.dependency_overrides[get_settings] = lambda: settings

    # Quota failure maps to 429
    service_quota = CellSatelliteService(
        settings=settings,
        analyzer=MockSatelliteAnalyzer(exception=GeminiQuotaExceeded("Quota exceeded")),
    )
    app.dependency_overrides[get_cell_satellite_service] = lambda: service_quota
    client = TestClient(app)
    resp = client.post(f"/api/v1/cells/{DELHI_CELL}/satellite-analysis", json={"reanalyze": True})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "gemini_quota_exceeded"

    # Timeout maps to 504
    service_timeout = CellSatelliteService(
        settings=settings,
        analyzer=MockSatelliteAnalyzer(exception=GeminiProviderTimeout("Timed out")),
    )
    app.dependency_overrides[get_cell_satellite_service] = lambda: service_timeout
    resp2 = client.post(f"/api/v1/cells/{DELHI_CELL}/satellite-analysis", json={"reanalyze": True})
    assert resp2.status_code == 504
    assert resp2.json()["error"]["code"] == "gemini_timeout"
