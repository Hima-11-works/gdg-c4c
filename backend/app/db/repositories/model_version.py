"""SQLAlchemy-backed repository for versioned model metadata."""

from __future__ import annotations

from sqlalchemy import Select, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.training import ModelStatus, ModelVersion
from app.models.tables import model_version as model_version_table


def _values(model: ModelVersion) -> dict:
    return {
        "id": model.model_id,
        "artifact_uri": model.artifact_uri,
        "artifact_sha256": model.artifact_sha256,
        "feature_schema_version": model.feature_schema_version,
        "feature_names": list(model.feature_names),
        "trained_at": model.trained_at,
        "training_start": model.training_start,
        "training_end": model.training_end,
        "region": model.region,
        "horizon_hours": model.horizon_hours,
        "metrics": dict(model.metrics),
        "synthetic_only": model.synthetic_only,
        "status": model.status.value,
    }


def _upsert_stmt(model: ModelVersion):
    values = _values(model)
    insert = pg_insert(model_version_table).values(**values)
    updates = {key: insert.excluded[key] for key in values if key != "id"}
    return insert.on_conflict_do_update(index_elements=["id"], set_=updates).returning(
        model_version_table
    )


def _get_stmt(model_id: str) -> Select:
    return select(model_version_table).where(model_version_table.c.id == model_id)


def _list_stmt(
    region: str | None, horizon_hours: float | None, status: str | None
) -> Select:
    stmt = select(model_version_table)
    if region is not None:
        stmt = stmt.where(model_version_table.c.region == region)
    if horizon_hours is not None:
        stmt = stmt.where(model_version_table.c.horizon_hours == horizon_hours)
    if status is not None:
        stmt = stmt.where(model_version_table.c.status == status)
    return stmt.order_by(model_version_table.c.trained_at.desc(), model_version_table.c.id)


def _row_to_domain(row: Row) -> ModelVersion:
    return ModelVersion(
        model_id=row.id,
        artifact_uri=row.artifact_uri,
        artifact_sha256=row.artifact_sha256,
        feature_schema_version=row.feature_schema_version,
        feature_names=tuple(row.feature_names),
        trained_at=row.trained_at,
        training_start=row.training_start,
        training_end=row.training_end,
        region=row.region,
        horizon_hours=row.horizon_hours,
        metrics=row.metrics,
        synthetic_only=row.synthetic_only,
        status=ModelStatus(row.status),
    )


class SqlModelVersionRepository:
    """Idempotent PostgreSQL model registry."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, model: ModelVersion) -> ModelVersion:
        row = self._session.execute(_upsert_stmt(model)).one()
        self._session.commit()
        return _row_to_domain(row)

    def set_status(
        self,
        model_id: str,
        *,
        expected: ModelStatus,
        status: ModelStatus,
    ) -> ModelVersion:
        """Compare-and-set a lifecycle status, preventing stale operator writes."""

        try:
            row = self._session.execute(
                update(model_version_table)
                .where(
                    model_version_table.c.id == model_id,
                    model_version_table.c.status == expected.value,
                )
                .values(status=status.value)
                .returning(model_version_table)
            ).first()
            if row is None:
                raise ValueError(
                    f"model {model_id!r} was not in expected status {expected.value!r}"
                )
            self._session.commit()
            return _row_to_domain(row)
        except Exception:
            self._session.rollback()
            raise

    def activate(self, model_id: str, *, allow_retired: bool = False) -> ModelVersion:
        """Atomically retire the active horizon version and activate a validated one."""

        try:
            identity = self._session.execute(
                select(
                    model_version_table.c.region,
                    model_version_table.c.horizon_hours,
                ).where(model_version_table.c.id == model_id)
            ).first()
            if identity is None:
                raise ValueError(f"model {model_id!r} does not exist in the database registry")
            # Serialize activations for this region/horizon, including the first
            # activation when there is not yet a promoted row to lock.
            lock_key = f"model-activation:{identity.region}:{identity.horizon_hours}"
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": lock_key},
            )
            target_row = self._session.execute(
                select(model_version_table)
                .where(model_version_table.c.id == model_id)
                .with_for_update()
            ).first()
            if target_row is None:
                raise ValueError(f"model {model_id!r} does not exist in the database registry")
            target = _row_to_domain(target_row)
            allowed_statuses = {ModelStatus.VALIDATED, ModelStatus.PROMOTED}
            if allow_retired:
                allowed_statuses.add(ModelStatus.RETIRED)
            if target.status not in allowed_statuses:
                raise ValueError(f"{target.status.value} model cannot be activated")
            self._session.execute(
                update(model_version_table)
                .where(
                    model_version_table.c.region == target.region,
                    model_version_table.c.horizon_hours == target.horizon_hours,
                    model_version_table.c.status == ModelStatus.PROMOTED.value,
                    model_version_table.c.id != model_id,
                )
                .values(status=ModelStatus.RETIRED.value)
            )
            row = self._session.execute(
                update(model_version_table)
                .where(model_version_table.c.id == model_id)
                .values(status=ModelStatus.PROMOTED.value)
                .returning(model_version_table)
            ).one()
            self._session.commit()
            return _row_to_domain(row)
        except Exception:
            self._session.rollback()
            raise

    def get(self, model_id: str) -> ModelVersion | None:
        row = self._session.execute(_get_stmt(model_id)).first()
        return None if row is None else _row_to_domain(row)

    def list(
        self,
        *,
        region: str | None = None,
        horizon_hours: float | None = None,
        status: str | None = None,
    ) -> list[ModelVersion]:
        rows = self._session.execute(_list_stmt(region, horizon_hours, status)).all()
        return [_row_to_domain(row) for row in rows]
