"""Routes for the incident workflow (the fire-department simulator).

Every write depends on require_simulator_key, so anonymous public changes are
impossible; reads are open. Business logic lives in
app.services.incidents.IncidentService. See docs/api/incidents.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import get_incident_service, require_simulator_key
from app.api.schemas import Envelope
from app.api.schemas_incidents import (
    IncidentAssignIn,
    IncidentCreateIn,
    IncidentEventOut,
    IncidentOut,
    IncidentSourceType,
    IncidentStatus,
    IncidentTransitionIn,
    ResponderRole,
)
from app.domain.incidents import IncidentEvent, InvalidIncidentError, InvalidTransitionError
from app.services.incidents import (
    CreateOutcome,
    IncidentConflictError,
    IncidentNotFoundError,
    IncidentService,
    RoleMismatchError,
    SimulatorDisabledError,
    SourceNotEligibleError,
    UseAssignEndpointError,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _incident_out(incident) -> IncidentOut:
    return IncidentOut(
        id=incident.id,
        source_type=incident.source_type,
        source_id=incident.source_id,
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
        note=event.note,
        created_at=event.created_at,
    )


@router.post(
    "",
    response_model=Envelope[IncidentOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create an incident from an eligible alert or report",
    dependencies=[Depends(require_simulator_key)],
)
def create_incident(
    payload: IncidentCreateIn,
    response: Response,
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[IncidentOut]:
    try:
        outcome: CreateOutcome = service.create_from_source(
            source_type=payload.source_type,
            source_id=payload.source_id,
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
    summary="Assign an incident to a responder",
    dependencies=[Depends(require_simulator_key)],
)
def assign_incident(
    incident_id: int,
    payload: IncidentAssignIn,
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[IncidentOut]:
    try:
        incident = service.assign(
            incident_id=incident_id,
            role=payload.role,
            assignee=payload.assignee,
            actor=payload.assignee,
            now=datetime.now(UTC),
        )
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoleMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidIncidentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return Envelope(
        generated_at=datetime.now(UTC), is_demo=False, data=_incident_out(incident)
    )


@router.post(
    "/{incident_id}/transitions",
    response_model=Envelope[IncidentOut],
    summary="Advance an incident's status",
    dependencies=[Depends(require_simulator_key)],
)
def transition_incident(
    incident_id: int,
    payload: IncidentTransitionIn,
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[IncidentOut]:
    try:
        incident = service.transition(
            incident_id=incident_id,
            to_status=payload.to_status,
            role=payload.role,
            actor=None,
            note=payload.note,
            now=datetime.now(UTC),
        )
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoleMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except UseAssignEndpointError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
    service: IncidentService = Depends(get_incident_service),
) -> Envelope[list[IncidentOut]]:
    incidents = service.list(status=status_filter, role=role)
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[_incident_out(incident) for incident in incidents],
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
