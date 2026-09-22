"""Explicit model lifecycle and prediction-monitoring operations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.domain.training import ModelStatus, ModelVersion, assert_live_promotion_allowed
from app.services.training_data import parse_utc

_FEATURE_GROUPS = {
    "fires": {"fire_frp_upwind_mw", "fire_count_upwind", "fire_age_hours_min"},
    "traffic": {"traffic_congestion_ratio"},
}


def model_from_registry_row(row: Mapping[str, Any]) -> ModelVersion:
    """Parse a local JSON-registry entry into its validated domain contract."""

    try:
        return ModelVersion(
            model_id=str(row["model_id"]),
            artifact_uri=str(row["artifact_uri"]),
            artifact_sha256=str(row["artifact_sha256"]),
            feature_schema_version=str(row["feature_schema_version"]),
            feature_names=tuple(str(name) for name in row["feature_names"]),
            trained_at=parse_utc(row["trained_at"]),
            training_start=parse_utc(row["training_start"]),
            training_end=parse_utc(row["training_end"]),
            region=str(row["region"]),
            horizon_hours=float(row["horizon_hours"]),
            metrics=row["metrics"],
            synthetic_only=bool(row["synthetic_only"]),
            status=ModelStatus(row["status"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid model registry entry: {exc}") from exc


def verify_model_artifact(model: ModelVersion, artifact: Mapping[str, Any]) -> None:
    """Verify content address, provenance, schema, horizon, and evaluation gates."""

    body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    try:
        canonical = json.dumps(
            body, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"artifact is not valid canonical JSON: {exc}") from exc
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != model.artifact_sha256 or artifact.get("artifact_sha256") != digest:
        raise ValueError("model artifact SHA-256 does not match the registry")
    if artifact.get("data_mode") != "live" or artifact.get("synthetic_only") is not False:
        raise ValueError("only live, observed-data artifacts can enter the live registry")
    if model.synthetic_only:
        raise ValueError("synthetic-only artifacts cannot be promoted to live")
    for field, expected in (
        ("region", model.region),
        ("feature_schema_version", model.feature_schema_version),
    ):
        if artifact.get(field) != expected:
            raise ValueError(f"model artifact {field} does not match the registry")
    horizon = str(model.horizon_hours)
    artifact_model = artifact.get("models", {}).get(horizon)
    evaluation = artifact.get("evaluation", {}).get(horizon)
    if not isinstance(artifact_model, dict) or not isinstance(evaluation, dict):
        raise ValueError(f"artifact has no model and evaluation for horizon {horizon}")
    if tuple(artifact_model.get("feature_names", ())) != model.feature_names:
        raise ValueError("model artifact feature names do not match the registry")
    if model.metrics != evaluation.get("temporal", {}).get("metrics"):
        raise ValueError("model evaluation metrics do not match the registry")
    if evaluation.get("promotion_eligible") is not True:
        blockers = evaluation.get("promotion_blockers", [])
        detail = "; ".join(str(item) for item in blockers) or "evaluation is not eligible"
        raise ValueError(f"model evaluation blocks promotion: {detail}")
    if evaluation.get("promotion_blockers"):
        raise ValueError("promotion-eligible artifact unexpectedly contains blockers")
    if artifact.get("auto_promoted") is not False:
        raise ValueError("artifact must not be automatically promoted")


def validate_candidate(
    model: ModelVersion,
    artifact: Mapping[str, Any],
    incremental_reports: Mapping[str, Mapping[str, Any]] | None = None,
) -> ModelVersion:
    """Move an eligible candidate to validated after any required M5 review."""

    if model.status not in {ModelStatus.CANDIDATE, ModelStatus.VALIDATED}:
        raise ValueError("only candidate models can be validated")
    verify_model_artifact(model, artifact)
    if model.status is ModelStatus.VALIDATED:
        return model

    required_groups = {
        group
        for group, features in _FEATURE_GROUPS.items()
        if features.intersection(model.feature_names)
    }
    reports = incremental_reports or {}
    for group in sorted(required_groups):
        report = reports.get(group)
        if report is None:
            raise ValueError(
                f"candidate uses {group} features; provide its live incremental evaluation report"
            )
        if (
            report.get("report_schema_version") != "incremental-feature-evaluation-v1"
            or report.get("feature_group") != group
            or report.get("data_mode") != "live"
            or report.get("target_kind") != "observed"
            or report.get("as_of_verified") is not True
            or report.get("auto_promoted") is not False
            or report.get("with_group_artifact_sha256") != model.artifact_sha256
        ):
            raise ValueError(f"{group} incremental report does not validate this candidate")
        horizon_report = report.get("horizons", {}).get(str(model.horizon_hours))
        if (
            not isinstance(horizon_report, dict)
            or horizon_report.get("supported") is not True
            or horizon_report.get("same_test_rows") is not True
            or horizon_report.get("eligible_for_manual_review") is not True
        ):
            raise ValueError(
                f"{group} incremental evaluation is not eligible for manual review at "
                f"horizon {model.horizon_hours}"
            )
    return replace(model, status=ModelStatus.VALIDATED)


def assert_model_can_be_activated(model: ModelVersion, artifact: Mapping[str, Any]) -> None:
    """Re-check the pinned artifact immediately before activation/rollback."""

    verify_model_artifact(model, artifact)
    if model.status is ModelStatus.RETIRED:
        # Retired rows are only created when a previously active version is
        # replaced. Re-check immutable evaluation evidence before rollback.
        assert_live_promotion_allowed(replace(model, status=ModelStatus.VALIDATED))
        return
    assert_live_promotion_allowed(model)


def activate_registry_model(
    registry: dict[str, Any], model_id: str, *, rollback: bool = False
) -> dict[str, Any]:
    """Atomically switch a local registry's active artifact for its scope."""

    rows = registry.get("models")
    if registry.get("schema_version") != "model-registry-v1" or not isinstance(rows, list):
        raise ValueError("registry file must contain a model-registry-v1 models array")
    target_rows = [row for row in rows if row.get("model_id") == model_id]
    if len(target_rows) != 1:
        raise ValueError(f"model {model_id!r} must exist exactly once in the registry")
    target = model_from_registry_row(target_rows[0])
    expected = ModelStatus.RETIRED if rollback else ModelStatus.VALIDATED
    if target.status is not expected:
        if target.status is not ModelStatus.PROMOTED:
            action = "rolled back to" if rollback else "promoted"
            raise ValueError(f"{target.status.value} model cannot be {action}")
    artifact_path = Path(target.artifact_uri)
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert_model_can_be_activated(target, artifact)

    for row in rows:
        model = model_from_registry_row(row)
        if (
            model.region == target.region
            and model.horizon_hours == target.horizon_hours
            and model.status is ModelStatus.PROMOTED
            and model.model_id != target.model_id
        ):
            row["status"] = ModelStatus.RETIRED.value
    target_rows[0]["status"] = ModelStatus.PROMOTED.value
    return registry


def set_registry_model_status(
    registry: dict[str, Any], model_id: str, status: ModelStatus
) -> dict[str, Any]:
    rows = registry.get("models")
    if registry.get("schema_version") != "model-registry-v1" or not isinstance(rows, list):
        raise ValueError("registry file must contain a model-registry-v1 models array")
    matches = [row for row in rows if row.get("model_id") == model_id]
    if len(matches) != 1:
        raise ValueError(f"model {model_id!r} must exist exactly once in the registry")
    current = model_from_registry_row(matches[0])
    if current.status not in {ModelStatus.CANDIDATE, ModelStatus.VALIDATED}:
        raise ValueError(f"{current.status.value} model cannot be validated")
    matches[0]["status"] = status.value
    return registry


def write_registry_atomic(path: Path, registry: dict[str, Any]) -> None:
    """Replace a local registry atomically so interruptions cannot truncate it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(registry, sort_keys=True, indent=2, allow_nan=False) + "\n"
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            temp_path = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def _finite_nonnegative(value: Any, field: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return result


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def summarize_model_monitoring(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize labelled prediction errors, data coverage, and feature drift."""

    if payload.get("schema_version") != "model-monitor-v1":
        raise ValueError("monitor input must use schema_version='model-monitor-v1'")
    if not str(payload.get("model_id", "")).strip():
        raise ValueError("monitor input must identify a model_id")
    data_mode = payload.get("data_mode")
    target_kind = payload.get("target_kind")
    synthetic_only = payload.get("synthetic_only")
    if not (
        (data_mode == "live" and target_kind == "observed" and synthetic_only is False)
        or (data_mode == "demo" and target_kind == "synthetic" and synthetic_only is True)
    ):
        raise ValueError(
            "monitor input provenance must be live/observed/non-synthetic or "
            "demo/synthetic/synthetic-only"
        )
    if payload.get("synthetic") is True and data_mode != "demo":
        raise ValueError("synthetic rows cannot be reported as live monitoring")
    rows = payload.get("rows")
    expected_count = payload.get("expected_count")
    if (
        not isinstance(rows, list)
        or not rows
        or isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count < len(rows)
        or expected_count <= 0
    ):
        raise ValueError("monitor input needs non-empty rows and expected_count >= row count")
    reference = payload.get("reference_features")
    if not isinstance(reference, dict) or not reference:
        raise ValueError("monitor input needs reference_features with mean and stddev values")

    predictions = labels = 0
    errors: list[float] = []
    high_errors: list[float] = []
    interval_hits = interval_count = 0
    feature_values: dict[str, list[float]] = {name: [] for name in reference}
    feature_missing = 0
    feature_slots = expected_count * len(reference)
    feature_missing += (expected_count - len(rows)) * len(reference)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"monitor row {index} must be an object")
        if "synthetic" in row and row["synthetic"] is not synthetic_only:
            raise ValueError(f"monitor row {index} provenance differs from the report mode")
        predicted = _finite_nonnegative(row.get("predicted_pm25"), "predicted_pm25", nullable=True)
        observed = _finite_nonnegative(row.get("observed_pm25"), "observed_pm25", nullable=True)
        if predicted is not None:
            predictions += 1
        if predicted is not None and observed is not None:
            labels += 1
            error = predicted - observed
            errors.append(abs(error))
            if observed >= 91.0:
                high_errors.append(abs(error))
        lower = _finite_nonnegative(row.get("lower_pm25"), "lower_pm25", nullable=True)
        upper = _finite_nonnegative(row.get("upper_pm25"), "upper_pm25", nullable=True)
        if (lower is None) != (upper is None):
            raise ValueError("prediction intervals must provide both lower_pm25 and upper_pm25")
        if lower is not None and upper is not None:
            if lower > upper:
                raise ValueError("lower_pm25 must not exceed upper_pm25")
            if observed is not None:
                interval_count += 1
                interval_hits += int(lower <= observed <= upper)

        features = row.get("features", {})
        if not isinstance(features, dict):
            raise ValueError(f"monitor row {index} features must be an object")
        for name in reference:
            value = features.get(name)
            if value is None:
                feature_missing += 1
            else:
                feature_values[name].append(_finite_number(value, f"features[{name}]"))

    drift = {}
    drift_alerts = []
    for name, baseline in reference.items():
        if not isinstance(baseline, dict):
            raise ValueError(f"reference_features[{name}] must contain mean and stddev")
        mean = _finite_number(baseline.get("mean"), f"reference_features[{name}].mean")
        stddev = _finite_nonnegative(baseline.get("stddev"), f"reference_features[{name}].stddev")
        values = feature_values[name]
        current_mean = sum(values) / len(values) if values else None
        standardized_shift = (
            (current_mean - mean) / stddev
            if current_mean is not None and stddev > 0
            else None
        )
        drift[name] = {
            "reference_mean": mean,
            "reference_stddev": stddev,
            "current_mean": current_mean,
            "observed_count": len(values),
            "standardized_mean_shift": standardized_shift,
        }
        if standardized_shift is not None and abs(standardized_shift) >= 1.0:
            drift_alerts.append(name)
        elif (
            current_mean is not None
            and stddev == 0
            and not math.isclose(current_mean, mean, rel_tol=0, abs_tol=1e-12)
        ):
            drift[name]["zero_variance_changed"] = True
            drift_alerts.append(name)
        else:
            drift[name]["zero_variance_changed"] = False

    missing_rate = feature_missing / feature_slots if feature_slots else 0.0
    if missing_rate >= 0.2:
        drift_alerts.append("feature_missingness")
    mae = sum(errors) / len(errors) if errors else None
    rmse = math.sqrt(sum(error * error for error in errors) / len(errors)) if errors else None
    bias_rows = [
        _finite_nonnegative(row["predicted_pm25"], "predicted_pm25")
        - _finite_nonnegative(row["observed_pm25"], "observed_pm25")
        for row in rows
        if row.get("predicted_pm25") is not None and row.get("observed_pm25") is not None
    ]
    coverage = {
        "expected_count": expected_count,
        "received_rows": len(rows),
        "prediction_count": predictions,
        "labelled_prediction_count": labels,
        "row_fraction": len(rows) / expected_count,
        "prediction_fraction": predictions / expected_count,
        "label_fraction": labels / expected_count,
        "feature_missing_fraction": missing_rate,
    }
    metrics = {
        "mae_ugm3": mae,
        "rmse_ugm3": rmse,
        "bias_ugm3": sum(bias_rows) / len(bias_rows) if bias_rows else None,
        "high_pollution_mae_ugm3": (
            sum(high_errors) / len(high_errors) if high_errors else None
        ),
        "interval_coverage": interval_hits / interval_count if interval_count else None,
        "interval_labelled_count": interval_count,
    }
    return {
        "report_schema_version": "model-monitor-report-v1",
        "model_id": payload["model_id"],
        "region": payload.get("region"),
        "horizon_hours": payload.get("horizon_hours"),
        "window": payload.get("window"),
        "metrics": metrics,
        "coverage": coverage,
        "feature_drift": drift,
        "drift_alerts": sorted(set(drift_alerts)),
        "data_mode": data_mode,
        "synthetic_only": synthetic_only,
        "status": (
            "demo_only"
            if synthetic_only
            else ("investigate" if drift_alerts else "within_threshold")
        ),
        "auto_promoted": False,
        "note": (
            "Synthetic replay diagnostic only; do not use as operational evidence."
            if synthetic_only
            else "Drift is an investigation trigger, never automatic model replacement."
        ),
    }


__all__ = [
    "activate_registry_model",
    "assert_model_can_be_activated",
    "model_from_registry_row",
    "set_registry_model_status",
    "summarize_model_monitoring",
    "validate_candidate",
    "verify_model_artifact",
    "write_registry_atomic",
]
