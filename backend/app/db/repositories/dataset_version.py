"""SQLAlchemy-backed implementation of DatasetVersionRepository."""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.features import InputKind
from app.domain.scenario import DatasetVersion
from app.models.tables import dataset_version as dataset_version_table


def _row_to_domain(row: Row) -> DatasetVersion:
    return DatasetVersion(
        dataset_id=row.id,
        source=row.source,
        product=row.product,
        version=row.version,
        kind=InputKind(row.kind),
        region=row.region,
        attribution=row.attribution,
        license=row.license,
        coverage_start=row.coverage_start,
        coverage_end=row.coverage_end,
        available_at=row.available_at,
    )


def _upsert_stmt(dataset: DatasetVersion):
    values = {
        "id": dataset.dataset_id,
        "source": dataset.source,
        "product": dataset.product,
        "version": dataset.version,
        "kind": dataset.kind.value,
        "region": dataset.region,
        "attribution": dataset.attribution,
        "license": dataset.license,
        "coverage_start": dataset.coverage_start,
        "coverage_end": dataset.coverage_end,
        "available_at": dataset.available_at,
    }
    insert = pg_insert(dataset_version_table).values(**values)
    updates = {key: insert.excluded[key] for key in values if key != "id"}
    return insert.on_conflict_do_update(index_elements=["id"], set_=updates).returning(
        dataset_version_table
    )


def _get_stmt(dataset_id: str) -> Select:
    return select(dataset_version_table).where(dataset_version_table.c.id == dataset_id)


def _list_stmt(source: str | None) -> Select:
    stmt = select(dataset_version_table)
    if source is not None:
        stmt = stmt.where(dataset_version_table.c.source == source)
    return stmt.order_by(dataset_version_table.c.id)


class SqlDatasetVersionRepository:
    """Idempotent PostgreSQL repository for immutable dataset metadata."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, dataset: DatasetVersion) -> DatasetVersion:
        row = self._session.execute(_upsert_stmt(dataset)).one()
        self._session.commit()
        return _row_to_domain(row)

    def get(self, dataset_id: str) -> DatasetVersion | None:
        row = self._session.execute(_get_stmt(dataset_id)).first()
        return None if row is None else _row_to_domain(row)

    def list(self, *, source: str | None = None) -> list[DatasetVersion]:
        rows = self._session.execute(_list_stmt(source)).all()
        return [_row_to_domain(row) for row in rows]
