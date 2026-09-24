"""Tests for the two-region federated-training demonstration.

Covers the reproducible flow end to end: deterministic partitioning, local
fitting, an exchange payload that provably contains no raw rows, weighted
federated aggregation verified against an independent computation, run
reproducibility, the synthetic-only evaluation marker, the status endpoint,
and the CLI sequence.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.federation import FederationRunStatus
from app.services.federation import (
    PARTICIPANT_IDS,
    aggregate_updates,
    default_run_id,
    evaluate_aggregate,
    local_fit,
    partition_examples,
    payload_sha256,
    reject_raw_rows,
    run_federation_demo,
)
from app.services.training_data import generate_synthetic_training_dataset
from tests.fakes import FakeFederationRepository

FORBIDDEN_KEYS = (
    "target_pm25",
    "baseline_pm25",
    "issued_at",
    "target_at",
    "measured_at",
    "latitude",
    "longitude",
    "h3_cell",
    "station_id",
    "examples",
)

RUN_ID = "federation-test-run"


def _dataset() -> dict:
    return generate_synthetic_training_dataset(hours=12, station_count=6)


def _fit_both():
    from app.services.federation import partition_examples

    partitioned = partition_examples(_dataset())
    payloads, holdouts = [], {}
    for participant in PARTICIPANT_IDS:
        payload, heldout = local_fit(
            participant_id=participant, examples=partitioned[participant]
        )
        payloads.append(payload)
        holdouts[participant] = heldout
    return payloads, holdouts


# --- partitioning ---------------------------------------------------------


def test_partition_is_deterministic_and_disjoint(tmp_path) -> None:
    first = run_federation_demo(out_dir=tmp_path, persist=False)["participants"]
    twice = run_federation_demo(out_dir=tmp_path, persist=False)["participants"]
    assert first == twice, "partitioning must be deterministic"

    partitioned = partition_examples(_dataset())
    station_sets = [
        {row.station_id for row in partitioned[participant]}
        for participant in PARTICIPANT_IDS
    ]
    assert station_sets[0].isdisjoint(station_sets[1]), "partitions must be disjoint"
    assert all(len(rows) > 0 for rows in partitioned.values())


# --- exchange payload -----------------------------------------------------


def test_exchange_payload_contains_no_raw_observations() -> None:
    payloads, _ = _fit_both()
    payload = payloads[0]

    text = json.dumps(payload)
    for forbidden in FORBIDDEN_KEYS:
        assert f'"{forbidden}"' not in text, f"payload leaked {forbidden}"
    assert payload["horizons"]["1.0"]["coefficients"]
    assert payload["train_count"] > 0
    assert payload["heldout_examples"] > 0


def test_reject_raw_rows_raises_on_injected_observation() -> None:
    payloads, _ = _fit_both()
    poisoned = dict(payloads[0])
    poisoned["measured_at"] = ["2026-01-01T00:00:00Z"]
    with pytest.raises(ValueError, match="unexpected top-level keys"):
        reject_raw_rows(poisoned)


# --- aggregation ----------------------------------------------------------


def test_aggregate_is_the_weighted_average_of_updates() -> None:
    payloads, _ = _fit_both()
    aggregate = aggregate_updates(payloads)

    weights = [float(payload["train_count"]) for payload in payloads]
    expected_intercept = (
        payloads[0]["horizons"]["1.0"]["intercept"] * weights[0]
        + payloads[1]["horizons"]["1.0"]["intercept"] * weights[1]
    ) / (weights[0] + weights[1])
    assert aggregate["models"]["1.0"]["intercept"] == pytest.approx(expected_intercept)
    feature = aggregate["models"]["1.0"]["feature_names"][0]
    expected_coefficient = (
        payloads[0]["horizons"]["1.0"]["coefficients"][feature] * weights[0]
        + payloads[1]["horizons"]["1.0"]["coefficients"][feature] * weights[1]
    ) / (weights[0] + weights[1])
    assert aggregate["models"]["1.0"]["coefficients"][feature] == pytest.approx(
        expected_coefficient
    )
    assert aggregate["exchange"]["raw_observation_rows_sent"] == 0
    assert aggregate["synthetic_only"] is True


# --- evaluation ------------------------------------------------------------


def test_evaluation_reports_heldout_metrics_with_eligibility_marker(tmp_path) -> None:
    result = run_federation_demo(out_dir=tmp_path, persist=False)
    evaluation = result["evaluation"]
    assert evaluation["status"] == "synthetic_evaluation_only"
    assert evaluation["usable_as_real_world_evidence"] is False
    assert "not evidence of real-world accuracy" in evaluation["reason"]
    horizons = evaluation["horizons"]
    assert horizons and horizons[0]["heldout_count"] > 0


def test_evaluation_is_unavailable_without_holdout() -> None:
    aggregate = {
        "models": {
            "1.0": {
                "feature_names": ["a"],
                "means": {},
                "scales": {},
                "coefficients": {},
            }
        }
    }
    evaluation = evaluate_aggregate(aggregate, heldout_by_participant={})
    assert evaluation["status"] == "unavailable"
    assert evaluation["usable_as_real_world_evidence"] is False


# --- reproducibility ---------------------------------------------------------


def test_demo_is_reproducible(tmp_path_factory: pytest.TempPathFactory) -> None:
    first = run_federation_demo(
        out_dir=tmp_path_factory.mktemp("repro-a"), persist=False
    )
    second = run_federation_demo(
        out_dir=tmp_path_factory.mktemp("repro-b"), persist=False
    )
    assert first["aggregate"]["artifact_sha256"] == second["aggregate"]["artifact_sha256"]
    first_horizon = first["evaluation"]["horizons"][0]
    second_horizon = second["evaluation"]["horizons"][0]
    assert first_horizon["mae_ugm3"] == second_horizon["mae_ugm3"]
    assert first["participants"][0]["update_sha256"] == second["participants"][0]["update_sha256"]


def test_default_run_id_is_deterministic() -> None:
    first = default_run_id(
        anchor_utc=datetime(2025, 1, 1, tzinfo=UTC), hours=60, station_count=6
    )
    second = default_run_id(
        anchor_utc=datetime(2025, 1, 1, tzinfo=UTC), hours=60, station_count=6
    )
    assert first == second
    assert payload_sha256({"a": 1}) == payload_sha256({"a": 1})


# --- status endpoint ---------------------------------------------------------


def test_status_endpoint_reports_recorded_run(
    api_client: TestClient, fake_repos, tmp_path
) -> None:
    from app.services.federation import _domain_from_payload  # noqa: PLC2701

    result = run_federation_demo(out_dir=tmp_path, run_id=RUN_ID, persist=False)
    participants = [
        {
            "participant_id": participant["participant_id"],
            "region_label": participant["region_label"],
            "weight_fraction": participant["weight_fraction"],
            "example_count": participant["example_count"],
            "train_count": participant["train_count"],
            "validation_count": participant["validation_count"],
            "test_count": participant["test_count"],
            "station_count": participant["station_count"],
            "horizon_count": participant["horizon_count"],
            "update_path": participant["update_path"],
            "update_sha256": participant["update_sha256"],
        }
        for participant in result["participants"]
    ]
    run_domain, participant_rows = _domain_from_payload(result, participants)
    # The api_client fixture serves /api/v1/federation/status from this exact
    # repository, so the route must report the run saved into it.
    fake_repos.federation.save(run_domain, participant_rows)

    response = api_client.get("/api/v1/federation/status")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == FederationRunStatus.SUCCEEDED.value
    assert body["participant_count"] == 2
    assert body["raw_rows_exchanged_to_aggregator"] == 0
    assert body["evaluation"]["usable_as_real_world_evidence"] is False
    assert len(body["participants"]) == 2


def test_status_before_any_run(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/federation/status")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "no_federation_run"


# --- CLI sequence ---------------------------------------------------------


def test_cli_federation_demo_no_db(tmp_path) -> None:
    out_dir = tmp_path / "federation"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "federation-demo",
            "--no-db",
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout[completed.stdout.index("{") :])
    assert payload["status"] == "succeeded"
    assert payload["raw_rows_exchanged_to_aggregator"] == 0

    run_dir = out_dir / payload["run_id"]
    updates = sorted(path.name for path in (run_dir / "updates").glob("*.json"))
    assert updates == ["region-a.json", "region-b.json"]
    # The aggregate artifact exists and matches the reported hash.
    aggregate = json.loads((run_dir / "aggregate.json").read_text(encoding="utf-8"))
    assert aggregate["artifact_sha256"] == payload["aggregate"]["artifact_sha256"]
