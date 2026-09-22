"""SQLAlchemy-backed persistence for typed feature snapshots."""

from __future__ import annotations

from dataclasses import fields

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.features import (
    CellFeatureVector,
    DatasetRef,
    FeatureQuality,
    FeatureSnapshot,
    InputKind,
)
from app.models.tables import cell_feature_snapshot as feature_snapshot_table


def _vector_values(snapshot: FeatureSnapshot) -> dict[str, float | None]:
    return {
        field.name: getattr(snapshot.vector, field.name)
        for field in fields(CellFeatureVector)
    }


def _quality_values(snapshot: FeatureSnapshot) -> dict:
    quality = snapshot.quality
    return {
        "coverage_fraction": quality.coverage_fraction,
        "observed_station_count": quality.observed_station_count,
        "max_observation_age_hours": quality.max_observation_age_hours,
        "missing_fields": list(quality.missing_fields),
        "warnings": list(quality.warnings),
    }


def _refs_values(snapshot: FeatureSnapshot) -> list[dict[str, str]]:
    return [
        {
            "dataset_id": ref.dataset_id,
            "source": ref.source,
            "product": ref.product,
            "version": ref.version,
            "kind": ref.kind.value,
            "region": ref.region,
            "attribution": ref.attribution,
            "license": ref.license,
        }
        for ref in snapshot.dataset_refs
    ]


def _snapshot_id(run_id: str, snapshot: FeatureSnapshot) -> str:
    return f"{run_id}:{snapshot.h3_cell}:{snapshot.horizon_hours:g}"


def _values(run_id: str, snapshot: FeatureSnapshot) -> dict:
    return {
        "id": _snapshot_id(run_id, snapshot),
        "run_id": run_id,
        "h3_cell": snapshot.h3_cell,
        "issued_at": snapshot.issued_at,
        "valid_at": snapshot.valid_at,
        "horizon_hours": snapshot.horizon_hours,
        "feature_schema_version": snapshot.feature_schema_version,
        "vector": _vector_values(snapshot),
        "quality": _quality_values(snapshot),
        "dataset_refs": _refs_values(snapshot),
    }


def _upsert_stmt(run_id: str, snapshots: list[FeatureSnapshot]):
    if not snapshots:
        raise ValueError("snapshots must not be empty")
    values = [_values(run_id, snapshot) for snapshot in snapshots]
    insert = pg_insert(feature_snapshot_table).values(values)
    updates = {
        key: insert.excluded[key]
        for key in values[0]
        if key not in {"id", "run_id", "h3_cell", "horizon_hours"}
    }
    return insert.on_conflict_do_update(
        index_elements=["run_id", "h3_cell", "horizon_hours"], set_=updates
    ).returning(feature_snapshot_table)


def _list_for_run_stmt(run_id: str) -> Select:
    return (
        select(feature_snapshot_table)
        .where(feature_snapshot_table.c.run_id == run_id)
        .order_by(feature_snapshot_table.c.h3_cell, feature_snapshot_table.c.horizon_hours)
    )


def _row_to_domain(row: Row) -> FeatureSnapshot:
    refs = tuple(
        DatasetRef(
            dataset_id=ref["dataset_id"],
            source=ref["source"],
            product=ref["product"],
            version=ref["version"],
            kind=InputKind(ref["kind"]),
            region=ref["region"],
            attribution=ref["attribution"],
            license=ref["license"],
        )
        for ref in row.dataset_refs
    )
    quality = row.quality
    return FeatureSnapshot(
        h3_cell=row.h3_cell,
        issued_at=row.issued_at,
        valid_at=row.valid_at,
        horizon_hours=row.horizon_hours,
        feature_schema_version=row.feature_schema_version,
        vector=CellFeatureVector(**row.vector),
        quality=FeatureQuality(
            coverage_fraction=quality["coverage_fraction"],
            observed_station_count=quality["observed_station_count"],
            max_observation_age_hours=quality["max_observation_age_hours"],
            missing_fields=tuple(quality["missing_fields"]),
            warnings=tuple(quality["warnings"]),
        ),
        dataset_refs=refs,
    )


class SqlFeatureSnapshotRepository:
    """Idempotent PostgreSQL repository for as-of feature projections."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_many(self, run_id: str, snapshots: list[FeatureSnapshot]) -> list[FeatureSnapshot]:
        rows = self._session.execute(_upsert_stmt(run_id, snapshots)).all()
        self._session.commit()
        return [_row_to_domain(row) for row in rows]

    def list_for_run(self, run_id: str) -> list[FeatureSnapshot]:
        rows = self._session.execute(_list_for_run_stmt(run_id)).all()
        return [_row_to_domain(row) for row in rows]
