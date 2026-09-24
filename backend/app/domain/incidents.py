"""Domain types for the incident workflow (the fire-department simulator).

An incident is an operational record created from an eligible published v2
alert, persisted fire alert, or citizen report, progressed through response
states by an authenticated responder. It is separate from the source it came
from: the source can exist without an incident, and the incident owns its
lifecycle, assignment, jurisdiction, and append-only history.

Two rules are load-bearing here:

* **Writes carry an identity, not just a key.** `IncidentActor` binds a write
  to a role *and* a jurisdiction that the server resolved, so a shared
  simulator key cannot be used to act as any authority anywhere.
* **Delivery is simulated.** `IncidentDelivery` records that an assignment
  became visible in a responder's inbox. Nothing is ever sent to a real person
  or system; the type and its table both refuse to represent that.

See docs/api/incidents.md for the API contract and the transition table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.types import _require_utc


class IncidentSourceType(StrEnum):
    """What an incident was created from.

    The pair (source_type, source_id) — or, for a published alert,
    (source_type, source_ref) — is unique across incidents, which is what makes
    creation idempotent.
    """

    ALERT = "alert"
    REPORT = "report"
    PUBLISHED_ALERT = "published_alert"

    @property
    def uses_ref(self) -> bool:
        """Whether this source is named by a string reference rather than a row
        id. A published alert has no row of its own: it is a run + cell +
        horizon in the published prediction space."""
        return self is IncidentSourceType.PUBLISHED_ALERT


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
    # A newly assigned incident became visible in the simulated responder
    # inbox. Appended to history so the operational trail shows that the
    # handoff was made visible — the delivery itself is simulated.
    DELIVERED = "delivered"


class IncidentDeliveryStatus(StrEnum):
    """Lifecycle of a *simulated* delivery of an incident to a responder.

    There is deliberately no "sent" state: this workflow never contacts anyone.
    `SIMULATED` means "recorded as handed to the responder's inbox", and
    `ACKNOWLEDGED` means the responder acknowledged the incident, which closes
    the loop. The table that stores these rows has a CHECK constraint forcing
    `simulated = true`, so a real notification cannot be recorded here by
    accident.
    """

    SIMULATED = "simulated"
    ACKNOWLEDGED = "acknowledged"


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
    # The jurisdiction the actor acted under, recorded so the history shows
    # *which* authority made each change, not just which role.
    actor_jurisdiction: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        if self.event_type is IncidentEventType.TRANSITION and (
            self.from_status is None or self.to_status is None
        ):
            raise ValueError("a transition event must record both from_status and to_status")


@dataclass(frozen=True, slots=True)
class IncidentActor:
    """An authenticated responder identity making a write.

    The role and jurisdiction are *not* taken from the request body: they come
    from the server-side actor registry, so one shared simulator key cannot be
    used to act as any role in any jurisdiction. `jurisdiction` is the area the
    actor is allowed to act in; `None` means the actor is not scoped to one
    (a control-room actor, e.g. the one that creates incidents).
    """

    actor_id: str
    role: ResponderRole
    jurisdiction: str | None = None

    def __post_init__(self) -> None:
        if not self.actor_id or not self.actor_id.strip():
            raise ValueError("actor_id must not be empty")
        if len(self.actor_id) > 120:
            raise ValueError("actor_id must be at most 120 characters")
        if self.jurisdiction is not None and not self.jurisdiction.strip():
            raise ValueError("actor jurisdiction must not be blank")


@dataclass(frozen=True, slots=True)
class IncidentDelivery:
    """A simulated hand-off of an incident to a responder role's inbox.

    `simulated` is always True — the type will not represent a real dispatch,
    and the database enforces it too. No notification is ever sent by this
    system; the row exists so an assigned incident is *visible to the intended
    responder role* in the simulator, and so the acknowledgement loop is
    auditable.
    """

    incident_id: int
    audience_role: ResponderRole
    status: IncidentDeliveryStatus
    simulated_at: datetime
    assignee: str | None = None
    acknowledged_at: datetime | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.simulated_at, "simulated_at")
        if self.acknowledged_at is not None:
            _require_utc(self.acknowledged_at, "acknowledged_at")
            if self.acknowledged_at < self.simulated_at:
                raise ValueError("acknowledged_at must not precede simulated_at")
        if self.status is IncidentDeliveryStatus.ACKNOWLEDGED and self.acknowledged_at is None:
            raise ValueError("an acknowledged delivery must record acknowledged_at")

    @property
    def simulated(self) -> bool:
        """Always True. Kept explicit in the API response so a consumer cannot
        mistake this for a real dispatch."""
        return True


@dataclass(frozen=True, slots=True)
class Incident:
    """An operational incident record."""

    source_type: IncidentSourceType
    status: IncidentStatus
    responder_role: ResponderRole
    severity: str
    latitude: float
    longitude: float
    created_at: datetime
    updated_at: datetime
    # Exactly one of source_id / source_ref identifies the source, decided by
    # source_type (see IncidentSourceType.uses_ref).
    source_id: int | None = None
    source_ref: str | None = None
    # True when the source was a synthetic/demo published run, so a responder
    # reading the incident cannot mistake a fallback run for a real forecast.
    source_synthetic: bool = False
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
        # The source must be identified exactly one way, and the way must match
        # the source type: a published alert is a ref, everything else a row id.
        if self.source_type.uses_ref:
            if self.source_ref is None or not self.source_ref.strip():
                raise ValueError("a published-alert incident requires source_ref")
            if self.source_id is not None:
                raise ValueError("a published-alert incident must not carry source_id")
        else:
            if self.source_id is None or self.source_id < 1:
                raise ValueError(f"a {self.source_type.value} incident requires source_id")
            if self.source_ref is not None:
                raise ValueError(f"a {self.source_type.value} incident must not carry source_ref")

    @property
    def source_key(self) -> tuple[IncidentSourceType, int | str]:
        """The idempotency key: the row id for alert/report sources, the
        published-alert reference otherwise."""
        if self.source_type.uses_ref:
            return (self.source_type, self.source_ref or "")
        return (self.source_type, self.source_id)
