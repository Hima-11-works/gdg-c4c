from fastapi.testclient import TestClient

from app.api.deps import get_incident_service
from app.domain.incidents import ResponderRole
from app.main import create_app


class RecordingIncidentService:
    def __init__(self) -> None:
        self.last_query = None

    def list(
        self,
        *,
        status=None,
        role=None,
        archived_only=False,
        limit=None,
        offset=0,
    ):
        self.last_query = {
            "status": status,
            "role": role,
            "archived_only": archived_only,
            "limit": limit,
            "offset": offset,
        }
        return []


def test_incident_list_defaults_to_active_and_accepts_archive_page() -> None:
    service = RecordingIncidentService()
    app = create_app()
    app.dependency_overrides[get_incident_service] = lambda: service

    with TestClient(app) as client:
        active = client.get("/api/v1/incidents")
        assert active.status_code == 200
        assert service.last_query == {
            "status": None,
            "role": None,
            "archived_only": False,
            "limit": 50,
            "offset": 0,
        }

        archive = client.get(
            "/api/v1/incidents",
            params={
                "archive": "true",
                "role": "fire_department",
                "limit": 25,
                "offset": 50,
            },
        )
        assert archive.status_code == 200
        assert service.last_query == {
            "status": None,
            "role": ResponderRole.FIRE_DEPARTMENT,
            "archived_only": True,
            "limit": 25,
            "offset": 50,
        }
