"""PostgreSQL storage for sampled, licensed traffic observations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.environmental_observations import TrafficObservation
from app.models.tables import traffic_observation as traffic_observation_table


def _values(item: TrafficObservation) -> dict:
    return {
        "observation_id": item.observation_id,
        "dataset_id": item.dataset_id,
        "ingestion_run_id": item.ingestion_run_id,
        "source": item.source,
        "road_id": item.road_id,
        "h3_cell": item.h3_cell,
        "observed_at": item.observed_at,
        "available_at": item.available_at,
        "observed_speed_kph": item.observed_speed_kph,
        "free_flow_speed_kph": item.free_flow_speed_kph,
        "observed_free_flow_ratio": item.observed_free_flow_ratio,
        "confidence": item.confidence,
        "sampled_road_coverage_fraction": item.sampled_road_coverage_fraction,
        "quality": {"flags": list(item.quality_flags)},
    }


def _row_to_domain(row: Row) -> TrafficObservation:
    return TrafficObservation(
        observation_id=row.observation_id,
        dataset_id=row.dataset_id,
        ingestion_run_id=row.ingestion_run_id,
        source=row.source,
        road_id=row.road_id,
        h3_cell=row.h3_cell,
        observed_at=row.observed_at,
        available_at=row.available_at,
        observed_speed_kph=row.observed_speed_kph,
        free_flow_speed_kph=row.free_flow_speed_kph,
        observed_free_flow_ratio=row.observed_free_flow_ratio,
        confidence=row.confidence,
        sampled_road_coverage_fraction=row.sampled_road_coverage_fraction,
        quality_flags=tuple((row.quality or {}).get("flags", ())),
    )


class SqlTrafficObservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_many(self, observations: list[TrafficObservation]) -> tuple[int, int]:
        if not observations:
            return 0, 0
        insert = pg_insert(traffic_observation_table).values([_values(item) for item in observations])
        inserted = self._session.execute(
            insert.on_conflict_do_nothing(index_elements=["observation_id"])
            .returning(traffic_observation_table.c.observation_id)
        ).all()
        self._session.commit()
        saved = len(inserted)
        return saved, len(observations) - saved

    def list_for_window(
        self,
        *,
        observed_from,
        observed_to,
        available_by,
        h3_cells: list[str] | None = None,
    ) -> list[TrafficObservation]:
        stmt = select(traffic_observation_table).where(
            traffic_observation_table.c.observed_at >= observed_from,
            traffic_observation_table.c.observed_at <= observed_to,
            traffic_observation_table.c.available_at <= available_by,
        )
        if h3_cells is not None:
            stmt = stmt.where(traffic_observation_table.c.h3_cell.in_(h3_cells))
        rows = self._session.execute(
            stmt.order_by(traffic_observation_table.c.observed_at, traffic_observation_table.c.observation_id)
        ).all()
        return [_row_to_domain(row) for row in rows]
