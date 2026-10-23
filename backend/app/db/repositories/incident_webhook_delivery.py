"""SQL persistence for the optional authority webhook outbox."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.incidents import (
    IncidentWebhookDelivery,
    IncidentWebhookStatus,
    ResponderRole,
)
from app.models.tables import incident_webhook_delivery as delivery_table


def _row_to_delivery(row: Row) -> IncidentWebhookDelivery:
    return IncidentWebhookDelivery(
        id=row.id,
        incident_id=row.incident_id,
        audience_role=ResponderRole(row.audience_role),
        assignee=row.assignee,
        status=IncidentWebhookStatus(row.status),
        attempt_count=row.attempt_count,
        created_at=row.created_at,
        next_attempt_at=row.next_attempt_at,
        last_attempt_at=row.last_attempt_at,
        delivered_at=row.delivered_at,
        acknowledged_at=row.acknowledged_at,
        last_error=row.last_error,
    )


def _values(delivery: IncidentWebhookDelivery) -> dict:
    return {
        "incident_id": delivery.incident_id,
        "audience_role": delivery.audience_role.value,
        "assignee": delivery.assignee,
        "status": delivery.status.value,
        "attempt_count": delivery.attempt_count,
        "created_at": delivery.created_at,
        "next_attempt_at": delivery.next_attempt_at,
        "last_attempt_at": delivery.last_attempt_at,
        "delivered_at": delivery.delivered_at,
        "acknowledged_at": delivery.acknowledged_at,
        "last_error": delivery.last_error,
    }


def _due_stmt(
    *, now: datetime, limit: int, stale_sending_before: datetime, max_attempts: int
) -> Select:
    retryable = and_(
        delivery_table.c.status.in_(
            [IncidentWebhookStatus.QUEUED.value, IncidentWebhookStatus.RETRYING.value]
        ),
        or_(delivery_table.c.next_attempt_at.is_(None), delivery_table.c.next_attempt_at <= now),
        delivery_table.c.attempt_count < max_attempts,
    )
    abandoned = and_(
        delivery_table.c.status == IncidentWebhookStatus.SENDING.value,
        delivery_table.c.last_attempt_at <= stale_sending_before,
    )
    return (
        select(delivery_table)
        .where(or_(retryable, abandoned))
        .order_by(delivery_table.c.created_at, delivery_table.c.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


class SqlIncidentWebhookDeliveryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, delivery: IncidentWebhookDelivery) -> IncidentWebhookDelivery:
        row = self._session.execute(
            delivery_table.insert().values(**_values(delivery)).returning(delivery_table)
        ).one()
        self._session.commit()
        return _row_to_delivery(row)

    def claim_due(
        self,
        *,
        now: datetime,
        limit: int,
        stale_sending_before: datetime,
        max_attempts: int,
    ) -> list[IncidentWebhookDelivery]:
        rows = self._session.execute(
            _due_stmt(
                now=now,
                limit=limit,
                stale_sending_before=stale_sending_before,
                max_attempts=max_attempts,
            )
        ).all()
        claimed = []
        for row in rows:
            if (
                row.status == IncidentWebhookStatus.SENDING.value
                and row.attempt_count >= max_attempts
            ):
                self._session.execute(
                    delivery_table.update()
                    .where(delivery_table.c.id == row.id)
                    .values(
                        status=IncidentWebhookStatus.FAILED.value,
                        next_attempt_at=None,
                        last_error="worker stopped before completing the final attempt",
                    )
                )
                continue
            self._session.execute(
                delivery_table.update()
                .where(delivery_table.c.id == row.id)
                .values(
                    status=IncidentWebhookStatus.SENDING.value,
                    attempt_count=row.attempt_count + 1,
                    last_attempt_at=now,
                    next_attempt_at=None,
                )
            )
            claimed.append(
                replace(
                    _row_to_delivery(row),
                    status=IncidentWebhookStatus.SENDING,
                    attempt_count=row.attempt_count + 1,
                    last_attempt_at=now,
                    next_attempt_at=None,
                )
            )
        self._session.commit()
        return claimed

    def get(self, delivery_id: int) -> IncidentWebhookDelivery | None:
        row = self._session.execute(
            select(delivery_table).where(delivery_table.c.id == delivery_id)
        ).one_or_none()
        return None if row is None else _row_to_delivery(row)

    def mark_delivered(
        self, delivery_id: int, *, delivered_at: datetime
    ) -> IncidentWebhookDelivery | None:
        row = self.get(delivery_id)
        if row is None or row.status is IncidentWebhookStatus.ACKNOWLEDGED:
            return row
        if row.status is not IncidentWebhookStatus.SENDING:
            return row
        self._session.execute(
            delivery_table.update()
            .where(
                delivery_table.c.id == delivery_id,
                delivery_table.c.status == IncidentWebhookStatus.SENDING.value,
            )
            .values(
                status=IncidentWebhookStatus.DELIVERED.value,
                delivered_at=delivered_at,
                last_error=None,
            )
        )
        self._session.commit()
        return self.get(delivery_id)

    def mark_failure(
        self,
        delivery_id: int,
        *,
        attempted_at: datetime,
        next_attempt_at: datetime | None,
        error: str,
        status: IncidentWebhookStatus,
    ) -> IncidentWebhookDelivery | None:
        if status not in {IncidentWebhookStatus.RETRYING, IncidentWebhookStatus.FAILED}:
            raise ValueError("webhook failure status must be retrying or failed")
        self._session.execute(
            delivery_table.update()
            .where(
                delivery_table.c.id == delivery_id,
                delivery_table.c.status == IncidentWebhookStatus.SENDING.value,
            )
            .values(
                status=status.value,
                last_attempt_at=attempted_at,
                next_attempt_at=next_attempt_at,
                last_error=error[:500],
            )
        )
        self._session.commit()
        return self.get(delivery_id)

    def acknowledge(
        self, delivery_id: int, *, acknowledged_at: datetime
    ) -> IncidentWebhookDelivery | None:
        current = self.get(delivery_id)
        if current is None or current.status is IncidentWebhookStatus.ACKNOWLEDGED:
            return current
        if current.status not in {
            IncidentWebhookStatus.SENDING,
            IncidentWebhookStatus.DELIVERED,
        }:
            return current
        self._session.execute(
            delivery_table.update()
            .where(
                delivery_table.c.id == delivery_id,
                delivery_table.c.status.in_(
                    [
                        IncidentWebhookStatus.SENDING.value,
                        IncidentWebhookStatus.DELIVERED.value,
                    ]
                ),
            )
            .values(
                status=IncidentWebhookStatus.ACKNOWLEDGED.value,
                delivered_at=current.delivered_at or acknowledged_at,
                acknowledged_at=acknowledged_at,
                next_attempt_at=None,
                last_error=None,
            )
        )
        self._session.commit()
        return self.get(delivery_id)

    def list_for_incident(self, incident_id: int) -> list[IncidentWebhookDelivery]:
        rows = self._session.execute(
            select(delivery_table)
            .where(delivery_table.c.incident_id == incident_id)
            .order_by(delivery_table.c.created_at, delivery_table.c.id)
        ).all()
        return [_row_to_delivery(row) for row in rows]
