"""SQLAlchemy-backed store for modeled forecast weather.

Separate from ``weather_reading`` (observations) because a forecast's issue time
and valid time differ. The publication path filters on ``issued_at <= prediction
time``, so a horizon can never be filled with a forecast that did not exist when
the prediction was made.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domain.environmental_observations import WeatherForecast
from app.models.tables import weather_forecast as weather_forecast_table


def _row_to_domain(row) -> WeatherForecast:
    return WeatherForecast(
        forecast_id=row.forecast_id,
        dataset_id=row.dataset_id,
        ingestion_run_id=row.ingestion_run_id,
        source=row.source,
        h3_cell=row.h3_cell,
        issued_at=row.issued_at,
        valid_at=row.valid_at,
        horizon_hours=row.horizon_hours,
        wind_speed_ms=row.wind_speed_ms,
        wind_direction_deg=row.wind_direction_deg,
        precipitation_mm=row.precipitation_mm,
        boundary_layer_height_m=row.boundary_layer_height_m,
        temperature_c=row.temperature_c,
        relative_humidity_pct=row.relative_humidity_pct,
    )


def _values(item: WeatherForecast) -> dict:
    return {
        "forecast_id": item.forecast_id,
        "dataset_id": item.dataset_id,
        "ingestion_run_id": item.ingestion_run_id,
        "source": item.source,
        "h3_cell": item.h3_cell,
        "issued_at": item.issued_at,
        "valid_at": item.valid_at,
        "horizon_hours": item.horizon_hours,
        "wind_speed_ms": item.wind_speed_ms,
        "wind_direction_deg": item.wind_direction_deg,
        "precipitation_mm": item.precipitation_mm,
        "boundary_layer_height_m": item.boundary_layer_height_m,
        "temperature_c": item.temperature_c,
        "relative_humidity_pct": item.relative_humidity_pct,
    }


def _usable_stmt(
    *,
    issued_by: datetime,
    valid_from: datetime,
    valid_to: datetime,
    h3_cells: list[str] | None,
) -> Select:
    stmt = select(weather_forecast_table).where(
        # Issued by the prediction time (no lookahead) ...
        weather_forecast_table.c.issued_at <= issued_by,
        # ... and covering the horizon being built.
        weather_forecast_table.c.valid_at >= valid_from,
        weather_forecast_table.c.valid_at <= valid_to,
    )
    if h3_cells is not None:
        stmt = stmt.where(weather_forecast_table.c.h3_cell.in_(h3_cells))
    return stmt.order_by(
        weather_forecast_table.c.valid_at, weather_forecast_table.c.forecast_id
    )


class SqlWeatherForecastRepository:
    """Implements app.domain.repositories.WeatherForecastRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_many(self, forecasts: list[WeatherForecast]) -> tuple[int, int]:
        """Insert idempotently on forecast_id; return (inserted, already_seen)."""
        if not forecasts:
            return 0, 0
        rows = [_values(item) for item in forecasts]
        statement = (
            pg_insert(weather_forecast_table)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[weather_forecast_table.c.forecast_id])
        )
        result = self._session.execute(statement)
        self._session.commit()
        inserted = int(result.rowcount or 0)
        return inserted, len(rows) - inserted

    def list_usable(
        self,
        *,
        issued_by: datetime,
        valid_from: datetime,
        valid_to: datetime,
        h3_cells: list[str] | None = None,
    ) -> list[WeatherForecast]:
        """Forecasts that existed by `issued_by` and are valid in the window."""
        rows = self._session.execute(
            _usable_stmt(
                issued_by=issued_by,
                valid_from=valid_from,
                valid_to=valid_to,
                h3_cells=h3_cells,
            )
        ).all()
        return [_row_to_domain(row) for row in rows]

    def count_for_run(self, ingestion_run_id: str) -> int:
        row = self._session.execute(
            select(weather_forecast_table.c.forecast_id)
            .where(weather_forecast_table.c.ingestion_run_id == ingestion_run_id)
            .limit(1)
        ).first()
        return 0 if row is None else 1


__all__ = ["SqlWeatherForecastRepository"]
