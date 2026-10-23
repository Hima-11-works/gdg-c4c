"""Business logic for the incident workflow (the fire-department simulator).

Owns source eligibility, dedupe, the state machine, assignment, the simulated
responder inbox, and the append-only history.

Three things are enforced here rather than in the routes, so every entry point
gets them:

* **Actor identity.** Writes carry an `IncidentActor` whose role and
  jurisdiction were resolved server-side from the configured actor registry. A
  valid simulator key alone is not enough to act as an authority, and a request
  body cannot claim a role the actor does not hold.
* **Jurisdiction.** An actor scoped to a jurisdiction may only assign and
  transition incidents inside it.
* **Legal transitions.** `ALLOWED_TRANSITIONS` is the only source of truth.

Delivery is *simulated*: assigning an incident writes an `IncidentDelivery`
row, which makes the incident visible in the addressed role's inbox. No
notification, email, webhook, or SMS is sent by this system.

See docs/api/incidents.md.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from app.core.config import get_settings
from app.domain.environmental_observations import FireHotspot
from app.domain.features import DataMode
from app.domain.h3_grid import cell_center
from app.domain.incidents import (
    Incident,
    IncidentActor,
    IncidentDelivery,
    IncidentDeliveryStatus,
    IncidentEvent,
    IncidentEventType,
    IncidentSourceType,
    IncidentStatus,
    IncidentWebhookDelivery,
    IncidentWebhookStatus,
    InvalidIncidentError,
    InvalidTransitionError,
    ResponderRole,
    is_transition_allowed,
)
from app.domain.published_alerts import PublishedAlertIdentityError
from app.domain.repositories import (
    AlertRepository,
    FireHotspotRepository,
    FireReportRepository,
    IncidentDeliveryRepository,
    IncidentRepository,
    IncidentWebhookDeliveryRepository,
)
from app.domain.types import Alert, AlertSeverity, FireKind, FireReport
from app.services.hotspot_detection import HotspotScanStore
from app.services.published_alerts import (
    PublishedAlert,
    PublishedAlertNotEligibleError,
    PublishedAlertNotFoundError,
    PublishedAlertService,
)


class IncidentNotFoundError(LookupError):
    """No incident (or source) with the given id."""


class SourceNotEligibleError(ValueError):
    """The referenced alert/report cannot produce an incident."""


class RoleMismatchError(PermissionError):
    """The acting role may not act on this incident."""


class JurisdictionMismatchError(PermissionError):
    """The acting jurisdiction may not act on this incident."""


class ActorNotPermittedError(PermissionError):
    """The authenticated actor is not allowed to perform this write."""


class UseAssignEndpointError(ValueError):
    """A generic transition was asked to enter `assigned`; use /assign."""


class IncidentConflictError(ValueError):
    """Same source, but the requested attributes differ from the stored one."""


class SimulatorDisabledError(RuntimeError):
    """No simulator key or actor registry is configured, so writes are off."""


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


@dataclass(frozen=True)
class InboxItem:
    """One line of a responder role's simulated inbox."""

    delivery: IncidentDelivery
    incident: Incident

    @property
    def is_open(self) -> bool:
        return self.delivery.status is IncidentDeliveryStatus.SIMULATED


@dataclass(frozen=True)
class _PublishedSource:
    """The parts of a resolved published alert an incident records."""

    h3_cell: str
    run_id: str
    synthetic: bool


def parse_simulator_actors(spec: str) -> dict[str, IncidentActor]:
    """Parse the `SIMULATOR_ACTORS` registry.

    Format: comma-separated `actor_id:role[:jurisdiction]` entries, e.g.
    ``unit-12:pollution_control:Delhi,control-room:pollution_control``.

    The registry is the authority on who exists: an `X-Actor-Id` header is
    resolved against it, so a caller cannot invent a role or a jurisdiction.
    A malformed entry is a configuration error and is reported as one rather
    than skipped.
    """
    actors: dict[str, IncidentActor] = {}
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":")]
        if len(parts) not in (2, 3):
            raise SimulatorDisabledError(
                f"SIMULATOR_ACTORS entry {entry!r} must be "
                "'<actor_id>:<role>[:<jurisdiction>]'"
            )
        actor_id, role_name = parts[0], parts[1].lower()
        jurisdiction = parts[2] if len(parts) == 3 else None
        try:
            role = ResponderRole(role_name)
        except ValueError as exc:
            raise SimulatorDisabledError(
                f"SIMULATOR_ACTORS entry {entry!r} has unknown role {role_name!r}"
            ) from exc
        if actor_id in actors:
            raise SimulatorDisabledError(
                f"SIMULATOR_ACTORS lists {actor_id!r} more than once"
            )
        actors[actor_id] = IncidentActor(
            actor_id=actor_id, role=role, jurisdiction=jurisdiction
        )
    return actors


class IncidentService:
    def __init__(
        self,
        *,
        incident_repository: IncidentRepository,
        alert_repository: AlertRepository,
        report_repository: FireReportRepository,
        delivery_repository: IncidentDeliveryRepository,
        webhook_delivery_repository: IncidentWebhookDeliveryRepository | None = None,
        published_alerts: PublishedAlertService,
        hotspot_store: HotspotScanStore | None = None,
        fire_hotspots: FireHotspotRepository | None = None,
    ) -> None:
        self._incidents = incident_repository
        self._alerts = alert_repository
        self._reports = report_repository
        self._deliveries = delivery_repository
        self._webhook_deliveries = webhook_delivery_repository
        self._published_alerts = published_alerts
        self._hotspot_store = hotspot_store
        self._fire_hotspots = fire_hotspots

    @property
    def webhook_dispatch_enabled(self) -> bool:
        return self._webhook_deliveries is not None

    # -- permissions --------------------------------------------------------

    def resolve_actor(self, *, actor_id: str | None) -> IncidentActor:
        """Resolve `X-Actor-Id` against the configured actor registry.

        The role and jurisdiction come from the registry, never from the
        request, so possession of the shared simulator key is not authority.
        """
        if actor_id is None or not actor_id.strip():
            raise ActorNotPermittedError(
                "an X-Actor-Id header naming a configured responder is required"
            )
        registry = parse_simulator_actors(get_settings().simulator_actors)
        if not registry:
            raise SimulatorDisabledError(
                "no responder actors are configured (SIMULATOR_ACTORS is empty)"
            )
        actor = registry.get(actor_id.strip())
        if actor is None:
            raise ActorNotPermittedError(
                f"actor {actor_id.strip()!r} is not a configured responder"
            )
        return actor

    def _authorize(self, incident: Incident, actor: IncidentActor) -> None:
        """Role and jurisdiction checks every write on an existing incident."""
        if actor.role is not incident.responder_role:
            raise RoleMismatchError(
                f"incident {incident.id} is handled by {incident.responder_role.value}; "
                f"actor {actor.actor_id!r} acts as {actor.role.value}"
            )
        if actor.jurisdiction is None:
            # An actor with no jurisdiction of its own (a control-room actor) is
            # not scoped, so it may act anywhere it holds the right role.
            return
        if incident.jurisdiction is not None and incident.jurisdiction != actor.jurisdiction:
            raise JurisdictionMismatchError(
                f"incident {incident.id} is in jurisdiction "
                f"{incident.jurisdiction!r}; actor {actor.actor_id!r} acts in "
                f"{actor.jurisdiction!r}"
            )

    def _authorize_claimed_role(
        self, actor: IncidentActor, claimed_role: ResponderRole | None
    ) -> None:
        """A body may still name the role it thinks it is, but it may not
        disagree with the authenticated actor."""
        if claimed_role is not None and claimed_role is not actor.role:
            raise RoleMismatchError(
                f"request claims role {claimed_role.value} but actor "
                f"{actor.actor_id!r} is registered as {actor.role.value}"
            )

    # -- creation -----------------------------------------------------------

    def create_from_source(
        self,
        *,
        source_type: IncidentSourceType,
        actor: IncidentActor,
        source_id: int | None = None,
        source_ref: str | None = None,
        severity: str | None,
        jurisdiction: str | None,
        latitude: float | None,
        longitude: float | None,
        linked_prediction_run_id: str | None,
        evidence_report_ids: list[int] | None,
        now: datetime,
    ) -> CreateOutcome:
        # Authorize this source before the idempotent early-return below. A
        # previously opened FIRMS incident must not become readable through a
        # create retry made by a responder with the wrong role.
        if (
            source_type is IncidentSourceType.FIRMS_DETECTION
            and actor.role is not ResponderRole.FIRE_DEPARTMENT
        ):
            raise RoleMismatchError("only a fire-department responder may open a FIRMS detection")
        if source_type.uses_ref:
            if source_ref is None or not source_ref.strip():
                raise InvalidIncidentError(f"a {source_type.value} incident requires source_ref")
            if source_id is not None:
                raise InvalidIncidentError(
                    f"source_id must not be given for a {source_type.value} source"
                )
            key_ref: str | None = source_ref.strip()
        else:
            if source_id is None:
                raise InvalidIncidentError(f"a {source_type.value} incident requires source_id")
            if source_ref is not None:
                raise InvalidIncidentError(
                    f"source_ref is not valid for a {source_type.value} source"
                )
            key_ref = None

        existing = self._incidents.get_by_source(
            source_type, source_id=source_id, source_ref=key_ref
        )
        if existing is not None and self._is_incident_available(existing):
            self._assert_same_attributes(
                existing,
                severity=severity,
                jurisdiction=jurisdiction,
                linked_prediction_run_id=linked_prediction_run_id,
            )
            return CreateOutcome(existing, created=False)

        if source_type is IncidentSourceType.PUBLISHED_ALERT:
            role, default_severity, source_lat, source_lon, published = (
                self._resolve_published_alert(key_ref or "")
            )
            cell_id = published.h3_cell
            # A published alert is tied to its run, so the link is derived, not
            # optional: two different runs are two different incidents.
            effective_run_id = published.run_id
            source_synthetic = published.synthetic
        elif source_type is IncidentSourceType.HOTSPOT_EVENT:
            if actor.role is not ResponderRole.POLLUTION_CONTROL:
                raise RoleMismatchError(
                    "only a pollution-control responder may open a hotspot event"
                )
            role, default_severity, source_lat, source_lon, cell_id = (
                self._resolve_hotspot_event(key_ref or "")
            )
            effective_run_id = linked_prediction_run_id
            source_synthetic = False
        elif source_type is IncidentSourceType.FIRMS_DETECTION:
            role, default_severity, source_lat, source_lon, cell_id = self._resolve_firms_detection(
                key_ref or "", now=now
            )
            effective_run_id = linked_prediction_run_id
            source_synthetic = False
        else:
            role, default_severity, source_lat, source_lon, cell_id = self._resolve_source(
                source_type, source_id
            )
            effective_run_id = linked_prediction_run_id
            source_synthetic = self._source_is_synthetic(source_type, source_id)

        effective_lat = latitude if latitude is not None else source_lat
        effective_lon = longitude if longitude is not None else source_lon
        if not -90 <= effective_lat <= 90 or not -180 <= effective_lon <= 180:
            raise InvalidIncidentError("incident coordinates out of range")

        incident = Incident(
            source_type=source_type,
            source_id=source_id,
            source_ref=key_ref,
            source_synthetic=source_synthetic,
            status=IncidentStatus.REPORTED,
            responder_role=role,
            severity=severity or default_severity,
            # A new incident is recorded in the creating actor's jurisdiction
            # unless the caller states another one (which is allowed: that is
            # the act of classifying the incident, not of responding to it).
            jurisdiction=jurisdiction or actor.jurisdiction,
            latitude=effective_lat,
            longitude=effective_lon,
            h3_cell=cell_id,
            linked_prediction_run_id=effective_run_id,
            evidence_report_ids=tuple(evidence_report_ids or ()),
            created_at=now,
            updated_at=now,
        )
        event = IncidentEvent(
            incident_id=0,  # replaced by the repository with the stored id
            event_type=IncidentEventType.CREATED,
            to_status=IncidentStatus.REPORTED,
            role=role,
            actor=actor.actor_id,
            actor_jurisdiction=actor.jurisdiction,
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
            and stored.source_key == incident.source_key
        )
        if not created:
            self._assert_same_attributes(
                stored,
                severity=severity,
                jurisdiction=jurisdiction,
                linked_prediction_run_id=effective_run_id,
            )
        return CreateOutcome(stored, created=created)

    def _resolve_published_alert(
        self, source_ref: str
    ) -> tuple[ResponderRole, str, float, float, _PublishedSource]:
        """Resolve `v2:<run>:<cell>:<forecast_minutes>` to incident values.

        A published alert is an air-quality condition, so it is handled by
        pollution control — the same classification the v2 endpoint used to
        label the alert, taken from the same rule.
        """
        try:
            alert: PublishedAlert = self._published_alerts.resolve(source_ref)
        except PublishedAlertIdentityError as exc:
            raise InvalidIncidentError(str(exc)) from exc
        except PublishedAlertNotFoundError as exc:
            raise IncidentNotFoundError(str(exc)) from exc
        except PublishedAlertNotEligibleError as exc:
            raise SourceNotEligibleError(str(exc)) from exc
        # A published alert is a cell, not a point: use the cell centre, the
        # same convention a persisted cell-level alert uses.
        latitude, longitude = cell_center(alert.h3_cell)
        return (
            ResponderRole.POLLUTION_CONTROL,
            alert.severity,
            latitude,
            longitude,
            _PublishedSource(
                h3_cell=alert.h3_cell,
                run_id=alert.run_id,
                synthetic=alert.synthetic,
            ),
        )

    def _resolve_hotspot_event(
        self, event_id: str
    ) -> tuple[ResponderRole, str, float, float, str]:
        """Resolve a live persisted hotspot event into a reviewable incident."""
        if self._hotspot_store is None:
            raise SourceNotEligibleError("hotspot event storage is not configured")
        try:
            event = self._hotspot_store.event(event_id)
        except FileNotFoundError as exc:
            raise IncidentNotFoundError(str(exc)) from exc
        if event.get("synthetic") is True:
            raise SourceNotEligibleError("synthetic hotspot events cannot enter the response queue")
        if event.get("status") != "confirmed":
            raise SourceNotEligibleError(
                "a hotspot must be confirmed before it enters the response queue"
            )
        cell_id = event.get("dedup_cell")
        if not isinstance(cell_id, str):
            raise SourceNotEligibleError("hotspot event has no mappable H3 footprint")
        latitude, longitude = cell_center(cell_id)
        return ResponderRole.POLLUTION_CONTROL, "warning", latitude, longitude, cell_id

    def _resolve_firms_detection(
        self, detection_id: str, *, now: datetime
    ) -> tuple[ResponderRole, str, float, float, str]:
        """Resolve a recent persisted NASA detection as a review-only candidate."""
        if self._fire_hotspots is None:
            raise SourceNotEligibleError("FIRMS detection storage is not configured")
        detection: FireHotspot | None = self._fire_hotspots.get_by_detection_id(
            detection_id, available_by=now
        )
        if detection is None:
            raise IncidentNotFoundError(f"FIRMS detection {detection_id!r} does not exist")
        age_hours = (now - detection.acquired_at).total_seconds() / 3600
        if age_hours < 0:
            raise SourceNotEligibleError("future FIRMS detections cannot enter the response queue")
        if age_hours > get_settings().firms_stale_after_hours:
            raise SourceNotEligibleError("stale FIRMS detections cannot open new incidents")
        # Confidence and FRP determine a review band only. Even a high-FRP,
        # high-confidence satellite record never creates an emergency status.
        severity = "warning" if detection.confidence_class in {"nominal", "high"} else "watch"
        return (
            ResponderRole.FIRE_DEPARTMENT,
            severity,
            detection.latitude,
            detection.longitude,
            detection.h3_cell,
        )

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
        actor: IncidentActor,
        assignee: str,
        claimed_role: ResponderRole | None = None,
        now: datetime,
    ) -> Incident:
        incident = self._require(incident_id)
        self._authorize_claimed_role(actor, claimed_role)
        self._authorize(incident, actor)
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
            role=actor.role,
            actor=actor.actor_id,
            actor_jurisdiction=actor.jurisdiction,
            created_at=now,
        )
        stored = self._incidents.update(updated, event)
        # Assignment makes the incident visible in the addressed role's
        # *simulated* inbox. This records a hand-off; it sends nothing.
        self._record_delivery(stored, assignee=assignee, actor=actor, now=now)
        if self._webhook_deliveries is not None:
            if stored.id is None:  # pragma: no cover - persisted incidents have ids
                raise InvalidIncidentError("cannot enqueue delivery for an incident without an id")
            self._webhook_deliveries.create(
                IncidentWebhookDelivery(
                    incident_id=stored.id,
                    audience_role=stored.responder_role,
                    assignee=assignee.strip(),
                    status=IncidentWebhookStatus.QUEUED,
                    created_at=now,
                    next_attempt_at=now,
                )
            )
        return stored

    def _record_delivery(
        self,
        incident: Incident,
        *,
        assignee: str,
        actor: IncidentActor,
        now: datetime,
    ) -> IncidentDelivery:
        """Write the simulated delivery row for a new assignment.

        The delivery is addressed to the incident's responder role (not to
        whoever assigned it) so the intended authority is the only one that
        sees it in its inbox. A `delivered` event is appended so the operational
        history shows the hand-off, not just the state change.
        """
        if incident.id is None:  # pragma: no cover - stored incidents have ids
            raise InvalidIncidentError("cannot deliver an incident without an id")
        delivery = self._deliveries.create(
            IncidentDelivery(
                incident_id=incident.id,
                audience_role=incident.responder_role,
                status=IncidentDeliveryStatus.SIMULATED,
                simulated_at=now,
                assignee=assignee,
            )
        )
        self._incidents.append_event(
            IncidentEvent(
                incident_id=incident.id,
                event_type=IncidentEventType.DELIVERED,
                from_status=incident.status,
                to_status=incident.status,
                role=incident.responder_role,
                actor=actor.actor_id,
                actor_jurisdiction=actor.jurisdiction,
                note=f"simulated delivery to the {incident.responder_role.value} inbox",
                created_at=now,
            )
        )
        return delivery

    # -- transitions --------------------------------------------------------

    def transition(
        self,
        *,
        incident_id: int,
        to_status: IncidentStatus,
        actor: IncidentActor,
        note: str | None = None,
        claimed_role: ResponderRole | None = None,
        now: datetime,
    ) -> Incident:
        incident = self._require(incident_id)
        self._authorize_claimed_role(actor, claimed_role)
        self._authorize(incident, actor)
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
            role=actor.role,
            actor=actor.actor_id,
            actor_jurisdiction=actor.jurisdiction,
            note=note,
            created_at=now,
        )
        stored = self._incidents.update(updated, event)
        if to_status is IncidentStatus.ACKNOWLEDGED:
            # The responder acknowledged the incident, so their open inbox items
            # are answered. Idempotent: a repeated acknowledgement changes
            # nothing.
            self._deliveries.acknowledge_open(incident_id, acknowledged_at=now)
        return stored

    # -- reads --------------------------------------------------------------

    def get(self, incident_id: int) -> Incident:
        return self._require(incident_id)

    def list(
        self,
        *,
        status: IncidentStatus | None = None,
        role: ResponderRole | None = None,
        archived_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Incident]:
        if get_settings().demo_mode:
            return self._incidents.list(
                status=status,
                role=role,
                archived_only=archived_only,
                limit=limit,
                offset=offset,
            )

        page_size = 100
        visible: list[Incident] = []
        scanned = 0
        available_alert_ids: set[int] | None = None
        while True:
            page = self._incidents.list(
                status=status,
                role=role,
                archived_only=archived_only,
                limit=page_size,
                offset=scanned,
            )
            if available_alert_ids is None and any(
                item.source_type is IncidentSourceType.ALERT and not item.source_synthetic
                for item in page
            ):
                available_alert_ids = self._active_alert_ids()
            visible.extend(
                item
                for item in page
                if self._is_incident_available(
                    item, available_alert_ids=available_alert_ids
                )
            )
            scanned += len(page)
            if len(page) < page_size or (limit is not None and len(visible) >= offset + limit):
                break
        if limit is None:
            return visible[offset:]
        return visible[offset : offset + limit]

    def history(self, incident_id: int) -> list[IncidentEvent]:
        self._require(incident_id)
        return self._incidents.history(incident_id)

    def deliveries(self, incident_id: int) -> list[IncidentDelivery]:
        self._require(incident_id)
        return self._deliveries.list_for_incident(incident_id)

    def inbox(
        self, *, role: ResponderRole, only_open: bool = False
    ) -> list[InboxItem]:
        """The simulated inbox of one responder role, newest first.

        A delivery is only listed for the role it was addressed to, so a
        fire-department actor never sees a pollution-control assignment and
        vice versa. No key is needed to read: this is a simulator view, and it
        contains no more than the public incident list does.
        """
        demo_mode = get_settings().demo_mode
        items: list[InboxItem] = []
        available_alert_ids: set[int] | None = None
        for delivery in self._deliveries.list_for_role(role, only_open=only_open):
            incident = self._incidents.get(delivery.incident_id)
            if incident is None:  # pragma: no cover - deliveries FK to incidents
                continue
            if (
                not demo_mode
                and available_alert_ids is None
                and incident.source_type is IncidentSourceType.ALERT
                and not incident.source_synthetic
            ):
                available_alert_ids = self._active_alert_ids()
            if not self._is_incident_available(
                incident, available_alert_ids=available_alert_ids
            ):
                continue
            items.append(InboxItem(delivery=delivery, incident=incident))
        return items

    # -- helpers ------------------------------------------------------------

    def _require(self, incident_id: int) -> Incident:
        incident = self._incidents.get(incident_id)
        if incident is None or not self._is_incident_available(incident):
            raise IncidentNotFoundError(f"incident {incident_id} does not exist")
        return incident

    def _is_incident_available(
        self,
        incident: Incident,
        *,
        available_alert_ids: set[int] | None = None,
    ) -> bool:
        """Hide synthetic or demo-backed records when the API runs live."""
        if get_settings().demo_mode:
            return True
        if incident.source_synthetic:
            return False
        if incident.source_type is IncidentSourceType.REPORT:
            report = self._reports.get(incident.source_id or -1)
            return bool(
                report is not None
                and not (
                    report.client_report_id
                    and report.client_report_id.startswith("demo-fire-")
                )
            )
        if incident.source_type is IncidentSourceType.ALERT:
            if available_alert_ids is not None:
                return incident.source_id in available_alert_ids
            return self._alert_by_id(incident.source_id or -1) is not None
        if incident.source_type is IncidentSourceType.PUBLISHED_ALERT:
            try:
                alert = self._published_alerts.resolve(incident.source_ref or "")
            except Exception:
                return False
            return not alert.synthetic
        if incident.source_type is IncidentSourceType.HOTSPOT_EVENT:
            if self._hotspot_store is None:
                return False
            try:
                event = self._hotspot_store.event(incident.source_ref or "")
            except Exception:
                return False
            return event.get("synthetic") is not True and event.get("status") == "confirmed"
        if incident.source_type is IncidentSourceType.FIRMS_DETECTION:
            if self._fire_hotspots is None:
                return False
            return (
                self._fire_hotspots.get_by_detection_id(
                    incident.source_ref or "", available_by=datetime.now(UTC)
                )
                is not None
            )
        return False

    def _source_is_synthetic(
        self, source_type: IncidentSourceType, source_id: int | None
    ) -> bool:
        if source_type is IncidentSourceType.ALERT:
            return get_settings().demo_mode
        if source_type is IncidentSourceType.REPORT and source_id is not None:
            report = self._reports.get(source_id)
            return bool(
                report is not None
                and report.client_report_id
                and report.client_report_id.startswith("demo-fire-")
            )
        return False

    def _alert_by_id(self, alert_id: int) -> Alert | None:
        # AlertRepository exposes list_active(since); search the active window
        # (an incident is only created for a still-active alert).
        from datetime import timedelta

        since = datetime.now(UTC) - timedelta(hours=get_settings().alert_active_lookback_hours)
        mode = DataMode.DEMO if get_settings().demo_mode else DataMode.LIVE
        for alert in self._alerts.list_active(since=since, mode=mode):
            if alert.id == alert_id:
                return alert
        return None

    def _active_alert_ids(self) -> set[int]:
        """Load eligible alerts once when filtering a batch of incidents."""
        from datetime import timedelta

        since = datetime.now(UTC) - timedelta(hours=get_settings().alert_active_lookback_hours)
        return {
            alert.id
            for alert in self._alerts.list_active(since=since, mode=DataMode.LIVE)
            if alert.id is not None
        }

    def _report_by_id(self, report_id: int) -> FireReport | None:
        from datetime import timedelta

        since = datetime.now(UTC) - timedelta(hours=get_settings().fire_report_max_age_hours)
        for report in self._reports.list_active(since=since):
            if (
                report.id == report_id
                and (get_settings().demo_mode or not (
                    report.client_report_id
                    and report.client_report_id.startswith("demo-fire-")
                ))
            ):
                return report
        return None
