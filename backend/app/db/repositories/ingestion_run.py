"""SQLAlchemy-backed implementation of IngestionRunRepository."""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.scenario import IngestionRun, IngestionRunStatus
from app.models.tables import ingestion_run as ingestion_run_table


def _row_to_domain(row: Row) -> IngestionRun:
    return IngestionRun(
        run_id=row.id,
        dataset_id=row.dataset_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        fetched_at=row.fetched_at,
        status=IngestionRunStatus(row.status),
        errors=tuple(row.errors or ()),
        simulation_id=row.simulation_id,
    )


def _upsert_stmt(run: IngestionRun):
    values = {
        "id": run.run_id,
        "dataset_id": run.dataset_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "fetched_at": run.fetched_at,
        "status": run.status.value,
        "errors": list(run.errors),
        "simulation_id": run.simulation_id,
    }
    insert = pg_insert(ingestion_run_table).values(**values)
    updates = {key: insert.excluded[key] for key in values if key != "id"}
    return insert.on_conflict_do_update(index_elements=["id"], set_=updates).returning(
        ingestion_run_table
    )


def _get_stmt(run_id: str) -> Select:
    return select(ingestion_run_table).where(ingestion_run_table.c.id == run_id)


def _list_stmt(dataset_id: str | None) -> Select:
    stmt = select(ingestion_run_table)
    if dataset_id is not None:
        stmt = stmt.where(ingestion_run_table.c.dataset_id == dataset_id)
    return stmt.order_by(ingestion_run_table.c.started_at, ingestion_run_table.c.id)


class SqlIngestionRunRepository:
    """Idempotent PostgreSQL repository for provider/scenario executions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, run: IngestionRun) -> IngestionRun:
        row = self._session.execute(_upsert_stmt(run)).one()
        self._session.commit()
        return _row_to_domain(row)

    def get(self, run_id: str) -> IngestionRun | None:
        row = self._session.execute(_get_stmt(run_id)).first()
        return None if row is None else _row_to_domain(row)

    def list(self, *, dataset_id: str | None = None) -> list[IngestionRun]:
        rows = self._session.execute(_list_stmt(dataset_id)).all()
        return [_row_to_domain(row) for row in rows]
