"""Browser preflights allow the HTTP methods exposed by the API."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


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
