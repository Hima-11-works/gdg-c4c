"""SQLAlchemy-backed store for versioned static cell features.

Population / road / land-cover values change on a census or release cadence, not
per run, so they are stored once per ``dataset_id`` and read back by the
publication path for the run being published. A cell with no row in the current
dataset is *missing* for that run — never a zero.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domain.features import CellStaticFeatures, DatasetRef
from app.models.tables import static_cell_feature as static_cell_feature_table


def _row_to_features(row, refs: tuple[DatasetRef, ...]) -> CellStaticFeatures:
    return CellStaticFeatures(
        h3_cell=row.h3_cell,
        population_count=row.population_count,
        population_density_per_km2=row.population_density_per_km2,
        road_length_km_by_class=dict(row.road_length_km_by_class or {}),
        major_road_distance_km=row.major_road_distance_km,
        built_up_fraction=row.built_up_fraction,
        vegetation_fraction=row.vegetation_fraction,
        bare_soil_fraction=row.bare_soil_fraction,
        industrial_fraction=row.industrial_fraction,
        coverage_fraction=float(row.coverage_fraction),
        dataset_refs=refs,
        valid_from=row.valid_from,
        available_at=row.available_at,
    )


def _values(dataset_id: str, ingestion_run_id: str, features: CellStaticFeatures) -> dict:
    return {
        "dataset_id": dataset_id,
        "h3_cell": features.h3_cell,
        "ingestion_run_id": ingestion_run_id,
        "population_count": features.population_count,
        "population_density_per_km2": features.population_density_per_km2,
        "road_length_km_by_class": dict(features.road_length_km_by_class),
        "major_road_distance_km": features.major_road_distance_km,
        "built_up_fraction": features.built_up_fraction,
        "vegetation_fraction": features.vegetation_fraction,
        "bare_soil_fraction": features.bare_soil_fraction,
        "industrial_fraction": features.industrial_fraction,
        "coverage_fraction": features.coverage_fraction,
        "valid_from": features.valid_from,
        "available_at": features.available_at,
    }


def _for_dataset_stmt(dataset_id: str, *, h3_cells: list[str] | None) -> Select:
    stmt = select(static_cell_feature_table).where(
        static_cell_feature_table.c.dataset_id == dataset_id
    )
    if h3_cells is not None:
        stmt = stmt.where(static_cell_feature_table.c.h3_cell.in_(h3_cells))
    return stmt.order_by(static_cell_feature_table.c.h3_cell)


class SqlStaticCellFeatureRepository:
    """Implements app.domain.repositories.StaticCellFeatureRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_many(
        self,
        *,
        dataset_id: str,
        ingestion_run_id: str,
        features: list[CellStaticFeatures],
    ) -> tuple[int, int]:
        """Idempotent upsert keyed by (dataset_id, h3_cell).

        Re-importing the same dataset refreshes its rows rather than duplicating
        them; a *different* dataset_id never touches an older one.
        """
        if not features:
            return 0, 0
        rows = [_values(dataset_id, ingestion_run_id, item) for item in features]
        statement = pg_insert(static_cell_feature_table).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[
                static_cell_feature_table.c.dataset_id,
                static_cell_feature_table.c.h3_cell,
            ],
            set_={
                "ingestion_run_id": statement.excluded.ingestion_run_id,
                "population_count": statement.excluded.population_count,
                "population_density_per_km2": statement.excluded.population_density_per_km2,
                "road_length_km_by_class": statement.excluded.road_length_km_by_class,
                "major_road_distance_km": statement.excluded.major_road_distance_km,
                "built_up_fraction": statement.excluded.built_up_fraction,
                "vegetation_fraction": statement.excluded.vegetation_fraction,
                "bare_soil_fraction": statement.excluded.bare_soil_fraction,
                "industrial_fraction": statement.excluded.industrial_fraction,
                "coverage_fraction": statement.excluded.coverage_fraction,
                "valid_from": statement.excluded.valid_from,
                "available_at": statement.excluded.available_at,
            },
        )
        self._session.execute(statement)
        self._session.commit()
        return len(rows), 0

    def list_for_dataset(
        self,
        dataset_id: str,
        *,
        h3_cells: list[str] | None = None,
        available_by: datetime | None = None,
        refs: tuple[DatasetRef, ...] = (),
    ) -> list[CellStaticFeatures]:
        """Rows of one dataset, optionally only those usable at `available_by`.

        The dataset itself (version, license, availability) is a
        `dataset_version` row; this repository only holds the per-cell values.
        """
        stmt = _for_dataset_stmt(dataset_id, h3_cells=h3_cells)
        if available_by is not None:
            stmt = stmt.where(static_cell_feature_table.c.available_at <= available_by)
        rows = self._session.execute(stmt).all()
        return [_row_to_features(row, refs) for row in rows]
