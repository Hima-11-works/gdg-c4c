"""Authority contract for the incident workflow: published-alert sources,
authenticated actors, jurisdiction enforcement, and the simulated inbox.

`tests/test_api_incidents.py` pins the original lifecycle. This module covers
what the workflow needed next:

1. **Published alerts have an identity.** The web reads `GET /api/v2/alerts`, so
   an alert from there must be nameable. Its identity is
   `v2:<run_id>:<h3_cell>:<forecast_hours>` — the same string the alerts
   endpoint returns — and creating an incident from it is idempotent.
2. **Writes carry an identity, not just a key.** `X-Actor-Id` is resolved
   against SIMULATOR_ACTORS; the role and jurisdiction come from the server, so
   a request body cannot claim authority the actor does not have.
3. **Assignment is visible to the intended responder** through a *simulated*
   inbox, with no notification of any kind, and acknowledging the incident
   closes the loop.

Nothing here sends anything: deliveries are rows in a table whose CHECK
constraint forces `simulated = true`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import h3
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_prediction_query_service
from app.core.config import get_settings
from app.domain.features import FEATURE_SCHEMA_VERSION, DataMode, FeatureQuality
from app.domain.incidents import IncidentDeliveryStatus, ResponderRole
from app.domain.prediction import PredictionResult, PredictionRun
from app.domain.published_alerts import (
    PublishedAlertIdentity,
    PublishedAlertIdentityError,
)
from app.domain.types import FireKind, FireReport
from app.services.incidents import SimulatorDisabledError, parse_simulator_actors
from app.services.prediction_queries import PredictionQueryService
from tests.conftest import FakeRepos

KEY = "test-simulator-key"
# Registries set in tests/conftest.py: role + jurisdiction per actor id.
POLLUTION = {"X-Simulator-Key": KEY, "X-Actor-Id": "unit-12"}  # pollution_control, Delhi
FIRE = {"X-Simulator-Key": KEY, "X-Actor-Id": "engine-7"}  # fire_department, Delhi
FIRE_MUMBAI = {"X-Simulator-Key": KEY, "X-Actor-Id": "engine-9"}  # fire_department, Mumbai
CONTROL_ROOM = {"X-Simulator-Key": KEY, "X-Actor-Id": "control-room"}  # pollution, Delhi

LAT, LON = 28.55, 77.20
RESOLUTION = 8
CELL = h3.latlng_to_cell(LAT, LON, RESOLUTION)
RUN_ID = "pred-20260924T0000Z-india"
HORIZON = 6


# --- fixtures -------------------------------------------------------------


def _publish(
    fake_repos: FakeRepos,
    *,
    run_id: str = RUN_ID,
    forecast_pm25: float = 310.0,
    current_pm25: float = 140.0,
    mode: DataMode = DataMode.LIVE,
    synthetic: bool = False,
) -> PredictionRun:
    """Publish a run with one cell, current and +6h, the way the pipeline does."""
    now = datetime(2026, 9, 24, tzinfo=UTC)
    run = PredictionRun(
        run_id=run_id,
        generated_at=now,
        region="india",
        mode=mode,
        feature_run_id="features-20260924T0000Z",
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        published_at=now,
    )
    quality = FeatureQuality(coverage_fraction=0.8, observed_station_count=2)
    rows = [
        PredictionResult(
            run_id=run_id,
            h3_cell=CELL,
            horizon_hours=0.0,
            valid_at=now,
            baseline_pm25=100.0,
            predicted_pm25=current_pm25,
            quality=quality,
            synthetic=synthetic,
        ),
        PredictionResult(
            run_id=run_id,
            h3_cell=CELL,
            horizon_hours=float(HORIZON),
            valid_at=now + timedelta(hours=HORIZON),
            baseline_pm25=100.0,
            predicted_pm25=forecast_pm25,
            quality=quality,
            synthetic=synthetic,
        ),
    ]
    fake_repos.publication.publish(run, rows)
    return run


def _alert_id(**kwargs) -> str:
    return PublishedAlertIdentity(
        run_id=kwargs.get("run_id", RUN_ID),
        h3_cell=kwargs.get("h3_cell", CELL),
        forecast_hours=kwargs.get("forecast_hours", HORIZON),
    ).alert_id


def _v2_client(fake_repos: FakeRepos) -> TestClient:
    """A client whose /api/v2 reads the same in-memory publication the
    incidents do, so the id the web shows is the id the workflow accepts."""
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_prediction_query_service] = lambda: PredictionQueryService(
        fake_repos.publication, native_resolution=RESOLUTION, region="india"
    )
    return TestClient(app)


def _create(client: TestClient, body: dict, headers: dict = POLLUTION):
    return client.post("/api/v1/incidents", headers=headers, json=body)


def _assign(client: TestClient, incident_id: int, assignee: str = "unit-12", headers=POLLUTION, role=None):
    body: dict = {"assignee": assignee}
    if role is not None:
        body["role"] = role
    return client.post(f"/api/v1/incidents/{incident_id}/assign", headers=headers, json=body)


def _transition(client: TestClient, incident_id: int, to_status: str, headers=POLLUTION, role=None):
    body: dict = {"to_status": to_status}
    if role is not None:
        body["role"] = role
    return client.post(
        f"/api/v1/incidents/{incident_id}/transitions", headers=headers, json=body
    )


def _inbox(client: TestClient, role: str, *, only_open: bool = False):
    params: dict = {"role": role}
    if only_open:
        params["only_open"] = "true"
    return client.get("/api/v1/incidents/inbox", params=params)


# --- published alert identity --------------------------------------------


def test_identity_is_deterministic_and_round_trips() -> None:
    first = PublishedAlertIdentity(run_id=RUN_ID, h3_cell=CELL, forecast_hours=HORIZON)
    second = PublishedAlertIdentity(run_id=RUN_ID, h3_cell=CELL, forecast_hours=HORIZON)

    assert first.alert_id == second.alert_id
    assert first.alert_id == f"v2:{RUN_ID}:{CELL}:{HORIZON}"
    assert PublishedAlertIdentity.parse(first.alert_id) == first


def test_identity_depends_on_run_cell_and_horizon() -> None:
    base = PublishedAlertIdentity(run_id=RUN_ID, h3_cell=CELL, forecast_hours=HORIZON)
    other_run = PublishedAlertIdentity(
        run_id="pred-20260924T0600Z-india", h3_cell=CELL, forecast_hours=HORIZON
    )
    other_cell = PublishedAlertIdentity(
        run_id=RUN_ID, h3_cell=h3.latlng_to_cell(LAT + 1, LON, RESOLUTION), forecast_hours=HORIZON
    )
    other_horizon = PublishedAlertIdentity(run_id=RUN_ID, h3_cell=CELL, forecast_hours=3)

    # A different run is a different alert: the severity belongs to the run.
    assert base.alert_id != other_run.alert_id
    assert base.alert_id != other_cell.alert_id
    assert base.alert_id != other_horizon.alert_id


@pytest.mark.parametrize(
    "value",
    [
        "",
        "17",
        "incident-5",
        "v3:pred-1:8861892e0dfffff:6",
        f"v2:{RUN_ID}:{CELL}",
        f"v2:{RUN_ID}:{CELL}:six",
        f"v2:{RUN_ID}:{CELL}:0",
        f"v2:{RUN_ID}:{CELL}:-1",
        f"v2:{RUN_ID}:not-a-cell:6",
        f"v2:: {CELL}:6",
    ],
)
def test_malformed_identities_are_rejected(value: str) -> None:
    with pytest.raises(PublishedAlertIdentityError):
        PublishedAlertIdentity.parse(value)


def test_v2_alerts_endpoint_exposes_the_stable_identity(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """The identity the web reads is the identity the workflow accepts."""
    _publish(fake_repos)
    client = _v2_client(fake_repos)

    response = client.get("/api/v2/alerts", params={"run_id": RUN_ID})

    assert response.status_code == 200
    [alert] = response.json()["data"]
    assert alert["alert_id"] == _alert_id()
    assert alert["h3_cell"] == CELL
    assert alert["severity"] == "critical"
    assert alert["forecast_hours"] == HORIZON


# --- incident creation from a published alert -----------------------------


def test_incident_from_published_alert_records_run_cell_and_severity(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)

    response = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["source_type"] == "published_alert"
    assert data["source_ref"] == _alert_id()
    assert data["source_id"] is None
    assert data["source_synthetic"] is False
    assert data["h3_cell"] == CELL
    assert data["linked_prediction_run_id"] == RUN_ID
    assert data["severity"] == "critical"
    assert data["responder_role"] == "pollution_control"
    assert data["status"] == "reported"
    # The incident is located at the cell centre, like any cell-level alert.
    centre_lat, centre_lon = h3.cell_to_latlng(CELL)
    assert abs(data["latitude"] - centre_lat) < 1e-6
    assert abs(data["longitude"] - centre_lon) < 1e-6
    # The creating actor's jurisdiction is recorded by default.
    assert data["jurisdiction"] == "Delhi"


def test_duplicate_creation_from_the_same_published_alert_is_idempotent(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)
    body = {"source_type": "published_alert", "source_ref": _alert_id()}

    first = _create(api_client, body)
    second = _create(api_client, body)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert len(fake_repos.incidents.incidents) == 1
    # The retry did not append a second creation event.
    incident_id = first.json()["data"]["id"]
    kinds = [event.event_type.value for event in fake_repos.incidents.history(incident_id)]
    assert kinds.count("created") == 1


def test_published_alert_create_with_conflicting_attributes_is_409(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)
    _create(api_client, {"source_type": "published_alert", "source_ref": _alert_id()})

    conflict = _create(
        api_client,
        {
            "source_type": "published_alert",
            "source_ref": _alert_id(),
            "jurisdiction": "Mumbai",
        },
    )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"


def test_unknown_published_alert_is_404(api_client: TestClient, fake_repos: FakeRepos) -> None:
    _publish(fake_repos)

    missing_run = _create(
        api_client,
        {"source_type": "published_alert", "source_ref": _alert_id(run_id="pred-nope")},
    )
    missing_cell = _create(
        api_client,
        {
            "source_type": "published_alert",
            "source_ref": _alert_id(h3_cell=h3.latlng_to_cell(LAT + 1, LON, RESOLUTION)),
        },
    )
    missing_horizon = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id(forecast_hours=3)}
    )

    assert missing_run.status_code == 404
    assert missing_cell.status_code == 404
    assert missing_horizon.status_code == 404
    assert fake_repos.incidents.incidents == []


def test_published_cell_below_the_alert_threshold_is_422(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """The identity names a real published cell, but it is not an alert."""
    _publish(fake_repos, forecast_pm25=60.0)

    response = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    )

    assert response.status_code == 422
    assert fake_repos.incidents.incidents == []


def test_synthetic_published_run_is_flagged_on_the_incident(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """A demo-fallback run's incident must not read as a real-world event."""
    _publish(fake_repos, mode=DataMode.DEMO, synthetic=True)

    response = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    )

    assert response.status_code == 201
    assert response.json()["data"]["source_synthetic"] is True


def test_published_alert_body_must_carry_exactly_one_reference(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)

    with_id = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id(), "source_id": 1}
    )
    without_ref = _create(api_client, {"source_type": "published_alert"})
    ref_on_report = _create(
        api_client, {"source_type": "report", "source_id": 1, "source_ref": _alert_id()}
    )

    assert with_id.status_code == 422
    assert without_ref.status_code == 422
    assert ref_on_report.status_code == 422


# --- the full documented sequence, from a published alert -----------------


def test_published_alert_to_resolution_is_one_reproducible_sequence(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)

    # 1. create from the published alert the web showed
    created = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    )
    assert created.status_code == 201
    incident_id = created.json()["data"]["id"]

    # 2. assign — the incident becomes visible in the responder's simulated inbox
    assigned = _assign(api_client, incident_id)
    assert assigned.status_code == 200
    assert assigned.json()["data"]["status"] == "assigned"

    inbox = _inbox(api_client, "pollution_control")
    assert inbox.status_code == 200
    [item] = inbox.json()["data"]
    assert item["incident"]["id"] == incident_id
    assert item["is_open"] is True
    assert item["delivery"]["simulated"] is True
    assert "no email" in item["delivery"]["notification"]

    # 3. the responder acknowledges, which closes their inbox item
    acknowledged = _transition(api_client, incident_id, "acknowledged")
    assert acknowledged.status_code == 200
    assert _inbox(api_client, "pollution_control", only_open=True).json()["data"] == []
    closed = _inbox(api_client, "pollution_control").json()["data"][0]
    assert closed["is_open"] is False
    assert closed["delivery"]["status"] == "acknowledged"
    assert closed["delivery"]["acknowledged_at"] is not None

    # 4. through to resolution
    for target in ("en_route", "on_scene", "resolved"):
        step = _transition(api_client, incident_id, target)
        assert step.status_code == 200, step.text
    final = api_client.get(f"/api/v1/incidents/{incident_id}").json()["data"]
    assert final["status"] == "resolved"
    assert final["resolved_at"] is not None

    # 5. complete, append-only history
    events = api_client.get(f"/api/v1/incidents/{incident_id}/history").json()["data"]
    assert [event["event_type"] for event in events] == [
        "created",
        "assigned",
        "delivered",
        "transition",
        "transition",
        "transition",
        "transition",
    ]
    assert [event["to_status"] for event in events] == [
        "reported",
        "assigned",
        "assigned",
        "acknowledged",
        "en_route",
        "on_scene",
        "resolved",
    ]
    assert all(event["actor"] == "unit-12" for event in events)
    assert all(event["actor_jurisdiction"] == "Delhi" for event in events)


def test_citizen_report_to_resolution_is_also_reproducible(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """The same sequence from the other source kind: a citizen fire report."""
    report = FireReport(
        h3_cell=CELL,
        latitude=LAT,
        longitude=LON,
        kind=FireKind.CROP_BURNING,
        smoke_intensity=4,
        duration_hours=1.0,
        reported_at=datetime.now(UTC),
    )
    report_id = fake_repos.fire.save(report).id

    created = _create(
        api_client,
        {"source_type": "report", "source_id": report_id},
        headers=CONTROL_ROOM,
    )
    assert created.status_code == 201
    incident_id = created.json()["data"]["id"]
    assert created.json()["data"]["responder_role"] == "fire_department"

    assigned = _assign(api_client, incident_id, assignee="engine-7", headers=FIRE)
    assert assigned.status_code == 200
    fire_inbox = _inbox(api_client, "fire_department").json()["data"]
    assert [item["incident"]["id"] for item in fire_inbox] == [incident_id]

    for target in ("acknowledged", "en_route", "on_scene", "resolved"):
        step = _transition(api_client, incident_id, target, headers=FIRE)
        assert step.status_code == 200, step.text

    events = api_client.get(f"/api/v1/incidents/{incident_id}/history").json()["data"]
    assert [event["event_type"] for event in events] == [
        "created",
        "assigned",
        "delivered",
        "transition",
        "transition",
        "transition",
        "transition",
    ]


# --- the simulated inbox --------------------------------------------------


def test_inbox_is_addressed_to_the_intended_role_only(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)
    incident_id = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    ).json()["data"]["id"]
    _assign(api_client, incident_id)

    assert len(_inbox(api_client, "pollution_control").json()["data"]) == 1
    # A pollution-control assignment is invisible to the fire department.
    assert _inbox(api_client, "fire_department").json()["data"] == []


def test_reassignment_adds_an_inbox_item_without_losing_history(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)
    incident_id = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    ).json()["data"]["id"]
    _assign(api_client, incident_id, assignee="unit-12")
    _assign(api_client, incident_id, assignee="unit-15")

    deliveries = api_client.get(f"/api/v1/incidents/{incident_id}/deliveries").json()["data"]
    assert len(deliveries) == 2
    assert [item["assignee"] for item in deliveries] == ["unit-12", "unit-15"]
    assert all(item["simulated"] is True for item in deliveries)

    events = api_client.get(f"/api/v1/incidents/{incident_id}/history").json()["data"]
    assert [event["event_type"] for event in events] == [
        "created",
        "assigned",
        "delivered",
        "reassigned",
        "delivered",
    ]


def test_every_delivery_is_simulated_and_says_so(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """No delivery can claim to be a real dispatch."""
    _publish(fake_repos)
    incident_id = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    ).json()["data"]["id"]
    _assign(api_client, incident_id)

    stored = fake_repos.incident_deliveries.list_for_incident(incident_id)
    assert stored
    assert all(item.simulated is True for item in stored)
    assert all(
        item.status in (IncidentDeliveryStatus.SIMULATED, IncidentDeliveryStatus.ACKNOWLEDGED)
        for item in stored
    )
    payload = api_client.get(f"/api/v1/incidents/{incident_id}/deliveries").json()["data"]
    assert all(item["simulated"] is True for item in payload)
    assert all(item["notification"].startswith("none") for item in payload)


def test_inbox_of_an_unknown_role_is_rejected(api_client: TestClient) -> None:
    assert _inbox(api_client, "mayor").status_code == 422


# --- actor role and jurisdiction enforcement ------------------------------


def test_actor_role_must_match_the_incident(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """A fire-department actor cannot act on a pollution-control incident."""
    _publish(fake_repos)
    incident_id = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    ).json()["data"]["id"]

    response = _assign(api_client, incident_id, headers=FIRE)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_mismatch"


def test_body_role_cannot_widen_the_actor_authority(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """A pollution-control actor may not act by *claiming* the fire role."""
    _publish(fake_repos)
    incident_id = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    ).json()["data"]["id"]

    assign = _assign(api_client, incident_id, role="fire_department")
    transition = _transition(api_client, incident_id, "acknowledged", role="fire_department")

    assert assign.status_code == 403
    assert transition.status_code == 403


def test_actor_outside_the_incident_jurisdiction_is_forbidden(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    """Role is checked first, then jurisdiction — and holding the right role in
    the wrong jurisdiction is still refused."""
    # A fire incident classified as Mumbai. The creating control-room actor is
    # allowed to say so (that is classification, not response).
    report = FireReport(
        h3_cell=CELL,
        latitude=LAT,
        longitude=LON,
        kind=FireKind.FOREST_FIRE,
        smoke_intensity=5,
        duration_hours=2.0,
        reported_at=datetime.now(UTC),
    )
    report_id = fake_repos.fire.save(report).id
    incident_id = _create(
        api_client,
        {"source_type": "report", "source_id": report_id, "jurisdiction": "Mumbai"},
        headers=CONTROL_ROOM,
    ).json()["data"]["id"]

    delhi_fire = _assign(api_client, incident_id, headers=FIRE)
    delhi_pollution = _assign(api_client, incident_id, headers=POLLUTION)
    mumbai_fire = _assign(api_client, incident_id, headers=FIRE_MUMBAI, role="fire_department")

    # Right role, wrong jurisdiction.
    assert delhi_fire.status_code == 403
    assert delhi_fire.json()["error"]["code"] == "jurisdiction_mismatch"
    # Wrong role is reported as such.
    assert delhi_pollution.status_code == 403
    assert delhi_pollution.json()["error"]["code"] == "role_mismatch"
    # The responder who holds that jurisdiction may act.
    assert mumbai_fire.status_code == 200


def test_published_alert_in_another_jurisdiction_is_not_actionable(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)
    incident_id = _create(
        api_client,
        {
            "source_type": "published_alert",
            "source_ref": _alert_id(),
            "jurisdiction": "Mumbai",
        },
    ).json()["data"]["id"]

    response = _assign(api_client, incident_id, headers=POLLUTION)  # pollution, Delhi

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "jurisdiction_mismatch"


def test_transition_is_jurisdiction_checked_too(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    report = FireReport(
        h3_cell=CELL,
        latitude=LAT,
        longitude=LON,
        kind=FireKind.FOREST_FIRE,
        smoke_intensity=5,
        duration_hours=2.0,
        reported_at=datetime.now(UTC),
    )
    report_id = fake_repos.fire.save(report).id
    incident_id = _create(
        api_client,
        {"source_type": "report", "source_id": report_id, "jurisdiction": "Mumbai"},
        headers=CONTROL_ROOM,
    ).json()["data"]["id"]
    _assign(api_client, incident_id, assignee="engine-9", headers=FIRE_MUMBAI)

    response = _transition(api_client, incident_id, "acknowledged", headers=FIRE)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "jurisdiction_mismatch"


def test_unnamed_or_unknown_actor_is_401(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)
    body = {"source_type": "published_alert", "source_ref": _alert_id()}

    no_actor = _create(api_client, body, headers={"X-Simulator-Key": KEY})
    unknown_actor = _create(
        api_client, body, headers={"X-Simulator-Key": KEY, "X-Actor-Id": "who-dat"}
    )

    assert no_actor.status_code == 401
    assert unknown_actor.status_code == 401
    assert fake_repos.incidents.incidents == []


def test_writes_are_refused_when_no_actors_are_configured(
    api_client: TestClient, fake_repos: FakeRepos, monkeypatch
) -> None:
    """An empty registry means nobody can act: off, not unprotected."""
    _publish(fake_repos)
    monkeypatch.setattr(get_settings(), "simulator_actors", "")

    response = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "simulator_disabled"


# --- invalid transitions --------------------------------------------------


def test_illegal_transition_is_409_with_a_specific_code(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)
    incident_id = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    ).json()["data"]["id"]

    skip = _transition(api_client, incident_id, "on_scene")  # reported -> on_scene
    assert skip.status_code == 409
    assert skip.json()["error"]["code"] == "invalid_transition"

    _assign(api_client, incident_id)
    for target in ("acknowledged", "en_route", "on_scene", "resolved"):
        _transition(api_client, incident_id, target)
    after_resolved = _transition(api_client, incident_id, "cancelled")

    assert after_resolved.status_code == 409
    assert after_resolved.json()["error"]["code"] == "invalid_transition"
    # The refused transition left the record untouched.
    assert api_client.get(f"/api/v1/incidents/{incident_id}").json()["data"]["status"] == "resolved"


def test_generic_transition_to_assigned_is_still_refused(
    api_client: TestClient, fake_repos: FakeRepos
) -> None:
    _publish(fake_repos)
    incident_id = _create(
        api_client, {"source_type": "published_alert", "source_ref": _alert_id()}
    ).json()["data"]["id"]

    response = _transition(api_client, incident_id, "assigned")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "use_assign_endpoint"


# --- the actor registry ---------------------------------------------------


def test_actor_registry_parsing() -> None:
    actors = parse_simulator_actors("a:pollution_control:Delhi, b:fire_department , c:pollution_control")

    assert actors["a"].role is ResponderRole.POLLUTION_CONTROL
    assert actors["a"].jurisdiction == "Delhi"
    assert actors["b"].role is ResponderRole.FIRE_DEPARTMENT
    # No jurisdiction means an actor that is not scoped to one.
    assert actors["b"].jurisdiction is None
    assert actors["c"].jurisdiction is None


@pytest.mark.parametrize(
    "spec",
    ["a", "a:mayor", "a:pollution_control:Delhi:extra", "a:pollution_control,a:pollution_control"],
)
def test_malformed_actor_registry_is_a_configuration_error(spec: str) -> None:
    with pytest.raises(SimulatorDisabledError):
        parse_simulator_actors(spec)
