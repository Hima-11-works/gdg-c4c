"""Reproducible residual regression and held-out evaluation.

The first M3 learner is a dependency-light ridge regressor so candidate
training remains runnable in the backend's offline toolchain. It is a
software/evaluation baseline, not a claim of scientific validity; M3 artifacts
are immutable candidates and require real observed labels before promotion.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from app.domain.features import DataMode, InputKind
from app.domain.training import TrainingExample
from app.services.training_data import example_from_dict

PROMOTION_THRESHOLDS = {1.0, 3.0, 6.0}
ALERT_THRESHOLD_UGM3 = 91.0
HIGH_POLLUTION_THRESHOLD_UGM3 = 91.0
MIN_INTERVAL_CALIBRATION_ROWS = 10
INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


def _india_season(timestamp: datetime) -> str:
    month = timestamp.astimezone(INDIA_TIMEZONE).month
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "pre_monsoon"
    if month in (6, 7, 8, 9):
        return "monsoon"
    return "post_monsoon"


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot compute a quantile of an empty sequence")
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[rank - 1]


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve a small dense system with deterministic partial pivoting."""

    size = len(vector)
    augmented = [matrix[row][:] + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            augmented[pivot][column] = 1e-12
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor:
                augmented[row] = [
                    value - factor * pivot_value
                    for value, pivot_value in zip(augmented[row], augmented[column], strict=True)
                ]
    return [augmented[row][-1] for row in range(size)]


def _fit_model(examples: Sequence[TrainingExample], ridge_alpha: float) -> dict[str, Any]:
    if not examples:
        raise ValueError("cannot train a residual model on an empty split")
    feature_names = sorted({name for example in examples for name in example.features})
    if not feature_names:
        raise ValueError("training rows contain no numeric features")
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    for name in feature_names:
        present = [
            float(value)
            for example in examples
            if (value := example.features.get(name)) is not None
        ]
        means[name] = sum(present) / len(present) if present else 0.0
        variance = (
            sum((value - means[name]) ** 2 for value in present) / len(present)
            if present
            else 0.0
        )
        scales[name] = math.sqrt(variance) if variance > 1e-12 else 1.0

    dimension = len(feature_names) + 1
    matrix = [[0.0] * dimension for _ in range(dimension)]
    vector = [0.0] * dimension
    for example in examples:
        values = [1.0]
        for name in feature_names:
            value = example.features.get(name)
            value = means[name] if value is None else float(value)
            values.append((value - means[name]) / scales[name])
        target = example.target_pm25 - example.baseline_pm25
        for row in range(dimension):
            vector[row] += values[row] * target
            for column in range(dimension):
                matrix[row][column] += values[row] * values[column]
    for index in range(1, dimension):
        matrix[index][index] += ridge_alpha
    coefficients = _solve(matrix, vector)
    return {
        "feature_names": feature_names,
        "means": means,
        "scales": scales,
        "intercept": coefficients[0],
        "coefficients": dict(zip(feature_names, coefficients[1:], strict=True)),
        "ridge_alpha": ridge_alpha,
    }


def predict_residual(model: dict[str, Any], features: dict[str, float | None]) -> float:
    residual = float(model["intercept"])
    for name in model["feature_names"]:
        value = features.get(name)
        value = float(model["means"][name]) if value is None else float(value)
        standardized = (value - float(model["means"][name])) / float(model["scales"][name])
        residual += standardized * float(model["coefficients"][name])
    return residual


def _temporal_split(
    examples: Sequence[TrainingExample],
) -> tuple[list[TrainingExample], list[TrainingExample], list[TrainingExample]]:
    times = sorted({example.target_at for example in examples})
    if len(times) < 5:
        raise ValueError("temporal evaluation needs at least five distinct target timestamps")
    train_end = max(1, math.floor(len(times) * 0.6))
    validation_end = max(train_end + 1, math.floor(len(times) * 0.8))
    validation_end = min(validation_end, len(times) - 1)
    train_times = set(times[:train_end])
    validation_times = set(times[train_end:validation_end])
    test_times = set(times[validation_end:])
    train = [example for example in examples if example.target_at in train_times]
    validation = [example for example in examples if example.target_at in validation_times]
    test = [example for example in examples if example.target_at in test_times]
    if not train or not validation or not test:
        raise ValueError("temporal train/validation/test split is empty")
    # Keep labels strictly before validation/test boundaries; this purges
    # any training target whose valid time crosses either holdout window.
    validation_start = min(validation_times)
    train = [example for example in train if example.target_at < validation_start]
    if not train:
        raise ValueError("purged temporal training split is empty")
    return train, validation, test


def _spatial_station_split(
    examples: Sequence[TrainingExample],
) -> tuple[list[TrainingExample], list[TrainingExample], list[str]]:
    stations = sorted({example.station_id for example in examples})
    if len(stations) < 3:
        return [], [], []
    ranked = sorted(
        stations,
        key=lambda station: hashlib.sha256(station.encode("utf-8")).hexdigest(),
    )
    holdout_count = max(1, math.ceil(len(stations) * 0.2))
    holdout = set(ranked[:holdout_count])
    train = [example for example in examples if example.station_id not in holdout]
    test = [example for example in examples if example.station_id in holdout]
    if not train or not test:
        return [], [], []
    return train, test, sorted(holdout)


def _predict_value(model: dict[str, Any], example: TrainingExample) -> float:
    residual = predict_residual(model, dict(example.features))
    return max(0.0, example.baseline_pm25 + residual)


def prediction_interval(
    point_prediction: float, lower_offset: float, upper_offset: float
) -> tuple[float, float]:
    """Apply calibrated residual quantiles and enforce nonnegative ordered bounds."""

    if lower_offset > upper_offset:
        raise ValueError("lower residual quantile must not exceed upper residual quantile")
    lower = max(0.0, point_prediction + lower_offset)
    upper = max(lower, point_prediction + upper_offset)
    return lower, upper


def _metrics(
    examples: Sequence[TrainingExample],
    model: dict[str, Any] | None,
    *,
    interval_offsets: tuple[float, float] | None = None,
    include_seasons: bool = True,
) -> dict[str, Any]:
    if not examples:
        return {"n": 0, "supported": False, "reason": "no examples"}
    model_errors: list[float] = []
    baseline_errors: list[float] = []
    persistence_errors: list[float] = []
    model_station_errors: dict[str, list[float]] = defaultdict(list)
    high_model_errors: list[float] = []
    high_baseline_errors: list[float] = []
    alert_true_positives = 0
    baseline_alert_true_positives = 0
    persistence_alert_true_positives = 0
    predicted_alerts = 0
    baseline_predicted_alerts = 0
    persistence_predicted_alerts = 0
    positive_targets = 0
    persistence_positive_targets = 0
    covered = 0
    interval_widths: list[float] = []
    for example in examples:
        target = example.target_pm25
        prediction = _predict_value(model, example) if model is not None else example.baseline_pm25
        error = prediction - target
        baseline_error = example.baseline_pm25 - target
        persistence = example.features.get("current_pm25")
        model_errors.append(abs(error))
        baseline_errors.append(abs(baseline_error))
        model_station_errors[example.station_id].append(abs(error))
        if persistence is not None:
            persistence_errors.append(abs(float(persistence) - target))
        if target >= ALERT_THRESHOLD_UGM3:
            positive_targets += 1
            if example.baseline_pm25 >= ALERT_THRESHOLD_UGM3:
                baseline_alert_true_positives += 1
            if persistence is not None:
                persistence_positive_targets += 1
                if float(persistence) >= ALERT_THRESHOLD_UGM3:
                    persistence_alert_true_positives += 1
        if prediction >= ALERT_THRESHOLD_UGM3:
            predicted_alerts += 1
            if target >= ALERT_THRESHOLD_UGM3:
                alert_true_positives += 1
        if example.baseline_pm25 >= ALERT_THRESHOLD_UGM3:
            baseline_predicted_alerts += 1
        if persistence is not None and float(persistence) >= ALERT_THRESHOLD_UGM3:
            persistence_predicted_alerts += 1
        if target >= HIGH_POLLUTION_THRESHOLD_UGM3:
            high_model_errors.append(abs(error))
            high_baseline_errors.append(abs(baseline_error))
        if interval_offsets is not None:
            lower, upper = prediction_interval(
                prediction, interval_offsets[0], interval_offsets[1]
            )
            covered += int(lower <= target <= upper)
            interval_widths.append(upper - lower)
    predictions = [
        _predict_value(model, example) if model is not None else example.baseline_pm25
        for example in examples
    ]
    targets = [example.target_pm25 for example in examples]
    residuals = [
        prediction - target
        for prediction, target in zip(predictions, targets, strict=True)
    ]
    result: dict[str, Any] = {
        "n": len(examples),
        "station_count": len(model_station_errors),
        "mae": sum(model_errors) / len(model_errors),
        "rmse": math.sqrt(sum(error * error for error in residuals) / len(residuals)),
        "bias": sum(residuals) / len(residuals),
        "station_balanced_mae": sum(
            sum(errors) / len(errors) for errors in model_station_errors.values()
        )
        / len(model_station_errors),
        "baseline_mae": sum(baseline_errors) / len(baseline_errors),
        "persistence_mae": (
            sum(persistence_errors) / len(persistence_errors) if persistence_errors else None
        ),
        "high_pollution_count": len(high_model_errors),
        "high_pollution_mae": (
            sum(high_model_errors) / len(high_model_errors) if high_model_errors else None
        ),
        "baseline_high_pollution_mae": (
            sum(high_baseline_errors) / len(high_baseline_errors) if high_baseline_errors else None
        ),
        "alert_precision": (
            alert_true_positives / predicted_alerts if predicted_alerts else None
        ),
        "alert_recall": alert_true_positives / positive_targets if positive_targets else None,
        "baseline_alert_precision": (
            baseline_alert_true_positives / baseline_predicted_alerts
            if baseline_predicted_alerts
            else None
        ),
        "baseline_alert_recall": (
            baseline_alert_true_positives / positive_targets if positive_targets else None
        ),
        "persistence_alert_precision": (
            persistence_alert_true_positives / persistence_predicted_alerts
            if persistence_predicted_alerts
            else None
        ),
        "persistence_alert_recall": (
            persistence_alert_true_positives / persistence_positive_targets
            if persistence_positive_targets
            else None
        ),
        "interval_80_coverage": covered / len(examples) if interval_offsets is not None else None,
        "interval_80_mean_width": (
            sum(interval_widths) / len(interval_widths) if interval_widths else None
        ),
    }
    if include_seasons:
        by_season: dict[str, Any] = {}
        for season in sorted({_india_season(row.target_at) for row in examples}):
            season_examples = [
                row for row in examples if _india_season(row.target_at) == season
            ]
            by_season[season] = _metrics(
                season_examples,
                model,
                interval_offsets=interval_offsets,
                include_seasons=False,
            )
        result["by_india_season"] = by_season
    return result


def _calibration_offsets(
    model: dict[str, Any], validation: Sequence[TrainingExample]
) -> tuple[tuple[float, float] | None, str | None]:
    if len(validation) < MIN_INTERVAL_CALIBRATION_ROWS:
        return None, f"need at least {MIN_INTERVAL_CALIBRATION_ROWS} validation labels"
    residuals = [
        example.target_pm25 - _predict_value(model, example) for example in validation
    ]
    lower = _quantile(residuals, 0.1)
    upper = _quantile(residuals, 0.9)
    return (min(lower, upper), max(lower, upper)), None


def _promotion_blockers(
    *,
    metrics: dict[str, Any],
    synthetic_only: bool,
    horizon: float,
    test_season_count: int,
    spatial_supported: bool,
) -> list[str]:
    blockers = []
    if synthetic_only:
        blockers.append("synthetic-only labels cannot be promoted")
    if horizon not in PROMOTION_THRESHOLDS:
        blockers.append("not a primary 1h/3h/6h checkpoint")
    if test_season_count < 2:
        blockers.append("held-out test labels cover fewer than two India seasons")
    if not spatial_supported:
        blockers.append("station-held-out evaluation is unsupported or has input leakage")
    reference_values = [
        value
        for value in (metrics.get("baseline_mae"), metrics.get("persistence_mae"))
        if value is not None
    ]
    reference = max(reference_values) if reference_values else None
    if reference is None or reference <= 0 or metrics["mae"] > 0.95 * reference:
        blockers.append("does not beat the stronger persistence/dispersion baseline by 5%")
    if metrics["high_pollution_count"] < 10:
        blockers.append("fewer than 10 high-pollution held-out labels")
    elif (
        metrics["baseline_high_pollution_mae"] is not None
        and metrics["high_pollution_mae"]
        > 1.05 * metrics["baseline_high_pollution_mae"]
    ):
        blockers.append("high-pollution MAE regresses by more than 5%")
    recall_references = [
        value
        for value in (
            metrics.get("baseline_alert_recall"),
            metrics.get("persistence_alert_recall"),
        )
        if value is not None
    ]
    if metrics["alert_recall"] is None:
        blockers.append("alert recall is unsupported")
    elif recall_references and metrics["alert_recall"] < max(recall_references) - 0.05:
        blockers.append("alert recall regresses by more than 5 percentage points")
    coverage = metrics.get("interval_80_coverage")
    if coverage is None or not 0.75 <= coverage <= 0.85:
        blockers.append("held-out 80% interval coverage is outside 75-85% or unsupported")
    return blockers


def train_candidate(
    dataset: dict[str, Any], *, ridge_alpha: float = 1.0, allow_synthetic: bool = False
) -> dict[str, Any]:
    if ridge_alpha < 0 or not math.isfinite(ridge_alpha):
        raise ValueError("ridge_alpha must be finite and >= 0")
    examples = [example_from_dict(row) for row in dataset.get("examples", [])]
    if not examples:
        raise ValueError("dataset contains no training examples")
    if dataset.get("schema_version", "training-dataset-v1") != "training-dataset-v1":
        raise ValueError("unsupported training dataset schema version")
    if dataset.get("label_unit", "ug/m3") != "ug/m3":
        raise ValueError("training dataset labels must use ug/m3")
    if dataset.get("example_count", len(examples)) != len(examples):
        raise ValueError("dataset manifest example_count does not match its rows")
    modes = {example.data_mode for example in examples}
    if len(modes) != 1 or DataMode.MIXED in modes:
        raise ValueError("training refuses mixed live/demo examples")
    mode = next(iter(modes))
    synthetic_only = mode is DataMode.DEMO and all(
        example.target_kind is InputKind.SYNTHETIC for example in examples
    )
    if synthetic_only and not allow_synthetic:
        raise ValueError(
            "synthetic-only training requires explicit allow_synthetic for smoke tests"
        )
    if mode is DataMode.LIVE and any(
        example.target_kind is not InputKind.OBSERVED for example in examples
    ):
        raise ValueError("live training accepts observed labels only")
    schemas = {example.feature_schema_version for example in examples}
    regions = {example.region for example in examples}
    if len(schemas) != 1 or len(regions) != 1:
        raise ValueError("training data must use one feature schema and region")
    if dataset.get("data_mode", mode.value) != mode.value:
        raise ValueError("dataset manifest data_mode does not match its examples")
    if dataset.get("feature_schema_version", next(iter(schemas))) != next(iter(schemas)):
        raise ValueError("dataset manifest feature schema does not match its examples")
    if dataset.get("region", next(iter(regions))) != next(iter(regions)):
        raise ValueError("dataset manifest region does not match its examples")
    if dataset.get("synthetic_only", synthetic_only) is not synthetic_only:
        raise ValueError("dataset manifest synthetic_only does not match its examples")
    spatial_verified = all(example.spatial_exclusion_verified for example in examples)
    if dataset.get("spatial_exclusion_verified", spatial_verified) is not spatial_verified:
        raise ValueError("dataset manifest spatial exclusion flag does not match its examples")
    expected_kind = "synthetic" if synthetic_only else "observed"
    if dataset.get("target_kind", expected_kind) != expected_kind:
        raise ValueError("dataset manifest target_kind does not match its examples")
    example_ids = [example.example_id for example in examples]
    if len(set(example_ids)) != len(example_ids):
        raise ValueError("training example_id values must be unique")
    models: dict[str, dict[str, Any]] = {}
    reports: dict[str, Any] = {}
    training_ranges: dict[str, dict[str, str]] = {}
    for horizon in sorted({example.horizon_hours for example in examples}):
        rows = [example for example in examples if example.horizon_hours == horizon]
        train, validation, test = _temporal_split(rows)
        temporal_model = _fit_model(train, ridge_alpha)
        interval_offsets, interval_reason = _calibration_offsets(temporal_model, validation)
        temporal_metrics = _metrics(test, temporal_model, interval_offsets=interval_offsets)
        temporal_metrics["validation_interval_calibration"] = {
            "nominal_coverage": 0.8,
            "row_count": len(validation),
            "lower_residual_quantile_ugm3": (
                interval_offsets[0] if interval_offsets is not None else None
            ),
            "upper_residual_quantile_ugm3": (
                interval_offsets[1] if interval_offsets is not None else None
            ),
            "reason": interval_reason,
        }
        spatial_train, spatial_test, heldout_stations = _spatial_station_split(rows)
        spatial_report: dict[str, Any]
        if not spatial_verified:
            spatial_report = {
                "supported": False,
                "reason": (
                    "prepared features and baseline must exclude held-out station data"
                ),
                "heldout_stations": heldout_stations,
            }
        elif len(spatial_train) < 2 or len(spatial_test) < 1:
            spatial_report = {
                "supported": False,
                "reason": "need at least three distinct stations for a station holdout",
                "station_count": len({row.station_id for row in rows}),
            }
        else:
            try:
                spatial_fit, spatial_validation, spatial_chronological_test = _temporal_split(
                    spatial_train
                )
                spatial_model = _fit_model(spatial_fit, ridge_alpha)
                spatial_offsets, spatial_reason = _calibration_offsets(
                    spatial_model, spatial_validation
                )
                evaluation_times = {
                    row.target_at for row in spatial_chronological_test
                }
                spatial_evaluation = [
                    row for row in spatial_test if row.target_at in evaluation_times
                ]
                if not spatial_evaluation:
                    raise ValueError("spatial test has no labels in the chronological test window")
                spatial_metrics = _metrics(
                    spatial_evaluation,
                    spatial_model,
                    interval_offsets=spatial_offsets,
                )
                spatial_report = {
                    "supported": True,
                    "heldout_stations": heldout_stations,
                    "train_count": len(spatial_fit),
                    "validation_count": len(spatial_validation),
                    "test_count": len(spatial_evaluation),
                    "metrics": spatial_metrics,
                    "interval_calibration_reason": spatial_reason,
                }
            except ValueError as exc:
                spatial_report = {
                    "supported": False,
                    "reason": str(exc),
                    "heldout_stations": heldout_stations,
                }
        blockers = _promotion_blockers(
            metrics=temporal_metrics,
            synthetic_only=synthetic_only,
            horizon=horizon,
            test_season_count=len({_india_season(row.target_at) for row in test}),
            spatial_supported=spatial_report["supported"],
        )
        temporal_model.update(
            interval_80_lower_offset=interval_offsets[0] if interval_offsets else None,
            interval_80_upper_offset=interval_offsets[1] if interval_offsets else None,
            interval_80_reason=interval_reason,
        )
        models[str(horizon)] = temporal_model
        training_ranges[str(horizon)] = {
            "start": min(row.issued_at for row in train).isoformat(),
            "end": max(row.target_at for row in train).isoformat(),
        }
        reports[str(horizon)] = {
            "temporal": {
                "train_count": len(train),
                "validation_count": len(validation),
                "test_count": len(test),
                "test_target_start": min(row.target_at for row in test).isoformat(),
                "test_target_end": max(row.target_at for row in test).isoformat(),
                "test_india_seasons": sorted(
                    {_india_season(row.target_at) for row in test}
                ),
                "metrics": temporal_metrics,
            },
            "spatial": spatial_report,
            "promotion_eligible": not blockers,
            "promotion_blockers": blockers,
        }
    body = {
        "artifact_schema_version": "residual-ridge-v1",
        "algorithm": "standardized-ridge-residual",
        "feature_schema_version": next(iter(schemas)),
        "region": next(iter(regions)),
        "data_mode": mode.value,
        "synthetic_only": synthetic_only,
        "training_start": min(
            datetime.fromisoformat(interval["start"]) for interval in training_ranges.values()
        ).isoformat(),
        "training_end": max(
            datetime.fromisoformat(interval["end"]) for interval in training_ranges.values()
        ).isoformat(),
        "training_ranges_by_horizon": training_ranges,
        "example_count": len(examples),
        "station_count": len({example.station_id for example in examples}),
        "dataset_ids": sorted({item for example in examples for item in example.dataset_ids}),
        "models": models,
        "evaluation": reports,
        "auto_promoted": False,
    }
    artifact_sha256 = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    return {**body, "artifact_sha256": artifact_sha256}


def evaluate_artifact(artifact: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    expected_hash = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    if artifact.get("artifact_sha256") != expected_hash:
        raise ValueError("artifact SHA-256 does not match its model payload")
    if artifact.get("feature_schema_version") != dataset.get("feature_schema_version"):
        raise ValueError("artifact and dataset feature schema versions do not match")
    if artifact.get("region") != dataset.get("region"):
        raise ValueError("artifact and dataset regions do not match")
    examples = [example_from_dict(row) for row in dataset.get("examples", [])]
    if not examples:
        raise ValueError("evaluation dataset contains no examples")
    if any(example.data_mode.value != artifact.get("data_mode") for example in examples):
        raise ValueError("evaluation data mode differs from the artifact training mode")
    result = {}
    for horizon in sorted({example.horizon_hours for example in examples}):
        model = artifact.get("models", {}).get(str(horizon))
        if model is None:
            result[str(horizon)] = {"supported": False, "reason": "artifact has no horizon model"}
            continue
        lower = model.get("interval_80_lower_offset")
        upper = model.get("interval_80_upper_offset")
        offsets = (float(lower), float(upper)) if lower is not None and upper is not None else None
        metrics = _metrics(
            [row for row in examples if row.horizon_hours == horizon],
            model,
            interval_offsets=offsets,
        )
        result[str(horizon)] = {"supported": True, "metrics": metrics}
    return {
        "report_schema_version": "model-evaluation-v1",
        "artifact_sha256": artifact["artifact_sha256"],
        "synthetic_only": bool(artifact["synthetic_only"]),
        "region": artifact["region"],
        "feature_schema_version": artifact["feature_schema_version"],
        "horizons": result,
    }


INCREMENTAL_FEATURE_GROUPS = {
    "fires": ("fire_frp_upwind_mw", "fire_count_upwind", "fire_age_hours_min"),
    "traffic": ("traffic_congestion_ratio",),
}


def evaluate_incremental_feature_group(
    dataset: dict[str, Any],
    *,
    group: str,
    as_of_verified: bool,
    ridge_alpha: float = 1.0,
) -> dict[str, Any]:
    """Compare a feature group against an otherwise identical live-label run.

    This is an offline diagnostic only: it refuses synthetic labels, requires
    explicit as-of verification, uses the same rows and deterministic held-out
    windows, and never changes registry or promotion state.
    """
    if group not in INCREMENTAL_FEATURE_GROUPS:
        raise ValueError(f"unsupported incremental feature group: {group}")
    if not as_of_verified:
        raise ValueError("incremental evaluation requires explicit as-of feature verification")
    if (
        dataset.get("data_mode") != "live"
        or dataset.get("target_kind") != "observed"
        or dataset.get("synthetic_only") is not False
    ):
        raise ValueError("incremental evaluation requires live, observed PM2.5 labels")
    if dataset.get("label_unit") != "ug/m3":
        raise ValueError("incremental evaluation requires PM2.5 labels in ug/m3")
    rows = dataset.get("examples")
    if not isinstance(rows, list) or not rows:
        raise ValueError("incremental evaluation requires non-empty training examples")
    group_features = set(INCREMENTAL_FEATURE_GROUPS[group])
    present = sorted(
        name
        for name in group_features
        if any(
            isinstance(row, dict)
            and isinstance(row.get("features"), dict)
            and row["features"].get(name) is not None
            for row in rows
        )
    )
    if not present:
        raise ValueError(f"dataset has no observed values for the {group} feature group")

    without_group = deepcopy(dataset)
    for row in without_group["examples"]:
        if isinstance(row.get("features"), dict):
            row["features"] = {
                key: value
                for key, value in row["features"].items()
                if key not in group_features
            }
    with_group_artifact = train_candidate(dataset, ridge_alpha=ridge_alpha)
    without_group_artifact = train_candidate(without_group, ridge_alpha=ridge_alpha)
    by_horizon: dict[str, Any] = {}
    for horizon in sorted(with_group_artifact["evaluation"], key=float):
        with_report = with_group_artifact["evaluation"][horizon]
        without_report = without_group_artifact["evaluation"].get(horizon)
        if without_report is None:
            by_horizon[horizon] = {"supported": False, "reason": "control artifact lacks horizon"}
            continue
        with_metrics = with_report["temporal"]["metrics"]
        without_metrics = without_report["temporal"]["metrics"]
        delta_mae = with_metrics["mae"] - without_metrics["mae"]
        delta_rmse = with_metrics["rmse"] - without_metrics["rmse"]
        improved = delta_mae < 0
        review_eligible = bool(with_report["promotion_eligible"] and improved)
        by_horizon[horizon] = {
            "supported": True,
            "test_rows_with_group": with_report["temporal"]["test_count"],
            "test_rows_without_group": without_report["temporal"]["test_count"],
            "same_test_rows": (
                with_report["temporal"]["test_count"]
                == without_report["temporal"]["test_count"]
            ),
            "with_group_metrics": with_metrics,
            "without_group_metrics": without_metrics,
            "delta_mae_ugm3": delta_mae,
            "delta_rmse_ugm3": delta_rmse,
            "mae_improved": improved,
            "candidate_promotion_eligible": with_report["promotion_eligible"],
            "eligible_for_manual_review": review_eligible,
        }
    return {
        "report_schema_version": "incremental-feature-evaluation-v1",
        "feature_group": group,
        "feature_names": present,
        "dataset_ids": sorted(dataset.get("dataset_ids", [])),
        "feature_group_dataset_ids": sorted(
            dataset.get("feature_group_dataset_ids", {}).get(group, [])
        ),
        "data_mode": "live",
        "target_kind": "observed",
        "as_of_verified": True,
        "same_rows_and_temporal_splits": True,
        "with_group_artifact_sha256": with_group_artifact["artifact_sha256"],
        "without_group_artifact_sha256": without_group_artifact["artifact_sha256"],
        "auto_promoted": False,
        "horizons": by_horizon,
    }


__all__ = [
    "evaluate_artifact",
    "evaluate_incremental_feature_group",
    "predict_residual",
    "prediction_interval",
    "train_candidate",
]
