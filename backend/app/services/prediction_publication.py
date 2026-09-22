"""Build and atomically publish a feature-aware prediction run."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from app.domain.features import DataMode, DatasetRef, FeatureSnapshot, InputKind
from app.domain.prediction import PredictionResult, PredictionRun
from app.domain.repositories import ModelVersionRepository, PredictionPublicationRepository
from app.domain.training import ModelStatus, ModelVersion
from app.services.model_training import predict_residual, prediction_interval


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def _artifact_model(model_version: ModelVersion, horizon: float) -> dict | None:
    """Load a local content-addressed artifact after verifying its registry hash."""
    path = Path(model_version.artifact_uri)
    if not path.is_file():
        return None
    artifact = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    if digest != model_version.artifact_sha256 or artifact.get("artifact_sha256") != digest:
        raise ValueError(f"model artifact hash mismatch: {model_version.model_id}")
    if artifact.get("feature_schema_version") != model_version.feature_schema_version:
        raise ValueError(f"model artifact feature schema mismatch: {model_version.model_id}")
    if artifact.get("region") != model_version.region:
        raise ValueError(f"model artifact region mismatch: {model_version.model_id}")
    if bool(artifact.get("synthetic_only")) != model_version.synthetic_only:
        raise ValueError(f"model artifact provenance mismatch: {model_version.model_id}")
    selected = artifact.get("models", {}).get(str(horizon))
    if selected is None:
        return None
    if tuple(selected.get("feature_names", ())) != model_version.feature_names:
        raise ValueError(f"model artifact feature list mismatch: {model_version.model_id}")
    return selected


def _refs_unique(refs: list[DatasetRef]) -> tuple[DatasetRef, ...]:
    by_id = {ref.dataset_id: ref for ref in refs}
    return tuple(by_id[key] for key in sorted(by_id))


def _input_kind(mode: DataMode, refs: tuple[DatasetRef, ...], horizon: float) -> InputKind:
    kinds = {ref.kind for ref in refs}
    if mode is DataMode.DEMO or InputKind.SYNTHETIC in kinds:
        return InputKind.SYNTHETIC
    if horizon == 0 and InputKind.OBSERVED in kinds:
        return InputKind.OBSERVED
    if InputKind.MODELED in kinds or horizon > 0:
        return InputKind.MODELED
    return InputKind.DERIVED


def assert_live_snapshots_available(mode: DataMode, snapshots: list[FeatureSnapshot]) -> None:
    """Fail closed when a live publication is built from demo or absent observations."""

    if mode is not DataMode.LIVE:
        return
    if any(
        ref.kind is InputKind.SYNTHETIC for item in snapshots for ref in item.dataset_refs
    ):
        raise ValueError("synthetic feature inputs cannot be published as a live run")
    has_observed_current = any(
        item.horizon_hours == 0
        and item.vector.current_pm25 is not None
        and item.quality.observed_station_count > 0
        for item in snapshots
    )
    if not has_observed_current:
        raise ValueError(
            "live publication unavailable: no current PM2.5 observation has supporting stations"
        )


class PredictionPublicationService:
    """Apply compatible residual models over a supplied baseline, then publish."""

    def __init__(
        self,
        publication_repository: PredictionPublicationRepository,
        model_repository: ModelVersionRepository | None = None,
    ) -> None:
        self._publication_repository = publication_repository
        self._model_repository = model_repository

    def publish(
        self,
        *,
        run_id: str,
        feature_run_id: str,
        region: str,
        mode: DataMode,
        generated_at: datetime,
        snapshots: list[FeatureSnapshot],
        baseline_by_cell_horizon: Mapping[tuple[str, float], float] | None = None,
        pdi_by_cell: Mapping[str, float] | None = None,
        scenario_id: str | None = None,
    ) -> tuple[PredictionRun, list[PredictionResult]]:
        if not snapshots:
            raise ValueError("cannot publish a run without feature snapshots")
        if any(snapshot.feature_schema_version != snapshots[0].feature_schema_version for snapshot in snapshots):
            raise ValueError("all snapshots in one run must use the same feature schema")
        identities = [(item.h3_cell, item.horizon_hours) for item in snapshots]
        if len(set(identities)) != len(identities):
            raise ValueError("feature snapshots must be unique per cell and horizon")
        assert_live_snapshots_available(mode, snapshots)

        baseline_by_cell_horizon = baseline_by_cell_horizon or {}
        pdi_by_cell = pdi_by_cell or {}
        available_models: dict[float, tuple[ModelVersion, dict] | None] = {}
        horizons = sorted({item.horizon_hours for item in snapshots if item.horizon_hours > 0})
        for horizon in horizons:
            available_models[horizon] = self._select_model(
                region=region,
                horizon=horizon,
                feature_schema_version=snapshots[0].feature_schema_version,
                mode=mode,
            )

        all_refs = _refs_unique([ref for item in snapshots for ref in item.dataset_refs])
        result_rows: list[PredictionResult] = []
        used_models: set[str] = set()
        for snapshot in snapshots:
            horizon = snapshot.horizon_hours
            vector = {
                item.name: getattr(snapshot.vector, item.name)
                for item in fields(snapshot.vector)
            }
            observed_pm25 = vector.get("current_pm25")
            baseline = (
                observed_pm25
                if horizon == 0
                else baseline_by_cell_horizon.get((snapshot.h3_cell, horizon), observed_pm25)
            )
            value = baseline
            lower = upper = None
            model_version = None
            method = "observed-current" if horizon == 0 else "persistence-baseline"
            selected = available_models.get(horizon) if horizon > 0 else None
            has_synthetic_input = any(ref.kind is InputKind.SYNTHETIC for ref in snapshot.dataset_refs)
            if mode is not DataMode.DEMO and has_synthetic_input:
                selected = None
            if selected is not None and baseline is not None:
                model_version, artifact_model = selected
                value = max(0.0, baseline + predict_residual(artifact_model, vector))
                lower_offset = artifact_model.get("interval_80_lower_offset")
                upper_offset = artifact_model.get("interval_80_upper_offset")
                if lower_offset is not None and upper_offset is not None:
                    lower, upper = prediction_interval(value, lower_offset, upper_offset)
                method = "experimental-residual-ridge" if model_version.synthetic_only else "residual-ridge"
                used_models.add(model_version.model_id)
            elif horizon > 0 and (snapshot.h3_cell, horizon) in baseline_by_cell_horizon:
                method = "deterministic-dispersion-baseline"

            refs = snapshot.dataset_refs
            result_rows.append(
                PredictionResult(
                    run_id=run_id,
                    h3_cell=snapshot.h3_cell,
                    horizon_hours=horizon,
                    valid_at=snapshot.valid_at,
                    baseline_pm25=baseline,
                    predicted_pm25=value,
                    lower_pm25=lower,
                    upper_pm25=upper,
                    pdi=pdi_by_cell.get(snapshot.h3_cell) if horizon == 0 else None,
                    prediction_method=method,
                    model_version=model_version.model_id if model_version else None,
                    feature_schema_version=snapshot.feature_schema_version,
                    input_kind=_input_kind(mode, refs, horizon),
                    synthetic=(mode is DataMode.DEMO or any(ref.kind is InputKind.SYNTHETIC for ref in refs)),
                    quality=snapshot.quality,
                    dataset_refs=refs,
                    feature_vector=vector,
                )
            )

        run = PredictionRun(
            run_id=run_id,
            generated_at=generated_at,
            region=region,
            mode=mode,
            feature_run_id=feature_run_id,
            feature_schema_version=snapshots[0].feature_schema_version,
            model_versions=tuple(sorted(used_models)),
            scenario_id=scenario_id,
            dataset_refs=all_refs,
        )
        self._publication_repository.publish(run, result_rows)
        return run, result_rows

    def _select_model(
        self, *, region: str, horizon: float, feature_schema_version: str, mode: DataMode
    ) -> tuple[ModelVersion, dict] | None:
        if self._model_repository is None:
            return None
        candidates = self._model_repository.list(region=region, horizon_hours=horizon)
        candidates.sort(key=lambda item: (item.trained_at, item.model_id), reverse=True)
        for candidate in candidates:
            if candidate.feature_schema_version != feature_schema_version:
                continue
            if mode is DataMode.DEMO:
                if not candidate.synthetic_only or candidate.status not in {
                    ModelStatus.CANDIDATE,
                    ModelStatus.VALIDATED,
                    ModelStatus.PROMOTED,
                }:
                    continue
            elif candidate.synthetic_only or candidate.status is not ModelStatus.PROMOTED:
                continue
            artifact_model = _artifact_model(candidate, horizon)
            if artifact_model is not None:
                artifact_path = Path(candidate.artifact_uri)
                artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                if mode is DataMode.DEMO and artifact_payload.get("data_mode") != DataMode.DEMO.value:
                    continue
                if mode is not DataMode.DEMO and artifact_payload.get("data_mode") != DataMode.LIVE.value:
                    continue
                return candidate, artifact_model
        return None
