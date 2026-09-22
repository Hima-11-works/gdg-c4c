import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.training import ModelStatus, ModelVersion
from app.services.model_operations import (
    activate_registry_model,
    summarize_model_monitoring,
    validate_candidate,
)


def _artifact(*, feature_names=("current_pm25",), region="india"):
    metrics = {"mae": 5.0, "rmse": 6.0}
    body = {
        "artifact_schema_version": "residual-ridge-v1",
        "algorithm": "standardized-ridge-residual",
        "feature_schema_version": "environmental-v2",
        "region": region,
        "data_mode": "live",
        "synthetic_only": False,
        "auto_promoted": False,
        "models": {"1.0": {"feature_names": list(feature_names)}},
        "evaluation": {
            "1.0": {
                "promotion_eligible": True,
                "promotion_blockers": [],
                "temporal": {"metrics": metrics},
            }
        },
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {**body, "artifact_sha256": digest}, metrics


def _model(artifact, metrics, *, model_id="candidate", status=ModelStatus.CANDIDATE):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return ModelVersion(
        model_id=model_id,
        artifact_uri=f"{model_id}.json",
        artifact_sha256=artifact["artifact_sha256"],
        feature_schema_version="environmental-v2",
        feature_names=tuple(artifact["models"]["1.0"]["feature_names"]),
        trained_at=now,
        training_start=now,
        training_end=now,
        region="india",
        horizon_hours=1.0,
        metrics=metrics,
        status=status,
    )


def _registry_row(model):
    return {
        "model_id": model.model_id,
        "artifact_uri": model.artifact_uri,
        "artifact_sha256": model.artifact_sha256,
        "feature_schema_version": model.feature_schema_version,
        "feature_names": list(model.feature_names),
        "trained_at": model.trained_at.isoformat(),
        "training_start": model.training_start.isoformat(),
        "training_end": model.training_end.isoformat(),
        "region": model.region,
        "horizon_hours": model.horizon_hours,
        "metrics": dict(model.metrics),
        "synthetic_only": model.synthetic_only,
        "status": model.status.value,
    }


def test_validation_checks_pinned_live_artifact_and_returns_validated_model():
    artifact, metrics = _artifact()
    candidate = _model(artifact, metrics)

    validated = validate_candidate(candidate, artifact)

    assert validated.status is ModelStatus.VALIDATED
    assert candidate.status is ModelStatus.CANDIDATE


def test_validation_rejects_tampering_and_synthetic_artifacts():
    artifact, metrics = _artifact()
    candidate = _model(artifact, metrics)
    altered = {**artifact, "models": {"1.0": {"feature_names": ["rain_1h_mm"]}}}
    with pytest.raises(ValueError, match="SHA-256"):
        validate_candidate(candidate, altered)

    synthetic = {**artifact, "data_mode": "demo", "synthetic_only": True}
    body = {key: value for key, value in synthetic.items() if key != "artifact_sha256"}
    synthetic["artifact_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    synthetic_model = replace(
        candidate,
        artifact_sha256=synthetic["artifact_sha256"],
        synthetic_only=True,
    )
    with pytest.raises(ValueError, match="only live"):
        validate_candidate(synthetic_model, synthetic)


def test_m5_features_require_matching_incremental_manual_review_report():
    artifact, metrics = _artifact(feature_names=("current_pm25", "fire_count_upwind"))
    candidate = _model(artifact, metrics)
    with pytest.raises(ValueError, match="provide its live incremental"):
        validate_candidate(candidate, artifact)

    report = {
        "report_schema_version": "incremental-feature-evaluation-v1",
        "feature_group": "fires",
        "data_mode": "live",
        "target_kind": "observed",
        "as_of_verified": True,
        "auto_promoted": False,
        "with_group_artifact_sha256": artifact["artifact_sha256"],
        "horizons": {
            "1.0": {
                "supported": True,
                "same_test_rows": True,
                "eligible_for_manual_review": True,
            }
        },
    }
    validated = validate_candidate(candidate, artifact, {"fires": report})
    assert validated.status is ModelStatus.VALIDATED


def test_local_registry_promotion_and_rollback_switch_one_active_version(tmp_path):
    first_artifact, metrics = _artifact()
    second_artifact, second_metrics = _artifact(region="india")
    # Content-addressed fixtures need unique payloads / identities.
    second_artifact["models"]["1.0"]["feature_names"] = ["current_pm25", "rain_1h_mm"]
    body = {key: value for key, value in second_artifact.items() if key != "artifact_sha256"}
    second_artifact["artifact_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    second_metrics = metrics
    first = _model(first_artifact, metrics, model_id="first", status=ModelStatus.PROMOTED)
    second = _model(
        second_artifact,
        second_metrics,
        model_id="second",
        status=ModelStatus.VALIDATED,
    )
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps(first_artifact), encoding="utf-8")
    second_path.write_text(json.dumps(second_artifact), encoding="utf-8")
    first = replace(first, artifact_uri=str(first_path))
    second = replace(second, artifact_uri=str(second_path))
    registry = {
        "schema_version": "model-registry-v1",
        "models": [_registry_row(first), _registry_row(second)],
    }

    activate_registry_model(registry, "second")
    assert [row["status"] for row in registry["models"]] == ["retired", "promoted"]
    activate_registry_model(registry, "first", rollback=True)
    assert [row["status"] for row in registry["models"]] == ["promoted", "retired"]


def test_monitor_reports_errors_coverage_drift_and_never_promotes():
    payload = {
        "schema_version": "model-monitor-v1",
        "model_id": "model-1",
        "region": "india",
        "horizon_hours": 1,
        "data_mode": "live",
        "target_kind": "observed",
        "synthetic_only": False,
        "expected_count": 3,
        "reference_features": {
            "wind_u_ms": {"mean": 0, "stddev": 1},
            "rain_1h_mm": {"mean": 1, "stddev": 1},
        },
        "rows": [
            {
                "predicted_pm25": 50,
                "observed_pm25": 40,
                "lower_pm25": 35,
                "upper_pm25": 55,
                "features": {"wind_u_ms": -2, "rain_1h_mm": 1},
            },
            {
                "predicted_pm25": None,
                "observed_pm25": 50,
                "features": {"wind_u_ms": None, "rain_1h_mm": 4},
            },
        ],
    }

    report = summarize_model_monitoring(payload)

    assert report["metrics"]["mae_ugm3"] == 10
    assert report["metrics"]["interval_coverage"] == 1
    assert report["coverage"]["prediction_fraction"] == pytest.approx(1 / 3)
    assert report["coverage"]["feature_missing_fraction"] == pytest.approx(0.5)
    assert report["status"] == "investigate"
    assert "rain_1h_mm" in report["drift_alerts"]
    assert report["auto_promoted"] is False


def test_explicit_synthetic_demo_monitor_is_diagnostic_only():
    report = summarize_model_monitoring(
        {
            "schema_version": "model-monitor-v1",
            "model_id": "demo-model",
            "data_mode": "demo",
            "target_kind": "synthetic",
            "synthetic_only": True,
            "expected_count": 1,
            "reference_features": {"rain_1h_mm": {"mean": 0, "stddev": 1}},
            "rows": [
                {
                    "predicted_pm25": 10,
                    "observed_pm25": 12,
                    "synthetic": True,
                    "features": {"rain_1h_mm": 0.5},
                }
            ],
        }
    )

    assert report["status"] == "demo_only"
    assert report["synthetic_only"] is True
    assert report["auto_promoted"] is False


def test_monitor_rejects_synthetic_rows_and_invalid_intervals():
    base = {
        "schema_version": "model-monitor-v1",
        "model_id": "model-1",
        "data_mode": "live",
        "target_kind": "observed",
        "synthetic_only": False,
        "expected_count": 1,
        "reference_features": {"rain_1h_mm": {"mean": 0, "stddev": 1}},
        "rows": [{"predicted_pm25": 1, "observed_pm25": 1, "features": {}}],
    }
    with pytest.raises(ValueError, match="synthetic"):
        summarize_model_monitoring({**base, "synthetic": True})
    with pytest.raises(ValueError, match="both lower"):
        summarize_model_monitoring(
            {**base, "rows": [{**base["rows"][0], "lower_pm25": 0}]}
        )
