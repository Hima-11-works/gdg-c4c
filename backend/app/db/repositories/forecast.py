"""SQLAlchemy-backed implementation of app.domain.repositories.ForecastRepository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Insert, Select, func, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.h3_grid import assert_valid_cell
from app.domain.types import Forecast
from app.models.tables import forecast as forecast_table


def _row_to_domain(row: Row) -> Forecast:
    return Forecast(
        id=row.id,
        h3_cell=row.h3_cell,
        generated_at=row.generated_at,
        forecast_time=row.forecast_time,
        forecast_hours=row.forecast_hours,
        predicted_pm25=row.predicted_pm25,
        confidence=row.confidence,
    )


def _values(forecast: Forecast) -> dict:
    return {
        "h3_cell": forecast.h3_cell,
        "generated_at": forecast.generated_at,
        "forecast_time": forecast.forecast_time,
        "forecast_hours": forecast.forecast_hours,
        "predicted_pm25": forecast.predicted_pm25,
        "confidence": forecast.confidence,
    }


def _insert_stmt(forecast: Forecast) -> Insert:
    return forecast_table.insert().values(**_values(forecast)).returning(forecast_table)


def _insert_many_stmt(forecasts: list[Forecast]) -> Insert:
    """One multi-row INSERT for a chunk of forecasts, instead of one round
    trip (and one commit) per forecast — see
    ForecastRepository.add_many's docstring for why that matters here.
    Callers must chunk (see _INSERT_CHUNK_ROWS) to stay under
    PostgreSQL's 65,535 bound-parameter limit per statement.
    """
    return forecast_table.insert().values([_values(f) for f in forecasts]).returning(forecast_table)


# _values() binds 6 parameters per row (id is server-generated); 65535 // 6
# = 10,922 rows max per statement. 5,000 leaves headroom for any future
# column additions without revisiting the math.
_INSERT_CHUNK_ROWS = 5_000


def _list_for_cell_stmt(h3_cell: str, generated_after: datetime | None) -> Select:
    stmt = select(forecast_table).where(forecast_table.c.h3_cell == h3_cell)
    if generated_after is not None:
        stmt = stmt.where(forecast_table.c.generated_at >= generated_after)
    return stmt.order_by(forecast_table.c.forecast_time)


def _latest_for_cell_stmt(h3_cell: str) -> Select:
    latest_run = (
        select(func.max(forecast_table.c.generated_at))
        .where(forecast_table.c.h3_cell == h3_cell)
        .scalar_subquery()
    )
    return (
        select(forecast_table)
        .where(forecast_table.c.h3_cell == h3_cell, forecast_table.c.generated_at == latest_run)
        .order_by(forecast_table.c.forecast_hours)
    )


def _latest_for_horizon_stmt(forecast_hours: float) -> Select:
    # Per cell, the most recent run that produced a forecast at this
    # horizon — cells can be on different run cadences, so this is a
    # per-cell max, not a single global "latest run" timestamp.
    latest_per_cell = (
        select(
            forecast_table.c.h3_cell,
            func.max(forecast_table.c.generated_at).label("max_generated_at"),
        )
        .where(forecast_table.c.forecast_hours == forecast_hours)
        .group_by(forecast_table.c.h3_cell)
        .subquery()
    )
    return (
        select(forecast_table)
        .join(
            latest_per_cell,
            (forecast_table.c.h3_cell == latest_per_cell.c.h3_cell)
            & (forecast_table.c.generated_at == latest_per_cell.c.max_generated_at)
            & (forecast_table.c.forecast_hours == forecast_hours),
        )
        .order_by(forecast_table.c.h3_cell)
    )


def _latest_for_horizon_in_cells_stmt(forecast_hours: float, cells: list[str]) -> Select:
    latest_per_cell = (
        select(
            forecast_table.c.h3_cell,
            func.max(forecast_table.c.generated_at).label("max_generated_at"),
        )
        .where(
            forecast_table.c.forecast_hours == forecast_hours,
            forecast_table.c.h3_cell.in_(cells),
        )
        .group_by(forecast_table.c.h3_cell)
        .subquery()
    )
    return (
        select(forecast_table)
        .join(
            latest_per_cell,
            (forecast_table.c.h3_cell == latest_per_cell.c.h3_cell)
            & (forecast_table.c.generated_at == latest_per_cell.c.max_generated_at)
            & (forecast_table.c.forecast_hours == forecast_hours),
        )
        .order_by(forecast_table.c.h3_cell)
    )


class SqlForecastRepository:
    """Implements app.domain.repositories.ForecastRepository against PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, forecast: Forecast) -> Forecast:
        assert_valid_cell(forecast.h3_cell, resolution=get_settings().h3_resolution)
        row = self._session.execute(_insert_stmt(forecast)).one()
        self._session.commit()
        return _row_to_domain(row)

    def add_many(self, forecasts: list[Forecast]) -> list[Forecast]:
        if not forecasts:
            return []
        resolution = get_settings().h3_resolution
        for forecast in forecasts:
            assert_valid_cell(forecast.h3_cell, resolution=resolution)

        # PostgreSQL's wire protocol caps a single statement at 65,535
        # bound parameters. The 24 quarter-hourly horizons of the current
        # pipeline (~120k rows at ~5k cells) blow far past that in one
        # INSERT (6 params/row), so chunk into executemany-sized
        # statements. Chunk boundaries stay within one uncommitted
        # transaction — the single commit() below keeps the batch
        # all-or-nothing, same as before.
        rows = []
        for chunk_start in range(0, len(forecasts), _INSERT_CHUNK_ROWS):
            chunk = forecasts[chunk_start : chunk_start + _INSERT_CHUNK_ROWS]
            rows.extend(self._session.execute(_insert_many_stmt(chunk)).all())
        self._session.commit()
        return [_row_to_domain(row) for row in rows]

    def list_for_cell(
        self, h3_cell: str, *, generated_after: datetime | None = None
    ) -> list[Forecast]:
        rows = self._session.execute(_list_for_cell_stmt(h3_cell, generated_after)).all()
        return [_row_to_domain(row) for row in rows]

    def latest_for_cell(self, h3_cell: str) -> list[Forecast]:
        rows = self._session.execute(_latest_for_cell_stmt(h3_cell)).all()
        return [_row_to_domain(row) for row in rows]

    def latest_for_horizon(self, hours: float) -> list[Forecast]:
        rows = self._session.execute(_latest_for_horizon_stmt(hours)).all()
        return [_row_to_domain(row) for row in rows]

    def latest_for_horizon_in_cells(self, hours: float, cells: list[str]) -> list[Forecast]:
        if not cells:
            return []
        rows = self._session.execute(_latest_for_horizon_in_cells_stmt(hours, cells)).all()
        return [_row_to_domain(row) for row in rows]
