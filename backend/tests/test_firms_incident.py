"""FIRMS rows can enter the responder workflow only as recent review candidates."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import h3
import pytest

from app.domain.environmental_observations import FireHotspot
from app.domain.incidents import (
    IncidentActor,
    IncidentSourceType,
    ResponderRole,
)
from app.services.incidents import IncidentService, RoleMismatchError, SourceNotEligibleError


class _IncidentRepository:
    def __init__(self) -> None:
        self.created = []

    def get_by_source(self, source_type, *, source_id=None, source_ref=None):
        return None

    def create(self, incident, event):
        stored = replace(incident, id=1)
        self.created.append(stored)
        return stored


class _FireHotspots:
    def __init__(self, detection: FireHotspot | None) -> None:
        self.detection = detection

    def get_by_detection_id(self, detection_id: str, *, available_by: datetime):
        if (
            self.detection is None
            or self.detection.detection_id != detection_id
            or self.detection.available_at > available_by
        ):
            return None
        return self.detection


def _detection(now: datetime, *, age_hours: float = 1, confidence: str = "high") -> FireHotspot:
    acquired = now - timedelta(hours=age_hours)
    return FireHotspot(
        detection_id="f" * 64,
        h3_cell=h3.latlng_to_cell(28.6, 77.2, 8),
        dataset_id="firms:run-1",
        ingestion_run_id="run-1",
        source="nasa-firms",
        product="VIIRS_NOAA21_NRT",
        product_version="2.0NRT",
        satellite="NOAA-21",
        instrument="VIIRS",
        latitude=28.6,
        longitude=77.2,
        acquired_at=acquired,
        available_at=acquired + timedelta(minutes=10),
        frp_mw=18.4,
        confidence_raw=confidence,
        confidence_class=confidence,
    )


def _service(detection: FireHotspot | None) -> tuple[IncidentService, _IncidentRepository]:
    incidents = _IncidentRepository()
    service = IncidentService(
        incident_repository=incidents,
        alert_repository=None,
        report_repository=None,
        delivery_repository=None,
        published_alerts=None,
        fire_hotspots=_FireHotspots(detection),
    )
    return service, incidents


def _create(service: IncidentService, actor: IncidentActor, now: datetime):
    return service.create_from_source(
        source_type=IncidentSourceType.FIRMS_DETECTION,
        actor=actor,
        source_ref="f" * 64,
        severity=None,
        jurisdiction=None,
        latitude=None,
        longitude=None,
        linked_prediction_run_id=None,
        evidence_report_ids=None,
        now=now,
    )


def test_firms_incident_uses_persisted_location_and_safe_review_severity(monkeypatch) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "app.services.incidents.get_settings",
        lambda: SimpleNamespace(firms_stale_after_hours=6),
    )
    service, repository = _service(_detection(now, confidence="high"))

    outcome = _create(
        service,
        IncidentActor("fire-unit-1", ResponderRole.FIRE_DEPARTMENT, "Delhi"),
        now,
    )

    assert outcome.created is True
    assert outcome.incident.severity == "warning"
    assert outcome.incident.responder_role is ResponderRole.FIRE_DEPARTMENT
    assert outcome.incident.source_ref == "f" * 64
    assert outcome.incident.latitude == 28.6
    assert len(repository.created) == 1


def test_low_confidence_firms_detection_defaults_to_watch(monkeypatch) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "app.services.incidents.get_settings",
        lambda: SimpleNamespace(firms_stale_after_hours=6),
    )
    service, _ = _service(_detection(now, confidence="low"))

    outcome = _create(
        service,
        IncidentActor("fire-unit-1", ResponderRole.FIRE_DEPARTMENT),
        now,
    )

    assert outcome.incident.severity == "watch"


def test_stale_firms_detection_cannot_open_new_incident(monkeypatch) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "app.services.incidents.get_settings",
        lambda: SimpleNamespace(firms_stale_after_hours=6),
    )
    service, repository = _service(_detection(now, age_hours=7))

    with pytest.raises(SourceNotEligibleError, match="stale"):
        _create(
            service,
            IncidentActor("fire-unit-1", ResponderRole.FIRE_DEPARTMENT),
            now,
        )
    assert repository.created == []


def test_non_fire_responder_cannot_open_firms_candidate(monkeypatch) -> None:
    now = datetime.now(UTC)
    service, repository = _service(_detection(now))

    with pytest.raises(RoleMismatchError, match="fire-department"):
        _create(
            service,
            IncidentActor("control-room", ResponderRole.POLLUTION_CONTROL),
            now,
        )
    assert repository.created == []
