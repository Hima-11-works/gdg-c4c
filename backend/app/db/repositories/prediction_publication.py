"""Atomic SQL publication storage for immutable prediction runs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.domain.features import DataMode, DatasetRef, FeatureQuality, InputKind
from app.domain.prediction import PredictionResult, PredictionRun
from app.models.tables import prediction_result, prediction_run

_RESULT_CHUNK_SIZE = 2_000


def _ref_values(ref: DatasetRef) -> dict[str, str]:
    return {
        "dataset_id": ref.dataset_id,
        "source": ref.source,
        "product": ref.product,
        "version": ref.version,
        "kind": ref.kind.value,
        "region": ref.region,
        "attribution": ref.attribution,
        "license": ref.license,
    }


def _refs_from_values(values: list[dict[str, str]]) -> tuple[DatasetRef, ...]:
    return tuple(
        DatasetRef(
            dataset_id=value["dataset_id"],
            source=value["source"],
            product=value["product"],
            version=value["version"],
            kind=InputKind(value["kind"]),
            region=value["region"],
            attribution=value["attribution"],
            license=value["license"],
        )
        for value in values
    )


def _quality_values(quality: FeatureQuality) -> dict:
    return {
        "coverage_fraction": quality.coverage_fraction,
        "observed_station_count": quality.observed_station_count,
        "max_observation_age_hours": quality.max_observation_age_hours,
        "missing_fields": list(quality.missing_fields),
        "warnings": list(quality.warnings),
    }


def _quality_from_values(value: dict) -> FeatureQuality:
    return FeatureQuality(
        coverage_fraction=value["coverage_fraction"],
        observed_station_count=value["observed_station_count"],
        max_observation_age_hours=value["max_observation_age_hours"],
        missing_fields=tuple(value["missing_fields"]),
        warnings=tuple(value["warnings"]),
    )


def _run_values(run: PredictionRun) -> dict:
    published_at = run.published_at or datetime.now(UTC)
    return {
        "id": run.run_id,
        "generated_at": run.generated_at,
        "published_at": published_at,
        "region": run.region,
        "mode": run.mode.value,
        "feature_run_id": run.feature_run_id,
        "feature_schema_version": run.feature_schema_version,
        "model_versions": list(run.model_versions),
        "scenario_id": run.scenario_id,
        "dataset_refs": [_ref_values(ref) for ref in run.dataset_refs],
    }


def _result_values(result: PredictionResult) -> dict:
    return {
        "run_id": result.run_id,
        "h3_cell": result.h3_cell,
        "horizon_hours": result.horizon_hours,
        "valid_at": result.valid_at,
        "baseline_pm25": result.baseline_pm25,
        "predicted_pm25": result.predicted_pm25,
        "lower_pm25": result.lower_pm25,
        "upper_pm25": result.upper_pm25,
        "pdi": result.pdi,
        "prediction_method": result.prediction_method,
        "model_version": result.model_version,
        "feature_schema_version": result.feature_schema_version,
        "input_kind": result.input_kind.value,
        "synthetic": result.synthetic,
        "quality": _quality_values(result.quality),
        "dataset_refs": [_ref_values(ref) for ref in result.dataset_refs],
        "feature_vector": dict(result.feature_vector),
    }


def _row_to_run(row: Row) -> PredictionRun:
    return PredictionRun(
        run_id=row.id,
        generated_at=row.generated_at,
        published_at=row.published_at,
        region=row.region,
        mode=DataMode(row.mode),
        feature_run_id=row.feature_run_id,
        feature_schema_version=row.feature_schema_version,
        model_versions=tuple(row.model_versions),
        scenario_id=row.scenario_id,
        dataset_refs=_refs_from_values(row.dataset_refs),
    )


def _row_to_result(row: Row) -> PredictionResult:
    return PredictionResult(
        run_id=row.run_id,
        h3_cell=row.h3_cell,
        horizon_hours=row.horizon_hours,
        valid_at=row.valid_at,
        baseline_pm25=row.baseline_pm25,
        predicted_pm25=row.predicted_pm25,
        lower_pm25=row.lower_pm25,
        upper_pm25=row.upper_pm25,
        pdi=row.pdi,
        prediction_method=row.prediction_method,
        model_version=row.model_version,
        feature_schema_version=row.feature_schema_version,
        input_kind=InputKind(row.input_kind),
        synthetic=row.synthetic,
        quality=_quality_from_values(row.quality),
        dataset_refs=_refs_from_values(row.dataset_refs),
        feature_vector=row.feature_vector,
    )


def _get_run_stmt(run_id: str) -> Select:
    return select(prediction_run).where(prediction_run.c.id == run_id)


def _latest_run_stmt(region: str | None) -> Select:
    stmt = select(prediction_run)
    if region is not None:
        stmt = stmt.where(prediction_run.c.region == region)
    return stmt.order_by(prediction_run.c.published_at.desc(), prediction_run.c.id).limit(1)


def _results_stmt(run_id: str) -> Select:
    return (
        select(prediction_result)
        .where(prediction_result.c.run_id == run_id)
        .order_by(prediction_result.c.h3_cell, prediction_result.c.horizon_hours)
    )


class SqlPredictionPublicationRepository:
    """Persists the run header and every result in a single transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def publish(self, run: PredictionRun, results: list[PredictionResult]) -> None:
        if not results:
            raise ValueError("a published prediction run must contain results")
        if any(result.run_id != run.run_id for result in results):
            raise ValueError("every prediction result must belong to the published run")
        identities = [(item.h3_cell, item.horizon_hours) for item in results]
        if len(set(identities)) != len(identities):
            raise ValueError("prediction results must be unique per cell and horizon")
        try:
            existing_row = self._session.execute(_get_run_stmt(run.run_id)).first()
            if existing_row is not None:
                existing_run = _row_to_run(existing_row)
                same_header = (
                    existing_run.generated_at == run.generated_at
                    and existing_run.region == run.region
                    and existing_run.mode == run.mode
                    and existing_run.feature_run_id == run.feature_run_id
                    and existing_run.feature_schema_version == run.feature_schema_version
                    and existing_run.model_versions == run.model_versions
                    and existing_run.scenario_id == run.scenario_id
                    and existing_run.dataset_refs == run.dataset_refs
                )
                persisted = self.list_results(run.run_id)
                def by_identity(rows):
                    return {(row.h3_cell, row.horizon_hours): row for row in rows}
                if not same_header or by_identity(persisted) != by_identity(results):
                    raise ValueError(
                        f"prediction run id {run.run_id!r} is immutable and already has different content"
                    )
                self._session.commit()
                return
            self._session.execute(pg_insert(prediction_run).values(**_run_values(run)))
            values = [_result_values(item) for item in results]
            for offset in range(0, len(values), _RESULT_CHUNK_SIZE):
                chunk = values[offset : offset + _RESULT_CHUNK_SIZE]
                self._session.execute(prediction_result.insert().values(chunk))
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def get_run(self, run_id: str) -> PredictionRun | None:
        row = self._session.execute(_get_run_stmt(run_id)).first()
        return None if row is None else _row_to_run(row)

    def latest_run(self, *, region: str | None = None) -> PredictionRun | None:
        row = self._session.execute(_latest_run_stmt(region)).first()
        return None if row is None else _row_to_run(row)

    def list_results(self, run_id: str) -> list[PredictionResult]:
        rows = self._session.execute(_results_stmt(run_id)).all()
        return [_row_to_result(row) for row in rows]
