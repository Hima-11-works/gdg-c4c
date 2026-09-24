"""Contract tests for the incident workflow.

Runs against in-memory fakes (no database). Covers the documented sequence:
create from an eligible fire alert, assign, progress through response states,
and retrieve the complete history — plus duplicate handling, invalid
transitions, role checks, and the anonymous-write protection.

Every write carries the simulator key *and* an X-Actor-Id naming a responder in
SIMULATOR_ACTORS: the key authenticates the simulator, the actor identity
carries the role and jurisdiction the server enforces. See
tests/test_incident_authority.py for published-alert sources, the simulated
inbox, and the jurisdiction rules.
"""

from datetime import UTC, datetime, timedelta

import h3
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.types import AlertSeverity, FireKind, FireReport
from tests.conftest import FakeRepos

KEY = "test-simulator-key"
# unit-12 is a pollution-control responder scoped to Delhi (see conftest's
# SIMULATOR_ACTORS); engine-7 is the fire-department equivalent.
HEADERS = {"X-Simulator-Key": KEY, "X-Actor-Id": "unit-12"}
FIRE_HEADERS = {"X-Simulator-Key": KEY, "X-Actor-Id": "engine-7"}
LAT, LON = 28.55, 77.20


def _seed_alert(fake_repos: FakeRepos, *, severity: AlertSeverity = AlertSeverity.CRITICAL) -> int:
    alert = _make_alert(severity)
    stored = fake_repos.alert.add(alert)
    return stored.id


def _make_alert(severity: AlertSeverity):
    from app.domain.types import Alert

    return Alert(
        h3_cell=h3.latlng_to_cell(LAT, LON, get_settings().h3_resolution),
        severity=severity,
        message="Critical PM2.5 episode",
        created_at=datetime.now(UTC),
    )


def _seed_report(fake_repos: FakeRepos, *, kind: FireKind = FireKind.CROP_BURNING) -> int:
    report = FireReport(
        h3_cell=h3.latlng_to_cell(LAT, LON, get_settings().h3_resolution),
        latitude=LAT,
        longitude=LON,
        kind=kind,
        smoke_intensity=4,
        duration_hours=1.0,
        reported_at=datetime.now(UTC),
    )
    return fake_repos.fire.save(report).id


# --- documented sequence --------------------------------------------------


def test_fire_incident_full_lifecycle_with_history(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    alert_id = _seed_alert(fake_repos, severity=AlertSeverity.CRITICAL)

    created = api_client.post(
        "/api/v1/incidents",
        headers=HEADERS,
        json={"source_type": "alert", "source_id": alert_id, "jurisdiction": "Delhi"},
    )
    assert created.status_code == 201
    incident = created.json()["data"]
    incident_id = incident["id"]
    assert incident["status"] == "reported"
    assert incident["responder_role"] == "pollution_control"
    assert incident["severity"] == "critical"
    assert incident["jurisdiction"] == "Delhi"

    assigned = api_client.post(
        f"/api/v1/incidents/{incident_id}/assign",
        headers=HEADERS,
        json={"role": "pollution_control", "assignee": "unit-12"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["data"]["status"] == "assigned"
    assert assigned.json()["data"]["assignee"] == "unit-12"

    for target in ("acknowledged", "en_route", "on_scene", "resolved"):
        step = api_client.post(
            f"/api/v1/incidents/{incident_id}/transitions",
            headers=HEADERS,
            json={"to_status": target, "role": "pollution_control"},
        )
        assert step.status_code == 200, step.text
        assert step.json()["data"]["status"] == target

    final = api_client.get(f"/api/v1/incidents/{incident_id}").json()["data"]
    assert final["status"] == "resolved"
    assert final["resolved_at"] is not None

    history = api_client.get(f"/api/v1/incidents/{incident_id}/history")
    assert history.status_code == 200
    events = history.json()["data"]
    types = [event["event_type"] for event in events]
    # Assignment also records the simulated hand-off to the responder's inbox.
    assert types == [
        "created",
        "assigned",
        "delivered",
        "transition",
        "transition",
        "transition",
        "transition",
    ]
    assert events[-1]["to_status"] == "resolved"
    # The append-only history never loses the earlier states.
    assert events[0]["to_status"] == "reported"
    # Every event names the acting authority, not just the role.
    assert events[1]["actor"] == "unit-12"
    assert events[1]["actor_jurisdiction"] == "Delhi"


def test_fire_report_creates_fire_department_incident(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos)

    created = api_client.post(
        "/api/v1/incidents",
        headers=HEADERS,
        json={"source_type": "report", "source_id": report_id},
    )

    assert created.status_code == 201
    data = created.json()["data"]
    assert data["responder_role"] == "fire_department"
    assert abs(data["latitude"] - LAT) < 1e-6
    assert abs(data["longitude"] - LON) < 1e-6


def test_pollution_only_report_is_pollution_control(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report_id = _seed_report(fake_repos, kind=FireKind.OTHER)

    created = api_client.post(
        "/api/v1/incidents",
        headers=HEADERS,
        json={"source_type": "report", "source_id": report_id},
    )

    assert created.status_code == 201
    assert created.json()["data"]["responder_role"] == "pollution_control"


# --- duplicate handling ---------------------------------------------------


def test_create_is_idempotent_on_source(api_client: TestClient, fake_repos: FakeRepos) -> None:
    alert_id = _seed_alert(fake_repos)

    first = api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "alert", "source_id": alert_id}
    )
    second = api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "alert", "source_id": alert_id}
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert len(fake_repos.incidents.incidents) == 1


def test_conflicting_create_is_409(api_client: TestClient, fake_repos: FakeRepos) -> None:
    alert_id = _seed_alert(fake_repos)

    api_client.post(
        "/api/v1/incidents",
        headers=HEADERS,
        json={"source_type": "alert", "source_id": alert_id, "jurisdiction": "Delhi"},
    )
    conflict = api_client.post(
        "/api/v1/incidents",
        headers=HEADERS,
        json={"source_type": "alert", "source_id": alert_id, "jurisdiction": "Mumbai"},
    )

    assert conflict.status_code == 409


def test_retried_transition_to_same_status_is_noop(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    alert_id = _seed_alert(fake_repos)
    incident_id = api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "alert", "source_id": alert_id}
    ).json()["data"]["id"]
    api_client.post(
        f"/api/v1/incidents/{incident_id}/assign",
        headers=HEADERS,
        json={"role": "pollution_control", "assignee": "u1"},
    )

    first = api_client.post(
        f"/api/v1/incidents/{incident_id}/transitions",
        headers=HEADERS,
        json={"to_status": "acknowledged", "role": "pollution_control"},
    )
    repeat = api_client.post(
        f"/api/v1/incidents/{incident_id}/transitions",
        headers=HEADERS,
        json={"to_status": "acknowledged", "role": "pollution_control"},
    )

    assert first.status_code == 200
    assert repeat.status_code == 200
    # The retry did not append a second event.
    events = fake_repos.incidents.history(incident_id)
    assert [e.to_status.value if e.to_status else None for e in events].count("acknowledged") == 1


# --- invalid transitions --------------------------------------------------


def test_skipping_a_state_is_409(api_client: TestClient, fake_repos: FakeRepos) -> None:
    alert_id = _seed_alert(fake_repos)
    incident_id = api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "alert", "source_id": alert_id}
    ).json()["data"]["id"]

    # reported -> on_scene is not allowed.
    response = api_client.post(
        f"/api/v1/incidents/{incident_id}/transitions",
        headers=HEADERS,
        json={"to_status": "on_scene", "role": "pollution_control"},
    )

    assert response.status_code == 409


def test_transition_after_resolved_is_409(api_client: TestClient, fake_repos: FakeRepos) -> None:
    alert_id = _seed_alert(fake_repos)
    incident_id = api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "alert", "source_id": alert_id}
    ).json()["data"]["id"]
    api_client.post(
        f"/api/v1/incidents/{incident_id}/assign",
        headers=HEADERS,
        json={"role": "pollution_control", "assignee": "u1"},
    )
    for target in ("acknowledged", "en_route", "on_scene", "resolved"):
        api_client.post(
            f"/api/v1/incidents/{incident_id}/transitions",
            headers=HEADERS,
            json={"to_status": target, "role": "pollution_control"},
        )

    response = api_client.post(
        f"/api/v1/incidents/{incident_id}/transitions",
        headers=HEADERS,
        json={"to_status": "cancelled", "role": "pollution_control"},
    )

    assert response.status_code == 409


def test_generic_transition_to_assigned_is_409(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    alert_id = _seed_alert(fake_repos)
    incident_id = api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "alert", "source_id": alert_id}
    ).json()["data"]["id"]

    response = api_client.post(
        f"/api/v1/incidents/{incident_id}/transitions",
        headers=HEADERS,
        json={"to_status": "assigned", "role": "pollution_control"},
    )

    assert response.status_code == 409


# --- role checks ----------------------------------------------------------


def test_wrong_role_for_incident_is_403(api_client: TestClient, fake_repos: FakeRepos) -> None:
    report_id = _seed_report(fake_repos)  # fire_department incident
    incident_id = api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "report", "source_id": report_id}
    ).json()["data"]["id"]

    response = api_client.post(
        f"/api/v1/incidents/{incident_id}/assign",
        headers=HEADERS,
        json={"role": "pollution_control", "assignee": "u1"},
    )

    assert response.status_code == 403


# --- source eligibility ---------------------------------------------------


def test_watch_alert_is_not_eligible(api_client: TestClient, fake_repos: FakeRepos) -> None:
    alert_id = _seed_alert(fake_repos, severity=AlertSeverity.WATCH)

    response = api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "alert", "source_id": alert_id}
    )

    assert response.status_code == 422


def test_unknown_source_is_404(api_client: TestClient, fake_repos: FakeRepos) -> None:
    response = api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "alert", "source_id": 99999}
    )

    assert response.status_code == 404


# --- permissions ----------------------------------------------------------


def test_write_without_key_is_401(api_client: TestClient, fake_repos: FakeRepos) -> None:
    alert_id = _seed_alert(fake_repos)

    response = api_client.post(
        "/api/v1/incidents", json={"source_type": "alert", "source_id": alert_id}
    )

    assert response.status_code == 401


def test_write_with_wrong_key_is_401(api_client: TestClient, fake_repos: FakeRepos) -> None:
    alert_id = _seed_alert(fake_repos)

    response = api_client.post(
        "/api/v1/incidents",
        headers={"X-Simulator-Key": "nope"},
        json={"source_type": "alert", "source_id": alert_id},
    )

    assert response.status_code == 401


def test_reads_need_no_key(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/incidents")

    assert response.status_code == 200


def test_writes_disabled_when_key_unset(
    api_client: TestClient, fake_repos: FakeRepos, monkeypatch
) -> None:
    alert_id = _seed_alert(fake_repos)
    settings = get_settings()
    monkeypatch.setattr(settings, "simulator_api_key", None)

    response = api_client.post(
        "/api/v1/incidents",
        headers=HEADERS,
        json={"source_type": "alert", "source_id": alert_id},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "simulator_disabled"


# --- listing --------------------------------------------------------------


def test_list_filters_by_status_and_role(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    alert_id = _seed_alert(fake_repos)
    api_client.post(
        "/api/v1/incidents", headers=HEADERS, json={"source_type": "alert", "source_id": alert_id}
    )

    listed = api_client.get("/api/v1/incidents", params={"status": "reported"})
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1

    none = api_client.get("/api/v1/incidents", params={"status": "resolved"})
    assert none.json()["data"] == []

    by_role = api_client.get("/api/v1/incidents", params={"role": "fire_department"})
    assert by_role.json()["data"] == []
