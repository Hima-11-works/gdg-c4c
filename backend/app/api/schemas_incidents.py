"""Schemas for the incident workflow. See docs/api/incidents.md."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

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
]


class IncidentCreateIn(BaseModel):
    source_type: IncidentSourceType
    source_id: int = Field(ge=1)
    severity: str | None = Field(default=None, max_length=20)
    jurisdiction: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    linked_prediction_run_id: str | None = Field(default=None, max_length=120)
    evidence_report_ids: list[int] | None = None


class IncidentAssignIn(BaseModel):
    role: ResponderRole
    assignee: str = Field(min_length=1, max_length=120)


class IncidentTransitionIn(BaseModel):
    to_status: IncidentStatus
    role: ResponderRole
    note: str | None = Field(default=None, max_length=500)


class IncidentOut(BaseModel):
    id: int
    source_type: IncidentSourceType
    source_id: int
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
    note: str | None
    created_at: datetime
