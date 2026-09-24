"""Business logic for the incident workflow (the fire-department simulator).

Owns source eligibility, dedupe, the state machine, assignment, and the
append-only history. Permissions (the simulator key and the role check) are
enforced here too, so the rules live in one place rather than only in routes.

See docs/api/incidents.md.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from app.core.config import get_settings
from app.domain.h3_grid import cell_center
from app.domain.incidents import (
    InvalidIncidentError,
    InvalidTransitionError,
    Incident,
    IncidentEvent,
    IncidentEventType,
    IncidentSourceType,
    IncidentStatus,
    ResponderRole,
    is_transition_allowed,
)
from app.domain.repositories import (
    AlertRepository,
    FireReportRepository,
    IncidentRepository,
)
from app.domain.types import Alert, AlertSeverity, FireKind, FireReport


class IncidentNotFoundError(LookupError):
    """No incident (or source) with the given id."""


class SourceNotEligibleError(ValueError):
    """The referenced alert/report cannot produce an incident."""


class RoleMismatchError(PermissionError):
    """The acting role may not act on this incident."""


class UseAssignEndpointError(ValueError):
    """A generic transition was asked to enter `assigned`; use /assign."""


class IncidentConflictError(ValueError):
    """Same source, but the requested attributes differ from the stored one."""


class SimulatorDisabledError(RuntimeError):
    """No simulator key is configured, so writes are off."""


# Fire kinds that form a fire-department incident. `other` is pollution-only.
_FIRE_KINDS = frozenset(
    {
        FireKind.BUILDING_FIRE,
        FireKind.INDUSTRIAL_FIRE,
        FireKind.FOREST_FIRE,
        FireKind.CROP_BURNING,
    }
)
# Alert severities eligible to open an incident. `watch` is informational.
_ELIGIBLE_ALERT_SEVERITIES = frozenset({AlertSeverity.WARNING, AlertSeverity.CRITICAL})

_ASSIGNABLE_FROM = frozenset({IncidentStatus.REPORTED, IncidentStatus.ASSIGNED})


@dataclass(frozen=True)
class CreateOutcome:
    incident: Incident
    created: bool


class IncidentService:
    def __init__(
        self,
        *,
        incident_repository: IncidentRepository,
        alert_repository: AlertRepository,
        report_repository: FireReportRepository,
    ) -> None:
        self._incidents = incident_repository
        self._alerts = alert_repository
        self._reports = report_repository

    # -- permissions --------------------------------------------------------

    def require_simulator_enabled(self) -> None:
        if get_settings().simulator_api_key is None:
            raise SimulatorDisabledError("simulator writes are not configured")

    # -- creation -----------------------------------------------------------

    def create_from_source(
        self,
        *,
        source_type: IncidentSourceType,
        source_id: int,
        severity: str | None,
        jurisdiction: str | None,
        latitude: float | None,
        longitude: float | None,
        linked_prediction_run_id: str | None,
        evidence_report_ids: list[int] | None,
        now: datetime,
    ) -> CreateOutcome:
        existing = self._incidents.get_by_source(source_type, source_id)
        if existing is not None:
            self._assert_same_attributes(
                existing,
                severity=severity,
                jurisdiction=jurisdiction,
                linked_prediction_run_id=linked_prediction_run_id,
            )
            return CreateOutcome(existing, created=False)

        role, default_severity, source_lat, source_lon, h3_cell = self._resolve_source(
            source_type, source_id
        )
        effective_lat = latitude if latitude is not None else source_lat
        effective_lon = longitude if longitude is not None else source_lon
        if not -90 <= effective_lat <= 90 or not -180 <= effective_lon <= 180:
            raise InvalidIncidentError("incident coordinates out of range")

        incident = Incident(
            source_type=source_type,
            source_id=source_id,
            status=IncidentStatus.REPORTED,
            responder_role=role,
            severity=severity or default_severity,
            jurisdiction=jurisdiction,
            latitude=effective_lat,
            longitude=effective_lon,
            h3_cell=h3_cell,
            linked_prediction_run_id=linked_prediction_run_id,
            evidence_report_ids=tuple(evidence_report_ids or ()),
            created_at=now,
            updated_at=now,
        )
        event = IncidentEvent(
            incident_id=0,  # replaced by the repository with the stored id
            event_type=IncidentEventType.CREATED,
            to_status=IncidentStatus.REPORTED,
            role=role,
            created_at=now,
        )
        stored = self._incidents.create(incident, event)
        # If a racing request won the unique constraint between our check and
        # the insert, the repository returns that existing row; tell it apart
        # from ours by stamps, then apply the attribute-conflict check either
        # way.
        created = (
            existing is None
            and stored.created_at == incident.created_at
            and stored.source_key == (source_type, source_id)
        )
        if not created:
            self._assert_same_attributes(
                stored,
                severity=severity,
                jurisdiction=jurisdiction,
                linked_prediction_run_id=linked_prediction_run_id,
            )
        return CreateOutcome(stored, created=created)

    def _resolve_source(
        self, source_type: IncidentSourceType, source_id: int
    ) -> tuple[ResponderRole, str, float, float, str | None]:
        if source_type is IncidentSourceType.ALERT:
            alert = self._alert_by_id(source_id)
            if alert is None:
                raise IncidentNotFoundError(f"alert {source_id} does not exist")
            if alert.severity not in _ELIGIBLE_ALERT_SEVERITIES:
                raise SourceNotEligibleError(
                    f"alert severity {alert.severity.value!r} is not eligible for an incident"
                )
            # An alert carries only an H3 cell, not a point. Use the cell's
            # centre as the incident's location, so the incident still has a
            # concrete coordinate even when created from a cell-level alert.
            alert_lat, alert_lon = cell_center(alert.h3_cell)
            # An alert is a pollution condition; where a fire component exists
            # the incident is handled by the fire department, but a bare
            # pollution alert defaults to pollution control.
            return (
                ResponderRole.POLLUTION_CONTROL,
                alert.severity.value,
                alert_lat,
                alert_lon,
                alert.h3_cell,
            )

        report = self._report_by_id(source_id)
        if report is None:
            raise IncidentNotFoundError(f"report {source_id} does not exist")
        role = (
            ResponderRole.FIRE_DEPARTMENT
            if report.kind in _FIRE_KINDS
            else ResponderRole.POLLUTION_CONTROL
        )
        default_severity = "warning" if role is ResponderRole.FIRE_DEPARTMENT else "watch"
        return role, default_severity, report.latitude, report.longitude, report.h3_cell

    def _assert_same_attributes(
        self,
        existing: Incident,
        *,
        severity: str | None,
        jurisdiction: str | None,
        linked_prediction_run_id: str | None,
    ) -> None:
        if severity is not None and severity != existing.severity:
            raise IncidentConflictError("an incident already exists for this source")
        if jurisdiction is not None and jurisdiction != existing.jurisdiction:
            raise IncidentConflictError("an incident already exists for this source")
        if (
            linked_prediction_run_id is not None
            and linked_prediction_run_id != existing.linked_prediction_run_id
        ):
            raise IncidentConflictError("an incident already exists for this source")

    # -- assignment ---------------------------------------------------------

    def assign(
        self,
        *,
        incident_id: int,
        role: ResponderRole,
        assignee: str,
        actor: str | None,
        now: datetime,
    ) -> Incident:
        incident = self._require(incident_id)
        if role is not incident.responder_role:
            raise RoleMismatchError(
                f"incident {incident_id} is handled by {incident.responder_role.value}"
            )
        if incident.status not in _ASSIGNABLE_FROM:
            raise InvalidTransitionError(
                f"cannot assign an incident in status {incident.status.value!r}"
            )
        if not assignee.strip():
            raise InvalidIncidentError("assignee must not be empty")

        was_assigned = incident.status is IncidentStatus.ASSIGNED
        updated = replace(
            incident,
            status=IncidentStatus.ASSIGNED,
            assignee=assignee,
            updated_at=now,
        )
        event = IncidentEvent(
            incident_id=incident_id,
            event_type=(
                IncidentEventType.REASSIGNED if was_assigned else IncidentEventType.ASSIGNED
            ),
            from_status=incident.status,
            to_status=IncidentStatus.ASSIGNED,
            role=role,
            actor=actor or assignee,
            created_at=now,
        )
        return self._incidents.update(updated, event)

    # -- transitions --------------------------------------------------------

    def transition(
        self,
        *,
        incident_id: int,
        to_status: IncidentStatus,
        role: ResponderRole,
        actor: str | None,
        note: str | None,
        now: datetime,
    ) -> Incident:
        incident = self._require(incident_id)
        if role is not incident.responder_role:
            raise RoleMismatchError(
                f"incident {incident_id} is handled by {incident.responder_role.value}"
            )
        # Idempotent retry: already in the requested status is a no-op, not an
        # error, so a retried request does not double-apply.
        if incident.status is to_status:
            return incident
        if to_status is IncidentStatus.ASSIGNED:
            raise UseAssignEndpointError("use POST /incidents/{id}/assign to assign")
        if not is_transition_allowed(incident.status, to_status):
            raise InvalidTransitionError(
                f"{incident.status.value!r} -> {to_status.value!r} is not allowed"
            )

        resolved_at = incident.resolved_at
        if to_status.is_terminal:
            resolved_at = now
        updated = replace(
            incident,
            status=to_status,
            updated_at=now,
            resolved_at=resolved_at,
        )
        event = IncidentEvent(
            incident_id=incident_id,
            event_type=IncidentEventType.TRANSITION,
            from_status=incident.status,
            to_status=to_status,
            role=role,
            actor=actor,
            note=note,
            created_at=now,
        )
        return self._incidents.update(updated, event)

    # -- reads --------------------------------------------------------------

    def get(self, incident_id: int) -> Incident:
        return self._require(incident_id)

    def list(
        self,
        *,
        status: IncidentStatus | None = None,
        role: ResponderRole | None = None,
    ) -> list[Incident]:
        return self._incidents.list(status=status, role=role)

    def history(self, incident_id: int) -> list[IncidentEvent]:
        self._require(incident_id)
        return self._incidents.history(incident_id)

    # -- helpers ------------------------------------------------------------

    def _require(self, incident_id: int) -> Incident:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise IncidentNotFoundError(f"incident {incident_id} does not exist")
        return incident

    def _alert_by_id(self, alert_id: int) -> Alert | None:
        # AlertRepository exposes list_active(since); search the active window
        # (an incident is only created for a still-active alert).
        from datetime import timedelta

        since = datetime.now(UTC) - timedelta(hours=get_settings().alert_active_lookback_hours)
        for alert in self._alerts.list_active(since=since):
            if alert.id == alert_id:
                return alert
        return None

    def _report_by_id(self, report_id: int) -> FireReport | None:
        from datetime import timedelta

        since = datetime.now(UTC) - timedelta(hours=get_settings().fire_report_max_age_hours)
        for report in self._reports.list_active(since=since):
            if report.id == report_id:
                return report
        return None
