"""Cross-cutting checks: OpenAPI docs, the /api/v1 prefix, and the
consistent error shape for cases that aren't specific to one endpoint.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

EXPECTED_PATHS = {
    "/health",
    "/health/ready",
    "/api/v1/sensors",
    "/api/v1/weather",
    "/api/v1/grid/current",
    "/api/v1/grid/forecast",
    "/api/v1/cells/{h3_cell}",
    "/api/v1/alerts",
}


def test_openapi_schema_lists_every_endpoint(api_client: TestClient) -> None:
    response = api_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert set(schema["paths"]) == EXPECTED_PATHS
    for path in EXPECTED_PATHS:
        assert "get" in schema["paths"][path]


def test_docs_ui_is_served(api_client: TestClient) -> None:
    response = api_client.get("/docs")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_unknown_route_returns_consistent_error_shape(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body == {"error": {"code": "not_found", "message": "Not Found"}}


def test_cors_preflight_reflects_configured_origin(api_client: TestClient) -> None:
    response = api_client.options(
        "/api/v1/sensors",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_unconfigured_origin(api_client: TestClient) -> None:
    # Starlette's CORSMiddleware answers a disallowed-origin preflight itself
    # (400, plain text) before the request ever reaches a route or our
    # exception handlers, so this isn't wrapped in the {"error": ...} shape.
    response = api_client.options(
        "/api/v1/sensors",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
