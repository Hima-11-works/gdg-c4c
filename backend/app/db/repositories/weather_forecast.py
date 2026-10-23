"""Persistence and query caching for coarse-location weather forecasts."""

from __future__ import annotations

from datetime import UTC, datetime

import h3
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.types import WeatherForecastObservation
from app.models.tables import weather_forecast_observation


def _observation(row) -> WeatherForecastObservation:
    return WeatherForecastObservation(
        sample_cell=row["sample_cell"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        valid_at=row["valid_at"],
        wind_speed=row["wind_speed"],
        wind_direction=row["wind_direction"],
        precipitation=row["precipitation"],
        boundary_layer_height=row["boundary_layer_height"],
        temperature=row["temperature"],
        humidity=row["humidity"],
        source=row["source"],
        source_model=row["source_model"],
        source_issued_at=row["source_issued_at"],
        received_at=row["received_at"],
    )


class SqlWeatherForecastRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def recently_collected_cells(
        self,
        sample_cells: list[str],
        *,
        source_model: str,
        received_after: datetime,
        valid_through: datetime,
    ) -> set[str]:
        if not sample_cells:
            return set()
        rows = self._session.execute(
            select(weather_forecast_observation.c.sample_cell)
            .where(
                weather_forecast_observation.c.sample_cell.in_(sample_cells),
                weather_forecast_observation.c.source == "open-meteo",
                weather_forecast_observation.c.source_model == source_model,
                weather_forecast_observation.c.received_at >= received_after,
                weather_forecast_observation.c.valid_at >= valid_through,
            )
            .distinct()
        ).all()
        self._session.commit()
        return {row.sample_cell for row in rows}

    def add_many(self, observations: list[WeatherForecastObservation]) -> tuple[int, int]:
        if not observations:
            return 0, 0
        values = []
        for observation in observations:
            # Native sample cells intentionally use WEATHER_H3_RESOLUTION,
            # which is configured independently of the fine map grid.
            if not h3.is_valid_cell(observation.sample_cell):
                raise ValueError(f"invalid weather sample cell {observation.sample_cell!r}")
            values.append(
                {
                    "sample_cell": observation.sample_cell,
                    "source": observation.source,
                    "source_model": observation.source_model,
                    "latitude": observation.latitude,
                    "longitude": observation.longitude,
                    "valid_at": observation.valid_at,
                    "wind_speed": observation.wind_speed,
                    "wind_direction": observation.wind_direction,
                    "precipitation": observation.precipitation,
                    "boundary_layer_height": observation.boundary_layer_height,
                    "temperature": observation.temperature,
                    "humidity": observation.humidity,
                    "source_issued_at": observation.source_issued_at,
                    "received_at": observation.received_at or datetime.now(UTC),
                }
            )
        statement = insert(weather_forecast_observation).values(values)
        statement = statement.on_conflict_do_update(
            constraint="uq_weather_forecast_location_model_validity",
            set_={
                column: getattr(statement.excluded, column)
                for column in (
                    "latitude",
                    "longitude",
                    "wind_speed",
                    "wind_direction",
                    "precipitation",
                    "boundary_layer_height",
                    "temperature",
                    "humidity",
                    "source_issued_at",
                    "received_at",
                )
            },
            where=weather_forecast_observation.c.received_at < statement.excluded.received_at,
        )
        try:
            inserted = self._session.execute(
                statement.returning(weather_forecast_observation.c.id)
            ).all()
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return len(inserted), len(observations) - len(inserted)

    def list_between(
        self,
        sample_cells: list[str],
        *,
        source_model: str,
        valid_from: datetime,
        valid_to: datetime,
        received_after: datetime | None = None,
    ) -> list[WeatherForecastObservation]:
        if not sample_cells:
            return []
        statement = select(weather_forecast_observation).where(
            weather_forecast_observation.c.sample_cell.in_(sample_cells),
            weather_forecast_observation.c.source == "open-meteo",
            weather_forecast_observation.c.source_model == source_model,
            weather_forecast_observation.c.valid_at >= valid_from,
            weather_forecast_observation.c.valid_at <= valid_to,
        )
        if received_after is not None:
            statement = statement.where(
                weather_forecast_observation.c.received_at >= received_after
            )
        rows = self._session.execute(
            statement.order_by(
                weather_forecast_observation.c.sample_cell,
                weather_forecast_observation.c.valid_at,
            )
        ).mappings().all()
        self._session.commit()
        return [_observation(row) for row in rows]

    def list_latest_current(
        self,
        *,
        valid_from: datetime,
        valid_through: datetime,
        sample_cells: list[str] | None = None,
    ) -> list[WeatherForecastObservation]:
        """Return one latest current-valid sample per native weather cell."""
        statement = select(weather_forecast_observation).where(
            weather_forecast_observation.c.source == "open-meteo",
            weather_forecast_observation.c.source_model == "best-match-v1",
            weather_forecast_observation.c.valid_at >= valid_from,
            weather_forecast_observation.c.valid_at <= valid_through,
        )
        if sample_cells is not None:
            if not sample_cells:
                return []
            statement = statement.where(
                weather_forecast_observation.c.sample_cell.in_(sample_cells)
            )
        rows = self._session.execute(
            statement.order_by(
                weather_forecast_observation.c.sample_cell,
                weather_forecast_observation.c.valid_at.desc(),
                weather_forecast_observation.c.received_at.desc(),
            )
        ).mappings().all()
        self._session.commit()
        latest: dict[str, WeatherForecastObservation] = {}
        for row in rows:
            sample_cell = row["sample_cell"]
            if sample_cell not in latest:
                latest[sample_cell] = _observation(row)
        return list(latest.values())
