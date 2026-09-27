"""SQLAlchemy-backed persistence for the federation demonstration runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Insert, Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.federation import (
    FederationParticipant,
    FederationRun,
    FederationRunStatus,
)
from app.models.tables import federation_participant as participant_table
from app.models.tables import federation_run as run_table


def _row_to_run(row: Row) -> FederationRun:
    return FederationRun(
        run_id=row.id,
        status=FederationRunStatus(row.status),
        participant_count=row.participant_count,
        region_scope=row.region_scope,
        feature_schema_version=row.feature_schema_version,
        horizons_hours=tuple(float(h) for h in row.horizons_hours),
        aggregate_artifact_path=row.aggregate_artifact_path,
        aggregate_artifact_sha256=row.aggregate_artifact_sha256,
        model_version_ids=tuple(row.model_version_ids),
        evaluation=dict(row.evaluation),
        raw_rows_exchanged_to_aggregator=row.raw_rows_exchanged_to_aggregator,
        provenance=dict(row.provenance),
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _run_values(run: FederationRun) -> dict:
    return {
        "id": run.run_id,
        "status": run.status.value,
        "participant_count": run.participant_count,
        "region_scope": run.region_scope,
        "feature_schema_version": run.feature_schema_version,
        "horizons_hours": list(run.horizons_hours),
        "aggregate_artifact_path": run.aggregate_artifact_path,
        "aggregate_artifact_sha256": run.aggregate_artifact_sha256,
        "model_version_ids": list(run.model_version_ids),
        "evaluation": run.evaluation,
        "raw_rows_exchanged_to_aggregator": run.raw_rows_exchanged_to_aggregator,
        "provenance": run.provenance,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _participant_values(run_id: str, participant: FederationParticipant) -> dict:
    return {
        "federation_run_id": run_id,
        "participant_id": participant.participant_id,
        "region_label": participant.region_label,
        "example_count": participant.example_count,
        "train_count": participant.train_count,
        "validation_count": participant.validation_count,
        "test_count": participant.test_count,
        "station_count": participant.station_count,
        "horizon_count": participant.horizon_count,
        "update_path": participant.update_path,
        "update_sha256": participant.update_sha256,
        "weight_fraction": participant.weight_fraction,
        "joined_at": participant.joined_at,
    }


def _row_to_participant(row: Row) -> FederationParticipant:
    return FederationParticipant(
        participant_id=row.participant_id,
        region_label=row.region_label,
        example_count=row.example_count,
        train_count=row.train_count,
        validation_count=row.validation_count,
        test_count=row.test_count,
        station_count=row.station_count,
        horizon_count=row.horizon_count,
        update_path=row.update_path,
        update_sha256=row.update_sha256,
        weight_fraction=row.weight_fraction,
        joined_at=row.joined_at,
    )


def _latest_stmt() -> Select:
    return (
        select(run_table)
        .order_by(run_table.c.finished_at.desc(), run_table.c.id)
        .limit(1)
    )


def _get_stmt(run_id: str) -> Select:
    return select(run_table).where(run_table.c.id == run_id)


def _participants_stmt(run_id: str) -> Select:
    return select(participant_table).where(
        participant_table.c.federation_run_id == run_id
    )


class SqlFederationRepository:
    """Implements the run record's storage contract against PostgreSQL.

    Saving is idempotent on the run id: re-running the same deterministic demo
    command records nothing new ( inserts are skipped for an existing run id
    and its confirmed participants), so a retried run leaves one run row.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self, run: FederationRun, participants: list[FederationParticipant]
    ) -> None:
        try:
            self._session.execute(
                pg_insert(run_table).values(**_run_values(run)).on_conflict_do_nothing()
            )
            for participant in participants:
                self._session.execute(
                    pg_insert(participant_table)
                    .values(**_participant_values(run.run_id, participant))
                    .on_conflict_do_nothing()
                )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def latest(self) -> FederationRun | None:
        row = self._session.execute(_latest_stmt()).first()
        return None if row is None else _row_to_run(row)

    def get(self, run_id: str) -> FederationRun | None:
        row = self._session.execute(_get_stmt(run_id)).first()
        return None if row is None else _row_to_run(row)

    def list_participants(self, run_id: str) -> list[FederationParticipant]:
        rows = self._session.execute(_participants_stmt(run_id)).all()
        return [_row_to_participant(row) for row in rows]
