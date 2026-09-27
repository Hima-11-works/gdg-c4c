"""SQLAlchemy-backed implementation of
app.domain.repositories.IncidentDeliveryRepository.

These rows record that an assigned incident became visible in a responder
role's inbox. **Nothing here sends anything**: the table's `simulated` column
carries a CHECK constraint forcing it to true, so a real dispatch cannot even
be recorded here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.domain.incidents import (
    IncidentDelivery,
    IncidentDeliveryStatus,
    ResponderRole,
)
from app.models.tables import incident_delivery as incident_delivery_table


def _row_to_delivery(row) -> IncidentDelivery:
    return IncidentDelivery(
        id=row.id,
        incident_id=row.incident_id,
        audience_role=ResponderRole(row.audience_role),
        status=IncidentDeliveryStatus(row.status),
        assignee=row.assignee,
        simulated_at=row.simulated_at,
        acknowledged_at=row.acknowledged_at,
    )


def _values(delivery: IncidentDelivery) -> dict:
    return {
        "incident_id": delivery.incident_id,
        "audience_role": delivery.audience_role.value,
        "status": delivery.status.value,
        "assignee": delivery.assignee,
        # Always true: this workflow has no real dispatch path.
        "simulated": True,
        "simulated_at": delivery.simulated_at,
        "acknowledged_at": delivery.acknowledged_at,
    }


def _for_incident_stmt(incident_id: int) -> Select:
    return (
        select(incident_delivery_table)
        .where(incident_delivery_table.c.incident_id == incident_id)
        .order_by(incident_delivery_table.c.simulated_at, incident_delivery_table.c.id)
    )


def _for_role_stmt(role: ResponderRole, *, only_open: bool) -> Select:
    stmt = select(incident_delivery_table).where(
        incident_delivery_table.c.audience_role == role.value
    )
    if only_open:
        stmt = stmt.where(
            incident_delivery_table.c.status == IncidentDeliveryStatus.SIMULATED.value
        )
    return stmt.order_by(
        incident_delivery_table.c.simulated_at.desc(), incident_delivery_table.c.id.desc()
    )


class SqlIncidentDeliveryRepository:
    """Implements app.domain.repositories.IncidentDeliveryRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, delivery: IncidentDelivery) -> IncidentDelivery:
        row = self._session.execute(
            incident_delivery_table.insert()
            .values(**_values(delivery))
            .returning(incident_delivery_table)
        ).one()
        self._session.commit()
        return _row_to_delivery(row)

    def acknowledge_open(
        self, incident_id: int, *, acknowledged_at: datetime
    ) -> list[IncidentDelivery]:
        """Close every open delivery for the incident. Idempotent by status."""
        self._session.execute(
            incident_delivery_table.update()
            .where(
                incident_delivery_table.c.incident_id == incident_id,
                incident_delivery_table.c.status
                == IncidentDeliveryStatus.SIMULATED.value,
            )
            .values(
                status=IncidentDeliveryStatus.ACKNOWLEDGED.value,
                acknowledged_at=acknowledged_at,
            )
        )
        self._session.commit()
        return self.list_for_incident(incident_id)

    def list_for_incident(self, incident_id: int) -> list[IncidentDelivery]:
        rows = self._session.execute(_for_incident_stmt(incident_id)).all()
        return [_row_to_delivery(row) for row in rows]

    def list_for_role(
        self, role: ResponderRole, *, only_open: bool = False
    ) -> list[IncidentDelivery]:
        rows = self._session.execute(_for_role_stmt(role, only_open=only_open)).all()
        return [_row_to_delivery(row) for row in rows]
