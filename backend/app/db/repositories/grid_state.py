"""SQLAlchemy-backed implementation of app.domain.repositories.GridStateRepository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.h3_grid import assert_valid_cell
from app.domain.types import GridState
from app.models.tables import grid_state as grid_state_table


def _row_to_domain(row: Row) -> GridState:
    return GridState(
        h3_cell=row.h3_cell,
        timestamp=row.timestamp,
        pm25=row.pm25,
        pdi=row.pdi,
        confidence=row.confidence,
        wind_speed=row.wind_speed,
        wind_direction=row.wind_direction,
    )


def _upsert_stmt(state: GridState) -> PgInsert:
    values = {
        "h3_cell": state.h3_cell,
        "timestamp": state.timestamp,
        "pm25": state.pm25,
        "pdi": state.pdi,
        "confidence": state.confidence,
        "wind_speed": state.wind_speed,
        "wind_direction": state.wind_direction,
    }
    stmt = pg_insert(grid_state_table).values(**values)
    update_values = {k: v for k, v in values.items() if k not in ("h3_cell", "timestamp")}
    return stmt.on_conflict_do_update(
        index_elements=[grid_state_table.c.h3_cell, grid_state_table.c.timestamp],
        set_=update_values,
    ).returning(grid_state_table)


def _get_stmt(h3_cell: str, timestamp: datetime) -> Select:
    return select(grid_state_table).where(
        grid_state_table.c.h3_cell == h3_cell, grid_state_table.c.timestamp == timestamp
    )


def _latest_stmt() -> Select:
    # DISTINCT ON is PostgreSQL-specific, which is fine: this whole layer is.
    return (
        select(grid_state_table)
        .distinct(grid_state_table.c.h3_cell)
        .order_by(grid_state_table.c.h3_cell, grid_state_table.c.timestamp.desc())
    )


class SqlGridStateRepository:
    """Implements app.domain.repositories.GridStateRepository against PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, state: GridState) -> GridState:
        assert_valid_cell(state.h3_cell, resolution=get_settings().h3_resolution)
        row = self._session.execute(_upsert_stmt(state)).one()
        self._session.commit()
        return _row_to_domain(row)

    def get(self, h3_cell: str, timestamp: datetime) -> GridState | None:
        row = self._session.execute(_get_stmt(h3_cell, timestamp)).first()
        return _row_to_domain(row) if row else None

    def latest(self) -> list[GridState]:
        rows = self._session.execute(_latest_stmt()).all()
        return [_row_to_domain(row) for row in rows]
