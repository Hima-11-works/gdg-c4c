"""Browser preflights allow the HTTP methods exposed by the API."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app, wrap_cors_for_errors


def test_delete_preflight_is_allowed_for_configured_frontend(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://web.example")
    get_settings.cache_clear()
    try:
        response = TestClient(create_app()).options(
            "/api/v1/reports/1/evidence/2",
            headers={
                "Origin": "https://web.example",
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "x-reviewer-key",
            },
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://web.example"
    assert "DELETE" in response.headers["access-control-allow-methods"]


def test_unhandled_error_response_keeps_cors_headers(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://web.example")
    get_settings.cache_clear()
    try:
        app = create_app()

        @app.get("/test/unhandled")
        def unhandled() -> None:
            raise RuntimeError("simulated server error")

        response = TestClient(
            wrap_cors_for_errors(app), raise_server_exceptions=False
        ).get("/test/unhandled", headers={"Origin": "https://web.example"})
    finally:
        get_settings.cache_clear()

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.headers["access-control-allow-origin"] == "https://web.example"


def test_production_frontend_is_in_default_cors_allowlist() -> None:
    settings = get_settings()

    assert "https://air-health.vercel.app" in settings.cors_origin_list
