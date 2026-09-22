from datetime import UTC, datetime

import pytest

from app.services import model_training


def _live_dataset():
    return {
        "schema_version": "training-dataset-v1",
        "label_unit": "ug/m3",
        "data_mode": "live",
        "target_kind": "observed",
        "synthetic_only": False,
        "dataset_ids": ["fire-dataset-1"],
        "examples": [
            {
                "features": {"current_pm25": 30.0, "fire_count_upwind": 2.0},
                "target_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            }
        ],
    }


def test_incremental_feature_eval_requires_as_of_verification() -> None:
    with pytest.raises(ValueError, match="as-of"):
        model_training.evaluate_incremental_feature_group(
            _live_dataset(), group="fires", as_of_verified=False
        )


def test_incremental_feature_eval_refuses_synthetic_labels() -> None:
    dataset = _live_dataset()
    dataset.update(data_mode="demo", target_kind="synthetic", synthetic_only=True)

    with pytest.raises(ValueError, match="live, observed"):
        model_training.evaluate_incremental_feature_group(
            dataset, group="fires", as_of_verified=True
        )


def test_incremental_feature_eval_compares_same_temporal_test_split(monkeypatch) -> None:
    calls = []

    def fake_train(dataset, *, ridge_alpha):
        calls.append(dataset)
        mae = 10.0 if "fire_count_upwind" in dataset["examples"][0]["features"] else 12.0
        return {
            "artifact_sha256": "a" * 64 if len(calls) == 1 else "b" * 64,
            "evaluation": {
                "1.0": {
                    "temporal": {"test_count": 8, "metrics": {"mae": mae, "rmse": mae + 1}},
                    "promotion_eligible": False,
                }
            },
        }

    monkeypatch.setattr(model_training, "train_candidate", fake_train)
    dataset = _live_dataset()
    report = model_training.evaluate_incremental_feature_group(
        dataset, group="fires", as_of_verified=True
    )

    assert report["same_rows_and_temporal_splits"]
    assert report["auto_promoted"] is False
    assert report["horizons"]["1.0"]["same_test_rows"]
    assert report["horizons"]["1.0"]["delta_mae_ugm3"] == -2.0
    assert len(calls) == 2
