"""SQLAlchemy-backed implementation of app.domain.repositories.GridStateRepository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.features import DataMode
from app.domain.h3_grid import assert_valid_cell
from app.domain.types import GridState
from app.models.tables import grid_state as grid_state_table
from app.models.tables import prediction_run


def _row_to_domain(row: Row) -> GridState:
    return GridState(
        h3_cell=row.h3_cell,
        timestamp=row.timestamp,
        pm25=row.pm25,
        pdi=row.pdi,
        pdi_factors=row.pdi_factors,
        confidence=row.confidence,
        wind_speed=row.wind_speed,
        wind_direction=row.wind_direction,
    )


def _values(state: GridState) -> dict:
    return {
        "h3_cell": state.h3_cell,
        "timestamp": state.timestamp,
        "pm25": state.pm25,
        "pdi": state.pdi,
        "pdi_factors": state.pdi_factors,
        "confidence": state.confidence,
        "wind_speed": state.wind_speed,
        "wind_direction": state.wind_direction,
    }


_UPDATE_COLUMNS = ("pm25", "pdi", "pdi_factors", "confidence", "wind_speed", "wind_direction")


def _upsert_stmt(state: GridState) -> PgInsert:
    values = _values(state)
    stmt = pg_insert(grid_state_table).values(**values)
    update_values = {k: v for k, v in values.items() if k in _UPDATE_COLUMNS}
    return stmt.on_conflict_do_update(
        index_elements=[grid_state_table.c.h3_cell, grid_state_table.c.timestamp],
        set_=update_values,
    ).returning(grid_state_table)


def _upsert_many_stmt(states: list[GridState]) -> PgInsert:
    """One multi-row INSERT ... ON CONFLICT DO UPDATE for the whole batch,
    instead of one round trip (and one transaction commit) per state — see
    GridStateRepository.upsert_many's docstring for why that matters here.
    """
    stmt = pg_insert(grid_state_table).values([_values(state) for state in states])
    update_values = {col: getattr(stmt.excluded, col) for col in _UPDATE_COLUMNS}
    return stmt.on_conflict_do_update(
        index_elements=[grid_state_table.c.h3_cell, grid_state_table.c.timestamp],
        set_=update_values,
    ).returning(grid_state_table)


def _get_stmt(h3_cell: str, timestamp: datetime) -> Select:
    return select(grid_state_table).where(
        grid_state_table.c.h3_cell == h3_cell, grid_state_table.c.timestamp == timestamp
    )


def _mode_timestamps(mode: DataMode):
    """Legacy rows are visible only when a publication of the same mode owns them."""
    return select(prediction_run.c.generated_at).where(
        prediction_run.c.mode == mode.value
    )


def _latest_stmt(mode: DataMode | None = None) -> Select:
    # DISTINCT ON is PostgreSQL-specific, which is fine: this whole layer is.
    stmt = select(grid_state_table)
    if mode is not None:
        stmt = stmt.where(grid_state_table.c.timestamp.in_(_mode_timestamps(mode)))
    return (
        stmt
        .distinct(grid_state_table.c.h3_cell)
        .order_by(grid_state_table.c.h3_cell, grid_state_table.c.timestamp.desc())
    )


def _latest_in_cells_stmt(cells: list[str], mode: DataMode | None = None) -> Select:
    conditions = [grid_state_table.c.h3_cell.in_(cells)]
    if mode is not None:
        conditions.append(grid_state_table.c.timestamp.in_(_mode_timestamps(mode)))
    return (
        select(grid_state_table)
        .where(*conditions)
        .distinct(grid_state_table.c.h3_cell)
        .order_by(grid_state_table.c.h3_cell, grid_state_table.c.timestamp.desc())
    )


def _latest_for_cell_stmt(h3_cell: str, mode: DataMode | None = None) -> Select:
    conditions = [grid_state_table.c.h3_cell == h3_cell]
    if mode is not None:
        conditions.append(grid_state_table.c.timestamp.in_(_mode_timestamps(mode)))
    return (
        select(grid_state_table)
        .where(*conditions)
        .order_by(grid_state_table.c.timestamp.desc())
        .limit(1)
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

    def upsert_many(self, states: list[GridState]) -> list[GridState]:
        if not states:
            return []
        resolution = get_settings().h3_resolution
        for state in states:
            assert_valid_cell(state.h3_cell, resolution=resolution)
        rows = self._session.execute(_upsert_many_stmt(states)).all()
        self._session.commit()
        return [_row_to_domain(row) for row in rows]

    def get(self, h3_cell: str, timestamp: datetime) -> GridState | None:
        row = self._session.execute(_get_stmt(h3_cell, timestamp)).first()
        return _row_to_domain(row) if row else None

    def latest(self, *, mode: DataMode | None = None) -> list[GridState]:
        rows = self._session.execute(_latest_stmt(mode)).all()
        return [_row_to_domain(row) for row in rows]

    def latest_in_cells(
        self, cells: list[str], *, mode: DataMode | None = None
    ) -> list[GridState]:
        if not cells:
            return []
        rows = self._session.execute(_latest_in_cells_stmt(cells, mode)).all()
        return [_row_to_domain(row) for row in rows]

    def latest_for_cell(
        self, h3_cell: str, *, mode: DataMode | None = None
    ) -> GridState | None:
        row = self._session.execute(_latest_for_cell_stmt(h3_cell, mode)).first()
        return _row_to_domain(row) if row else None
