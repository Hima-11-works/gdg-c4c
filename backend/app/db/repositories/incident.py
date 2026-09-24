"""SQLAlchemy-backed implementation of app.domain.repositories.IncidentRepository."""

from __future__ import annotations

from psycopg.errors import UniqueViolation
from sqlalchemy import Insert, Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.incidents import (
    Incident,
    IncidentEvent,
    IncidentEventType,
    IncidentSourceType,
    IncidentStatus,
    ResponderRole,
)
from app.models.tables import incident as incident_table
from app.models.tables import incident_event as incident_event_table


def _row_to_incident(row) -> Incident:
    evidence = row.evidence_report_ids or []
    return Incident(
        id=row.id,
        source_type=IncidentSourceType(row.source_type),
        source_id=row.source_id,
        status=IncidentStatus(row.status),
        responder_role=ResponderRole(row.responder_role),
        severity=row.severity,
        jurisdiction=row.jurisdiction,
        latitude=row.latitude,
        longitude=row.longitude,
        h3_cell=row.h3_cell,
        linked_prediction_run_id=row.linked_prediction_run_id,
        evidence_report_ids=tuple(int(value) for value in evidence),
        assignee=row.assignee,
        created_at=row.created_at,
        updated_at=row.updated_at,
        resolved_at=row.resolved_at,
    )


def _row_to_event(row) -> IncidentEvent:
    return IncidentEvent(
        id=row.id,
        incident_id=row.incident_id,
        event_type=IncidentEventType(row.event_type),
        from_status=None if row.from_status is None else IncidentStatus(row.from_status),
        to_status=None if row.to_status is None else IncidentStatus(row.to_status),
        role=None if row.role is None else ResponderRole(row.role),
        actor=row.actor,
        note=row.note,
        created_at=row.created_at,
    )


def _incident_values(incident: Incident) -> dict:
    return {
        "source_type": incident.source_type.value,
        "source_id": incident.source_id,
        "status": incident.status.value,
        "responder_role": incident.responder_role.value,
        "severity": incident.severity,
        "jurisdiction": incident.jurisdiction,
        "latitude": incident.latitude,
        "longitude": incident.longitude,
        "geom": _to_point(incident.latitude, incident.longitude),
        "h3_cell": incident.h3_cell,
        "linked_prediction_run_id": incident.linked_prediction_run_id,
        "evidence_report_ids": list(incident.evidence_report_ids),
        "assignee": incident.assignee,
        "created_at": incident.created_at,
        "updated_at": incident.updated_at,
        "resolved_at": incident.resolved_at,
    }


def _event_values(event: IncidentEvent) -> dict:
    return {
        "incident_id": event.incident_id,
        "event_type": event.event_type.value,
        "from_status": None if event.from_status is None else event.from_status.value,
        "to_status": None if event.to_status is None else event.to_status.value,
        "role": None if event.role is None else event.role.value,
        "actor": event.actor,
        "note": event.note,
        "created_at": event.created_at,
    }


def _to_point(latitude: float, longitude: float):
    from geoalchemy2.elements import WKTElement

    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


def _get_stmt(incident_id: int) -> Select:
    return select(incident_table).where(incident_table.c.id == incident_id)


def _by_source_stmt(source_type: IncidentSourceType, source_id: int) -> Select:
    return select(incident_table).where(
        incident_table.c.source_type == source_type.value,
        incident_table.c.source_id == source_id,
    )


def _list_stmt(status, role) -> Select:
    stmt = select(incident_table)
    if status is not None:
        stmt = stmt.where(incident_table.c.status == status.value)
    if role is not None:
        stmt = stmt.where(incident_table.c.responder_role == role.value)
    return stmt.order_by(incident_table.c.created_at.desc(), incident_table.c.id)


def _history_stmt(incident_id: int) -> Select:
    return (
        select(incident_event_table)
        .where(incident_event_table.c.incident_id == incident_id)
        .order_by(incident_event_table.c.created_at, incident_event_table.c.id)
    )


class SqlIncidentRepository:
    """Implements app.domain.repositories.IncidentRepository against PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, incident: Incident, event: IncidentEvent) -> Incident:
        try:
            row = self._session.execute(
                incident_table.insert().values(**_incident_values(incident)).returning(
                    incident_table
                )
            ).one()
            stored = _row_to_incident(row)
            self._session.execute(
                incident_event_table.insert().values(
                    **_event_values(
                        IncidentEvent(
                            incident_id=stored.id,
                            event_type=event.event_type,
                            from_status=event.from_status,
                            to_status=event.to_status,
                            role=event.role,
                            actor=event.actor,
                            note=event.note,
                            created_at=event.created_at,
                        )
                    )
                )
            )
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            if not isinstance(exc.orig, UniqueViolation):
                raise
            # Same source already has an incident: return it rather than
            # surfacing a storage error. The service compares attributes.
            existing = self.get_by_source(incident.source_type, incident.source_id)
            if existing is None:
                raise
            return existing
        return stored

    def get(self, incident_id: int) -> Incident | None:
        row = self._session.execute(_get_stmt(incident_id)).first()
        return None if row is None else _row_to_incident(row)

    def get_by_source(
        self, source_type: IncidentSourceType, source_id: int
    ) -> Incident | None:
        row = self._session.execute(_by_source_stmt(source_type, source_id)).first()
        return None if row is None else _row_to_incident(row)

    def update(self, incident: Incident, event: IncidentEvent) -> Incident:
        if incident.id is None:
            raise ValueError("cannot update an incident without an id")
        row = self._session.execute(
            incident_table.update()
            .where(incident_table.c.id == incident.id)
            .values(
                status=incident.status.value,
                assignee=incident.assignee,
                updated_at=incident.updated_at,
                resolved_at=incident.resolved_at,
            )
            .returning(incident_table)
        ).one()
        stored = _row_to_incident(row)
        self._session.execute(
            incident_event_table.insert().values(
                **_event_values(
                    IncidentEvent(
                        incident_id=stored.id,
                        event_type=event.event_type,
                        from_status=event.from_status,
                        to_status=event.to_status,
                        role=event.role,
                        actor=event.actor,
                        note=event.note,
                        created_at=event.created_at,
                    )
                )
            )
        )
        self._session.commit()
        return stored

    def list(self, *, status=None, role=None) -> list[Incident]:
        rows = self._session.execute(_list_stmt(status, role)).all()
        return [_row_to_incident(row) for row in rows]

    def history(self, incident_id: int) -> list[IncidentEvent]:
        rows = self._session.execute(_history_stmt(incident_id)).all()
        return [_row_to_event(row) for row in rows]
