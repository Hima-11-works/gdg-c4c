"""Schemas for the incident workflow. See docs/api/incidents.md."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.domain.incidents import (
    IncidentSourceType,
    IncidentStatus,
    ResponderRole,
)

# Re-exported for route signatures; the enums live in the domain layer.
__all__ = [
    "IncidentSourceType",
    "IncidentStatus",
    "ResponderRole",
    "IncidentCreateIn",
    "IncidentAssignIn",
    "IncidentTransitionIn",
    "IncidentOut",
    "IncidentEventOut",
    "IncidentDeliveryOut",
    "IncidentInboxItemOut",
]


class IncidentCreateIn(BaseModel):
    source_type: IncidentSourceType
    # Exactly one of source_id / source_ref, decided by source_type: a
    # published alert is named by its stable identity, a persisted alert or
    # report by its row id.
    source_id: int | None = Field(default=None, ge=1)
    source_ref: str | None = Field(default=None, min_length=1, max_length=200)
    severity: str | None = Field(default=None, max_length=20)
    jurisdiction: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    linked_prediction_run_id: str | None = Field(default=None, max_length=120)
    evidence_report_ids: list[int] | None = None

    @model_validator(mode="after")
    def _check_source_reference(self) -> "IncidentCreateIn":
        if self.source_type.uses_ref:
            if self.source_ref is None:
                raise ValueError(
                    "a published_alert incident requires source_ref "
                    "('v2:<run_id>:<h3_cell>:<forecast_minutes>', as returned by "
                    "GET /api/v2/alerts)"
                )
            if self.source_id is not None:
                raise ValueError("source_id must not be given for a published_alert source")
        else:
            if self.source_id is None:
                raise ValueError(f"a {self.source_type.value} incident requires source_id")
            if self.source_ref is not None:
                raise ValueError(
                    f"source_ref is not valid for a {self.source_type.value} source"
                )
        return self


class IncidentAssignIn(BaseModel):
    # The acting role is resolved from the authenticated actor (X-Actor-Id).
    # A body may still name the role it thinks it is, but a disagreement with
    # the actor is rejected — the body can never widen authority.
    role: ResponderRole | None = None
    assignee: str = Field(min_length=1, max_length=120)


class IncidentTransitionIn(BaseModel):
    to_status: IncidentStatus
    role: ResponderRole | None = None
    note: str | None = Field(default=None, max_length=500)


class IncidentOut(BaseModel):
    id: int
    source_type: IncidentSourceType
    source_id: int | None
    source_ref: str | None
    source_synthetic: bool
    status: IncidentStatus
    responder_role: ResponderRole
    severity: str
    jurisdiction: str | None
    latitude: float
    longitude: float
    h3_cell: str | None
    linked_prediction_run_id: str | None
    evidence_report_ids: list[int]
    assignee: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class IncidentEventOut(BaseModel):
    id: int
    incident_id: int
    event_type: str
    from_status: IncidentStatus | None
    to_status: IncidentStatus | None
    role: ResponderRole | None
    actor: str | None
    actor_jurisdiction: str | None
    note: str | None
    created_at: datetime


class IncidentDeliveryOut(BaseModel):
    """One simulated hand-off of an incident to a responder role's inbox.

    `simulated` is always true and `notification` names what did *not* happen,
    so a consumer of this API cannot mistake the row for a real dispatch.
    """

    id: int
    incident_id: int
    audience_role: ResponderRole
    status: str
    assignee: str | None
    simulated: bool
    notification: str
    simulated_at: datetime
    acknowledged_at: datetime | None


class IncidentInboxItemOut(BaseModel):
    """A line of a responder role's simulated inbox: the delivery plus enough
    of the incident to triage it."""

    delivery: IncidentDeliveryOut
    incident: IncidentOut
    is_open: bool
