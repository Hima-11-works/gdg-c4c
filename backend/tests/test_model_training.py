from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.training import ModelStatus, ModelVersion, assert_live_promotion_allowed
from app.services.model_training import (
    evaluate_artifact,
    prediction_interval,
    train_candidate,
)
from app.services.training_data import generate_synthetic_training_dataset


def test_synthetic_training_requires_explicit_smoke_test_flag() -> None:
    dataset = generate_synthetic_training_dataset(hours=24, station_count=6)

    with pytest.raises(ValueError, match="explicit allow_synthetic"):
        train_candidate(dataset)


def test_training_refuses_mixed_live_and_demo_examples() -> None:
    dataset = generate_synthetic_training_dataset(hours=24, station_count=6)
    demo_example = dataset["examples"][0]
    live_example = {
        **dataset["examples"][1],
        "example_id": "live-observed-row",
        "data_mode": "live",
        "target_kind": "observed",
    }
    dataset["examples"] = [demo_example, live_example]
    dataset["example_count"] = 2

    with pytest.raises(ValueError, match="mixed live/demo"):
        train_candidate(dataset, allow_synthetic=True)


def test_candidate_reports_time_and_spatial_holdouts_deterministically() -> None:
    dataset = generate_synthetic_training_dataset(hours=24, station_count=6)

    artifact = train_candidate(dataset, allow_synthetic=True)
    repeated = train_candidate(dataset, allow_synthetic=True)
    horizon = artifact["evaluation"]["1.0"]

    assert artifact == repeated
    assert artifact["synthetic_only"] is True
    assert artifact["auto_promoted"] is False
    assert artifact["algorithm"] == "standardized-ridge-residual"
    assert horizon["temporal"]["train_count"] > 0
    assert horizon["temporal"]["validation_count"] > 0
    assert horizon["temporal"]["test_count"] > 0
    assert horizon["spatial"]["supported"] is True
    assert horizon["spatial"]["heldout_stations"]
    assert horizon["promotion_eligible"] is False
    assert horizon["temporal"]["metrics"]["by_india_season"]
    assert any("synthetic-only" in reason for reason in horizon["promotion_blockers"])
    training_end = datetime.fromisoformat(artifact["training_end"])
    dataset_end = max(
        datetime.fromisoformat(row["target_at"].replace("Z", "+00:00"))
        for row in dataset["examples"]
    )
    assert training_end < dataset_end


def test_spatial_holdout_is_not_claimed_without_verified_input_exclusion() -> None:
    dataset = generate_synthetic_training_dataset(hours=24, station_count=6)
    dataset["spatial_exclusion_verified"] = False
    for example in dataset["examples"]:
        example["spatial_exclusion_verified"] = False

    artifact = train_candidate(dataset, allow_synthetic=True)
    report = artifact["evaluation"]["1.0"]

    assert report["spatial"]["supported"] is False
    assert "exclude held-out station data" in report["spatial"]["reason"]
    assert any("station-held-out" in blocker for blocker in report["promotion_blockers"])


def test_quantile_interval_bounds_are_ordered_and_nonnegative() -> None:
    assert prediction_interval(1.0, -5.0, 2.0) == (0.0, 3.0)
    assert prediction_interval(10.0, -1.0, -0.5) == (9.0, 9.5)
    with pytest.raises(ValueError, match="lower residual quantile"):
        prediction_interval(10.0, 2.0, 1.0)


def test_evaluation_verifies_artifact_hash_and_reports_intervals() -> None:
    dataset = generate_synthetic_training_dataset(hours=24, station_count=6)
    artifact = train_candidate(dataset, allow_synthetic=True)
    report = evaluate_artifact(artifact, dataset)

    assert report["synthetic_only"] is True
    metrics = report["horizons"]["1.0"]["metrics"]
    assert metrics["interval_80_coverage"] is not None
    assert metrics["interval_80_mean_width"] >= 0
    assert "alert_precision" in metrics
    assert "station_balanced_mae" in metrics

    altered = {**artifact, "models": {"1.0": dict(artifact["models"]["1.0"])}}
    altered["models"]["1.0"]["intercept"] += 1
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_artifact(altered, dataset)


def test_synthetic_or_unvalidated_model_cannot_be_promoted() -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    synthetic = ModelVersion(
        model_id="model-demo",
        artifact_uri="models/demo.json",
        artifact_sha256="a" * 64,
        feature_schema_version="environmental-v1",
        feature_names=("rain_1h_mm",),
        trained_at=now,
        training_start=now,
        training_end=now,
        region="delhi-ncr",
        horizon_hours=1.0,
        synthetic_only=True,
        status=ModelStatus.VALIDATED,
    )
    with pytest.raises(ValueError, match="synthetic-only"):
        assert_live_promotion_allowed(synthetic)

    live_candidate = replace(
        synthetic,
        model_id="model-live",
        synthetic_only=False,
        status=ModelStatus.CANDIDATE,
    )
    with pytest.raises(ValueError, match="only validated"):
        assert_live_promotion_allowed(live_candidate)
