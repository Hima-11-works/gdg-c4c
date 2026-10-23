"""SQLAlchemy-backed implementation of app.domain.repositories.AlertRepository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Insert, Select, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.features import DataMode
from app.domain.h3_grid import assert_valid_cell
from app.domain.types import Alert, AlertSeverity
from app.models.tables import alert as alert_table
from app.models.tables import prediction_run


def _row_to_domain(row: Row) -> Alert:
    return Alert(
        id=row.id,
        h3_cell=row.h3_cell,
        severity=AlertSeverity(row.severity),
        message=row.message,
        created_at=row.created_at,
        current_pm25=row.current_pm25,
        forecast_pm25=row.forecast_pm25,
        forecast_hours=row.forecast_hours,
        confidence=row.confidence,
        forecast_time=row.forecast_time,
        run_id=row.run_id,
    )


def _insert_stmt(alert: Alert) -> Insert:
    return (
        alert_table.insert()
        .values(
            h3_cell=alert.h3_cell,
            severity=alert.severity.value,
            message=alert.message,
            created_at=alert.created_at,
            current_pm25=alert.current_pm25,
            forecast_pm25=alert.forecast_pm25,
            forecast_hours=alert.forecast_hours,
            confidence=alert.confidence,
            forecast_time=alert.forecast_time,
            run_id=alert.run_id,
        )
        .returning(alert_table)
    )


def _list_active_stmt(since: datetime, mode: DataMode | None = None) -> Select:
    conditions = [alert_table.c.created_at >= since]
    if mode is not None:
        eligible_runs = select(prediction_run.c.id).where(
            prediction_run.c.mode == mode.value
        )
        conditions.extend(
            (
                alert_table.c.run_id.is_not(None),
                alert_table.c.run_id.in_(eligible_runs),
            )
        )
    return (
        select(alert_table)
        .where(*conditions)
        .order_by(alert_table.c.created_at.desc())
    )


class SqlAlertRepository:
    """Implements app.domain.repositories.AlertRepository against PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, alert: Alert) -> Alert:
        assert_valid_cell(alert.h3_cell, resolution=get_settings().h3_resolution)
        row = self._session.execute(_insert_stmt(alert)).one()
        self._session.commit()
        return _row_to_domain(row)

    def list_active(self, *, since: datetime, mode: DataMode | None = None) -> list[Alert]:
        rows = self._session.execute(_list_active_stmt(since, mode)).all()
        return [_row_to_domain(row) for row in rows]
