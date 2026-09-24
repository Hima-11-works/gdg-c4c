"""Domain types for the incident workflow (the fire-department simulator).

An incident is an operational record created from an eligible fire alert or
citizen report, progressed through response states by a responding role. It is
separate from the alert/report it came from: the source can exist without an
incident, and the incident owns its lifecycle, assignment, jurisdiction, and
append-only history.

See docs/api/incidents.md for the API contract and the transition table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.types import _require_utc


class IncidentSourceType(StrEnum):
    """What an incident was created from. The pair (source_type, source_id)
    is unique across incidents, which is what makes creation idempotent."""

    ALERT = "alert"
    REPORT = "report"


class IncidentStatus(StrEnum):
    REPORTED = "reported"
    ASSIGNED = "assigned"
    ACKNOWLEDGED = "acknowledged"
    EN_ROUTE = "en_route"
    ON_SCENE = "on_scene"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (IncidentStatus.RESOLVED, IncidentStatus.CANCELLED)


class ResponderRole(StrEnum):
    """Who may act on an incident. Fire incidents go to the fire department;
    pollution-only incidents go to pollution control, and the two are kept
    apart — a pollution-control role may not act on a fire incident."""

    FIRE_DEPARTMENT = "fire_department"
    POLLUTION_CONTROL = "pollution_control"


class IncidentEventType(StrEnum):
    CREATED = "created"
    ASSIGNED = "assigned"
    REASSIGNED = "reassigned"
    TRANSITION = "transition"


# The complete transition table. Anything not listed here is rejected, which
# includes every transition out of a terminal state.
#
# `assigned` is reachable only via the assign endpoint, so it appears as a
# target of `reported` here purely so the table is a complete description of
# the state machine; the service refuses a generic transition to `assigned`.
ALLOWED_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.REPORTED: frozenset(
        {IncidentStatus.ASSIGNED, IncidentStatus.CANCELLED}
    ),
    IncidentStatus.ASSIGNED: frozenset(
        {IncidentStatus.ACKNOWLEDGED, IncidentStatus.CANCELLED}
    ),
    IncidentStatus.ACKNOWLEDGED: frozenset(
        {IncidentStatus.EN_ROUTE, IncidentStatus.CANCELLED}
    ),
    IncidentStatus.EN_ROUTE: frozenset(
        {IncidentStatus.ON_SCENE, IncidentStatus.CANCELLED}
    ),
    IncidentStatus.ON_SCENE: frozenset(
        {IncidentStatus.RESOLVED, IncidentStatus.CANCELLED}
    ),
    IncidentStatus.RESOLVED: frozenset(),
    IncidentStatus.CANCELLED: frozenset(),
}


def is_transition_allowed(
    from_status: IncidentStatus, to_status: IncidentStatus
) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


class InvalidTransitionError(ValueError):
    """Raised for a transition the state machine does not allow."""


class InvalidIncidentError(ValueError):
    """Raised for an invalid incident payload or source."""


@dataclass(frozen=True, slots=True)
class IncidentEvent:
    """One append-only history row. History is never edited or deleted."""

    incident_id: int
    event_type: IncidentEventType
    created_at: datetime
    from_status: IncidentStatus | None = None
    to_status: IncidentStatus | None = None
    role: ResponderRole | None = None
    actor: str | None = None
    note: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        if self.event_type is IncidentEventType.TRANSITION and (
            self.from_status is None or self.to_status is None
        ):
            raise ValueError("a transition event must record both from_status and to_status")


@dataclass(frozen=True, slots=True)
class Incident:
    """An operational incident record."""

    source_type: IncidentSourceType
    source_id: int
    status: IncidentStatus
    responder_role: ResponderRole
    severity: str
    latitude: float
    longitude: float
    created_at: datetime
    updated_at: datetime
    jurisdiction: str | None = None
    linked_prediction_run_id: str | None = None
    evidence_report_ids: tuple[int, ...] = ()
    assignee: str | None = None
    h3_cell: str | None = None
    resolved_at: datetime | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.resolved_at is not None:
            _require_utc(self.resolved_at, "resolved_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude out of range: {self.longitude}")
        if not self.severity:
            raise ValueError("severity must not be empty")

    @property
    def source_key(self) -> tuple[IncidentSourceType, int]:
        return (self.source_type, self.source_id)
