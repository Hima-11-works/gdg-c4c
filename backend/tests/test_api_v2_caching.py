"""Tests for API v2 ETags, conditional requests (304), and Cache-Control headers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import h3
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_cell_satellite_service, get_prediction_query_service
from app.domain.features import DataMode, DatasetRef, InputKind
from app.domain.prediction import PredictionInputQuality, PredictionResult, PredictionRun
from app.domain.satellite_context import CellSatelliteAnalysisOut
from app.main import create_app
from app.services.prediction_queries import PredictionQueryService
from tests.test_prediction_publication import MemoryPublicationRepository


@pytest.fixture
def caching_client() -> tuple[TestClient, PredictionRun]:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    cell = h3.latlng_to_cell(28.6, 77.1, 8)
    ref = DatasetRef(
        dataset_id="openaq-pm25-v3",
        source="openaq",
        product="pm25",
        version="v3",
        kind=InputKind.OBSERVED,
        region="india",
        attribution="OpenAQ",
        license="open",
    )
    run = PredictionRun(
        run_id="run-caching-test",
        generated_at=now,
        region="india",
        mode=DataMode.LIVE,
        feature_run_id="feature-run-1",
        feature_schema_version="environmental-v1",
        model_versions=("residual-ridge-v1",),
        dataset_refs=(ref,),
        input_quality=PredictionInputQuality(
            supported_cells=1,
            expected_cells=1,
            unavailable_reasons=(),
            input_fingerprint="abc123hash",
        ),
    )
    result = PredictionResult(
        run_id=run.run_id,
        h3_cell=cell,
        horizon_hours=0,
        valid_at=now,
        baseline_pm25=10,
        predicted_pm25=42.0,
        prediction_method="observed-current",
        input_kind=InputKind.OBSERVED,
    )
    repo = MemoryPublicationRepository(run, [result])
    queries = PredictionQueryService(repo, native_resolution=8)

    app = create_app()
    app.dependency_overrides[get_prediction_query_service] = lambda: queries

    from app.core.config import Settings
    from app.services.cell_satellite import CellSatelliteService

    satellite_service = CellSatelliteService(settings=Settings(demo_mode=True))
    bundle = satellite_service.assemble_bundle(cell)
    analysis_out = CellSatelliteAnalysisOut(
        evidence_bundle=bundle,
        interpretation=None,
    )
    mock_satellite = MagicMock()
    mock_satellite.is_demo = False
    mock_satellite.analyze_cell.return_value = analysis_out
    app.dependency_overrides[get_cell_satellite_service] = lambda: mock_satellite

    client = TestClient(app)
    return client, run


def test_meta_etag_and_304_conditional_response(
    caching_client: tuple[TestClient, PredictionRun],
) -> None:
    client, run = caching_client

    response = client.get("/api/v2/meta")
    assert response.status_code == 200
    assert "etag" in response.headers
    assert "max-age=30" in response.headers.get("cache-control", "")
    etag = response.headers["etag"]

    conditional_response = client.get("/api/v2/meta", headers={"if-none-match": etag})
    assert conditional_response.status_code == 304
    assert conditional_response.headers.get("etag") == etag


def test_grid_current_etag_and_304(caching_client: tuple[TestClient, PredictionRun]) -> None:
    client, run = caching_client

    response = client.get("/api/v2/grid/current", params={"run_id": run.run_id})
    assert response.status_code == 200
    assert "etag" in response.headers
    assert "max-age=300" in response.headers.get("cache-control", "")
    etag = response.headers["etag"]

    conditional = client.get(
        "/api/v2/grid/current",
        params={"run_id": run.run_id},
        headers={"if-none-match": etag},
    )
    assert conditional.status_code == 304


def test_alerts_etag_and_304(caching_client: tuple[TestClient, PredictionRun]) -> None:
    client, run = caching_client

    response = client.get("/api/v2/alerts", params={"run_id": run.run_id})
    assert response.status_code == 200
    assert "etag" in response.headers
    etag = response.headers["etag"]

    conditional = client.get(
        "/api/v2/alerts",
        params={"run_id": run.run_id},
        headers={"if-none-match": etag},
    )
    assert conditional.status_code == 304


def test_cell_satellite_context_etag_and_304(
    caching_client: tuple[TestClient, PredictionRun],
) -> None:
    client, run = caching_client
    cell = h3.latlng_to_cell(28.6, 77.1, 8)

    response = client.get(f"/api/v1/cells/{cell}/satellite-context")
    assert response.status_code == 200
    assert "etag" in response.headers
    assert "max-age=300" in response.headers.get("cache-control", "")
    etag = response.headers["etag"]

    conditional = client.get(
        f"/api/v1/cells/{cell}/satellite-context",
        headers={"if-none-match": etag},
    )
    assert conditional.status_code == 304


def test_reviewer_evidence_not_cached_publicly(
    caching_client: tuple[TestClient, PredictionRun],
) -> None:
    client, _ = caching_client
    from app.api.deps import get_evidence_service

    mock_evidence = MagicMock()
    mock_evidence.list_for_report.return_value = []
    client.app.dependency_overrides[get_evidence_service] = lambda: mock_evidence

    response = client.get("/api/v1/reports/123/evidence")
    assert response.status_code == 200
    cache_control = response.headers.get("cache-control", "")
    assert "private" in cache_control
    assert "no-store" in cache_control

