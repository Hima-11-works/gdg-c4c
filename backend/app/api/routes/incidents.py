"""Routes for the incident workflow (the fire-department simulator).

Every incident write depends on `require_incident_actor`: the simulator key plus
an `X-Actor-Id` naming a configured responder, whose role and jurisdiction come
from the server-side registry. The simulator inbox is always recorded. An
additional real webhook is sent only when an operator configures the destination,
secret, and acknowledgement base URL. See docs/api/incidents.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status

from app.api.deps import (
    get_incident_service,
    get_incident_webhook_service,
    require_incident_actor,
)
from app.api.schemas import Envelope
from app.api.schemas_incidents import (
    IncidentAssignIn,
    IncidentCreateIn,
    IncidentDeliveryOut,
    IncidentEventOut,
    IncidentInboxItemOut,
    IncidentOut,
    IncidentSourceType,
    IncidentStatus,
    IncidentTransitionIn,
    IncidentWebhookAcknowledgeIn,
    IncidentWebhookDeliveryOut,
    ResponderRole,
)
from app.domain.incidents import (
    IncidentActor,
    IncidentDelivery,
    IncidentEvent,
    IncidentWebhookDelivery,
    InvalidIncidentError,
    InvalidTransitionError,
)
from app.services.incidents import (
    CreateOutcome,
    IncidentConflictError,
    IncidentNotFoundError,
    IncidentService,
    InboxItem,
    JurisdictionMismatchError,
    RoleMismatchError,
    SimulatorDisabledError,
    SourceNotEligibleError,
    UseAssignEndpointError,
)
from app.services.incident_webhook import IncidentWebhookAckError, IncidentWebhookService
from app.services.incident_webhook_runtime import dispatch_incident_webhooks

router = APIRouter(prefix="/incidents", tags=["incidents"])

# What a delivery row is, stated in the payload: this workflow records a
# hand-off and sends nothing.
_NO_NOTIFICATION = "none (simulated inbox row; optional webhook is reported separately)"


def _incident_out(incident) -> IncidentOut:
    return IncidentOut(
        id=incident.id,
        source_type=incident.source_type,
        source_id=incident.source_id,
        source_ref=incident.source_ref,
        source_synthetic=incident.source_synthetic,
        status=incident.status,
        responder_role=incident.responder_role,
        severity=incident.severity,
        jurisdiction=incident.jurisdiction,
        latitude=incident.latitude,
        longitude=incident.longitude,
        h3_cell=incident.h3_cell,
        linked_prediction_run_id=incident.linked_prediction_run_id,
        evidence_report_ids=list(incident.evidence_report_ids),
        assignee=incident.assignee,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        resolved_at=incident.resolved_at,
    )


def _event_out(event: IncidentEvent) -> IncidentEventOut:
    return IncidentEventOut(
        id=event.id,
        incident_id=event.incident_id,
        event_type=event.event_type.value,
        from_status=event.from_status,
        to_status=event.to_status,
        role=event.role,
        actor=event.actor,
        actor_jurisdiction=event.actor_jurisdiction,
        note=event.note,
        created_at=event.created_at,
    )


def _delivery_out(delivery: IncidentDelivery) -> IncidentDeliveryOut:
    return IncidentDeliveryOut(
        id=delivery.id,
        incident_id=delivery.incident_id,
        audience_role=delivery.audience_role,
        status=delivery.status.value,
        assignee=delivery.assignee,
        simulated=delivery.simulated,
        notification=_NO_NOTIFICATION,
        simulated_at=delivery.simulated_at,
        acknowledged_at=delivery.acknowledged_at,
    )


def _inbox_out(item: InboxItem) -> IncidentInboxItemOut:
    return IncidentInboxItemOut(
        delivery=_delivery_out(item.delivery),
        incident=_incident_out(item.incident),
        is_open=item.is_open,
    )


def _webhook_delivery_out(delivery: IncidentWebhookDelivery) -> IncidentWebhookDeliveryOut:
    return IncidentWebhookDeliveryOut(
        id=delivery.id,
        incident_id=delivery.incident_id,
        audience_role=delivery.audience_role,
        assignee=delivery.assignee,
        status=delivery.status.value,
        attempt_count=delivery.attempt_count,
        created_at=delivery.created_at,
        next_attempt_at=delivery.next_attempt_at,
        last_attempt_at=delivery.last_attempt_at,
        delivered_at=delivery.delivered_at,
        acknowledged_at=delivery.acknowledged_at,
        last_error=delivery.last_error,
    )


@router.post(
    "",
    response_model=Envelope[IncidentOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create an incident from a published alert, persisted alert, or report",
)
def create_incident(
    payload: IncidentCreateIn,
    response: Response,
    actor: IncidentActor = Depends(require_incident_actor),
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[IncidentOut]:
    try:
        outcome: CreateOutcome = service.create_from_source(
            source_type=payload.source_type,
            actor=actor,
            source_id=payload.source_id,
            source_ref=payload.source_ref,
            severity=payload.severity,
            jurisdiction=payload.jurisdiction,
            latitude=payload.latitude,
            longitude=payload.longitude,
            linked_prediction_run_id=payload.linked_prediction_run_id,
            evidence_report_ids=payload.evidence_report_ids,
            now=datetime.now(UTC),
        )
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IncidentConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SourceNotEligibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except InvalidIncidentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except JurisdictionMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
            headers={"X-Error-Code": "jurisdiction_mismatch"},
        ) from exc
    except RoleMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except SimulatorDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": "simulator_disabled"},
        ) from exc

    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return Envelope(
        generated_at=datetime.now(UTC), is_demo=False, data=_incident_out(outcome.incident)
    )


@router.post(
    "/{incident_id}/assign",
    response_model=Envelope[IncidentOut],
    summary="Assign an incident to a responder (and open it in their simulated inbox)",
)
def assign_incident(
    incident_id: int,
    payload: IncidentAssignIn,
    background_tasks: BackgroundTasks,
    actor: IncidentActor = Depends(require_incident_actor),
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[IncidentOut]:
    try:
        incident = service.assign(
            incident_id=incident_id,
            actor=actor,
            assignee=payload.assignee,
            claimed_role=payload.role,
            now=datetime.now(UTC),
        )
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except JurisdictionMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
            headers={"X-Error-Code": "jurisdiction_mismatch"},
        ) from exc
    except RoleMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-Error-Code": "invalid_transition"},
        ) from exc
    except InvalidIncidentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if service.webhook_dispatch_enabled:
        background_tasks.add_task(dispatch_incident_webhooks)
    return Envelope(
        generated_at=datetime.now(UTC), is_demo=False, data=_incident_out(incident)
    )


@router.get(
    "/{incident_id}/webhook-deliveries",
    response_model=Envelope[list[IncidentWebhookDeliveryOut]],
    summary="Authority webhook delivery and acknowledgement status",
)
def incident_webhook_deliveries(
    incident_id: int,
    incident_service: IncidentService = Depends(get_incident_service),
    webhook_service: IncidentWebhookService = Depends(get_incident_webhook_service),
) -> Envelope[list[IncidentWebhookDeliveryOut]]:
    try:
        incident_service.get(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[_webhook_delivery_out(row) for row in webhook_service.list_for_incident(incident_id)],
    )


@router.post(
    "/webhook-deliveries/{delivery_id}/acknowledge",
    response_model=Envelope[IncidentWebhookDeliveryOut],
    summary="Acknowledge receipt of an authority webhook",
)
def acknowledge_webhook_delivery(
    delivery_id: int,
    payload: IncidentWebhookAcknowledgeIn,
    service: IncidentWebhookService = Depends(get_incident_webhook_service),
) -> Envelope[IncidentWebhookDeliveryOut]:
    try:
        delivery = service.acknowledge(delivery_id=delivery_id, token=payload.token)
    except IncidentWebhookAckError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=_webhook_delivery_out(delivery),
    )

@router.post(
    "/{incident_id}/transitions",
    response_model=Envelope[IncidentOut],
    summary="Advance an incident's status",
)
def transition_incident(
    incident_id: int,
    payload: IncidentTransitionIn,
    actor: IncidentActor = Depends(require_incident_actor),
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[IncidentOut]:
    try:
        incident = service.transition(
            incident_id=incident_id,
            to_status=payload.to_status,
            actor=actor,
            note=payload.note,
            claimed_role=payload.role,
            now=datetime.now(UTC),
        )
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except JurisdictionMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
            headers={"X-Error-Code": "jurisdiction_mismatch"},
        ) from exc
    except RoleMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except UseAssignEndpointError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-Error-Code": "use_assign_endpoint"},
        ) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-Error-Code": "invalid_transition"},
        ) from exc
    return Envelope(
        generated_at=datetime.now(UTC), is_demo=False, data=_incident_out(incident)
    )


@router.get(
    "",
    response_model=Envelope[list[IncidentOut]],
    summary="List incidents",
)
def list_incidents(
    status_filter: IncidentStatus | None = Query(default=None, alias="status"),
    role: ResponderRole | None = Query(default=None),
    archive: bool = Query(
        default=False,
        description="List resolved/cancelled incidents instead of active work.",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[list[IncidentOut]]:
    incidents = service.list(
        status=status_filter,
        role=role,
        archived_only=archive,
        limit=limit,
        offset=offset,
    )
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[_incident_out(incident) for incident in incidents],
    )


@router.get(
    "/inbox",
    response_model=Envelope[list[IncidentInboxItemOut]],
    summary="A responder role's simulated inbox",
)
def incident_inbox(
    role: ResponderRole = Query(..., description="The responding authority's role."),
    only_open: bool = Query(
        default=False, description="Only assignments not yet acknowledged."
    ),
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[list[IncidentInboxItemOut]]:
    """Assignments addressed to one responder role, newest first.

    A delivery appears only in the inbox of the role it was addressed to, so a
    fire-department view never shows a pollution-control assignment. Readable
    without a key, like the rest of the simulator views: it exposes the same
    incidents the public incident list already does, and sends nothing.
    """
    items = service.inbox(role=role, only_open=only_open)
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[_inbox_out(item) for item in items],
    )


@router.get(
    "/{incident_id}/deliveries",
    response_model=Envelope[list[IncidentDeliveryOut]],
    summary="An incident's simulated delivery records",
)
def incident_deliveries(
    incident_id: int,
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[list[IncidentDeliveryOut]]:
    """Every simulated hand-off for one incident, oldest first."""
    try:
        deliveries = service.deliveries(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[_delivery_out(delivery) for delivery in deliveries],
    )


@router.get(
    "/{incident_id}",
    response_model=Envelope[IncidentOut],
    summary="Get an incident",
)
def get_incident(
    incident_id: int,
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[IncidentOut]:
    try:
        incident = service.get(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC), is_demo=False, data=_incident_out(incident)
    )


@router.get(
    "/{incident_id}/history",
    response_model=Envelope[list[IncidentEventOut]],
    summary="Get an incident's append-only history",
)
def get_incident_history(
    incident_id: int,
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[list[IncidentEventOut]]:
    try:
        events = service.history(incident_id)
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[_event_out(event) for event in events],
    )
