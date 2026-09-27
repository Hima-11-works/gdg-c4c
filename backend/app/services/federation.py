"""Two-region federated-training demonstration.

Contract in docs/api/federation.md. The flow, in order:

1. **Partition** the deterministic synthetic training dataset into two
   disjoint regional partitions (stable station-hash round robin; whole
   stations stay together, so no observation row reaches two clients).
2. **Train locally** per participant: the standardized-ridge residual learner
   central training uses, fitted on the participant's own train/validation
   split only.
3. **Exchange update payloads only** — fitted parameters, calibration
   offsets, and row/station counts. Raw observation rows, coordinates, cell
   ids, station ids and timestamps are rejected before an update is accepted.
4. **Aggregate** with federated weighted averaging (weights = training-row
   counts) into one immutable artifact.
5. **Evaluate** the aggregate on each participant's held-out test rows and
   record the metrics with an explicit ineligibility marker: synthetic-only
   labels are not evidence of real-world accuracy.
6. **Record** participants, model versions, run status and provenance, then
   expose everything through `GET /api/v1/federation/status`.

Privacy/geography/accuracy limitations are recorded verbatim in every run;
this feature claims no differential privacy and no nationwide deployment.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.features import DataMode
from app.domain.repositories import FederationRepository
from app.domain.federation import (
    PARTICIPANT_IDS,
    REGION_SCOPE,
    FederationParticipant,
    FederationRun,
    FederationRunStatus,
)
from app.domain.training import ModelStatus, ModelVersion
from app.services.model_training import (
    _calibration_offsets,
    _fit_model,
    _india_season,
    _temporal_split,
    predict_residual,
)
from app.services.training_data import (
    example_from_dict,
    example_to_dict,
    export_training_dataset,
    generate_synthetic_training_dataset,
)

ALGORITHM = "weighted-fedavg-of-standardized-ridge-residual"
LOCAL_ALGORITHM = "standardized-ridge-residual"
ARTIFACT_SCHEMA_VERSION = "federated-ridge-v1"
UPDATE_SCHEMA_VERSION = "federated-update-v1"
CODE_VERSION = "v1"
AGGREGATE_REGION = "federation-demo"

# Exchange contract: allowed payload keys, top level and per horizon. Anything
# outside one of these sets is rejected before an update can be aggregated —
# the runtime enforcement of "no raw rows reach the aggregator".
_UPDATE_TOP_KEYS = frozenset(
    {
        "update_schema_version",
        "participant_id",
        "region_label",
        "feature_schema_version",
        "data_mode",
        "algorithm",
        "ridge_alpha",
        "horizons",
        "example_count",
        "train_count",
        "validation_count",
        "test_count",
        "station_count",
        "horizon_count",
        "heldout_examples",
        "update_sha256",
    }
)
_HORIZON_KEYS = frozenset(
    {
        "feature_names",
        "means",
        "scales",
        "intercept",
        "coefficients",
        "interval_80_lower_offset",
        "interval_80_upper_offset",
        "interval_80_reason",
        "train_count",
        "station_count",
    }
)

_LIMITATIONS = {
    "privacy": "not established: exchanging model parameters is not a privacy guarantee",
    "geography": (
        "two disjoint partitions of one synthetic regional demo dataset; "
        "not a nationwide deployment"
    ),
    "accuracy": "synthetic-only evaluation; not evidence of real-world accuracy",
}


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def region_label_for(participant_id: str) -> str:
    """`region-a` -> `federation-partition-a`."""

    if not participant_id.startswith("region-"):
        raise ValueError(f"participant id must look like 'region-x': {participant_id!r}")
    return f"federation-partition-{participant_id.removeprefix('region-')}"


def reject_raw_rows(payload: dict[str, Any]) -> None:
    """Refuse any exchange payload that carries measurement-row data.

    Parameters and counts only. A leaked timestamp, coordinate, cell id,
    station id or per-row label raises instead of being silently aggregated.
    """
    unknown = set(payload) - _UPDATE_TOP_KEYS
    if unknown:
        raise ValueError(f"exchange payload carries unexpected top-level keys: {sorted(unknown)}")
    for horizon in payload["horizons"].values():
        unexpected = set(horizon) - _HORIZON_KEYS
        if unexpected:
            raise ValueError(
                f"exchange payload horizon carries unexpected keys: {sorted(unexpected)}"
            )


# ---------------------------------------------------------------------------
# 1. Partitioning
# ---------------------------------------------------------------------------


def partition_examples(
    dataset: dict[str, Any], participant_ids: tuple[str, ...] = PARTICIPANT_IDS
) -> dict[str, list]:
    """Assign whole stations to disjoint participants deterministically.

    Round-robin over stations ranked by a stable sha256 of the station id: the
    same dataset splits identically every time, no two clients share a
    station, and each client keeps whole hourly series.
    """
    examples = [example_from_dict(row) for row in dataset.get("examples", [])]
    if not examples:
        raise ValueError("cannot partition an empty dataset")
    station_ids = sorted(
        {example.station_id for example in examples},
        key=lambda station: hashlib.sha256(station.encode("utf-8")).digest(),
    )
    if len(station_ids) < len(participant_ids):
        raise ValueError(
            f"need at least {len(participant_ids)} stations for "
            f"{len(participant_ids)} participants"
        )
    assignment = {
        station: participant_ids[index % len(participant_ids)]
        for index, station in enumerate(station_ids)
    }
    buckets: dict[str, list] = {participant: [] for participant in participant_ids}
    for example in examples:
        buckets[assignment[example.station_id]].append(example)
    empty = sorted(participant for participant, rows in buckets.items() if not rows)
    if empty:
        raise ValueError(f"partition left empty participants: {', '.join(empty)}")
    return buckets


# ---------------------------------------------------------------------------
# 2. Local training + 3. exchange payload
# ---------------------------------------------------------------------------


def local_fit(
    *,
    participant_id: str,
    examples: list,
    ridge_alpha: float = 1.0,
) -> tuple[dict[str, Any], list]:
    """Train locally on one partition; produce its exchange payload.

    Returns ``(payload, heldout_rows)``. The payload is parameters + counts
    only (checked against the exchange contract before returning). The
    held-out rows never leave the participant — they are what the aggregate
    will later be scored against.
    """
    label = region_label_for(participant_id)
    train, validation, test = _temporal_split(examples)
    horizons: dict[str, dict[str, Any]] = {}
    for horizon in sorted({example.horizon_hours for example in examples}):
        horizon_train = [row for row in train if row.horizon_hours == horizon]
        horizon_validation = [row for row in validation if row.horizon_hours == horizon]
        if not horizon_train:
            raise ValueError(f"empty local training split for horizon {horizon}")
        model = _fit_model(horizon_train, ridge_alpha)
        offsets, reason = _calibration_offsets(model, horizon_validation)
        horizons[str(horizon)] = {
            "feature_names": list(model["feature_names"]),
            "means": dict(model["means"]),
            "scales": dict(model["scales"]),
            "intercept": float(model["intercept"]),
            "coefficients": dict(model["coefficients"]),
            "interval_80_lower_offset": None if offsets is None else offsets[0],
            "interval_80_upper_offset": None if offsets is None else offsets[1],
            "interval_80_reason": reason,
            "train_count": len(horizon_train),
            "station_count": len({row.station_id for row in horizon_train}),
        }
    payload = {
        "update_schema_version": UPDATE_SCHEMA_VERSION,
        "participant_id": participant_id,
        "region_label": label,
        "feature_schema_version": examples[0].feature_schema_version,
        "data_mode": examples[0].data_mode.value,
        "algorithm": LOCAL_ALGORITHM,
        "ridge_alpha": ridge_alpha,
        "horizons": horizons,
        "example_count": len(examples),
        "train_count": len(train),
        "validation_count": len(validation),
        "test_count": len(test),
        "station_count": len({row.station_id for row in examples}),
        "horizon_count": len(horizons),
        "heldout_examples": len(test),
    }
    reject_raw_rows(payload)
    payload["update_sha256"] = payload_sha256(payload)
    return payload, test


# ---------------------------------------------------------------------------
# 4. Aggregation (weighted federated averaging)
# ---------------------------------------------------------------------------


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total = sum(weights)
    if total <= 0:
        raise ValueError("federated averaging needs a positive total weight")
    return sum(value * weight for value, weight in zip(values, weights, strict=True)) / total


def aggregate_updates(
    updates: list[dict[str, Any]],
    *,
    participant_ids: tuple[str, ...] = PARTICIPANT_IDS,
) -> dict[str, Any]:
    """Aggregate exchange payloads into one artifact.

    Inputs are the participants' exchange payloads and nothing else: the
    aggregator never touches observation rows in any code path.
    """
    for payload in updates:
        reject_raw_rows(payload)
    if len(updates) < 2:
        raise ValueError("federated averaging needs at least two participants")
    anchored = {payload["participant_id"]: payload for payload in updates}
    missing = sorted(
        participant for participant in participant_ids if participant not in anchored
    )
    if missing:
        raise ValueError(f"missing exchange payloads for: {', '.join(missing)}")
    unknown = sorted(set(anchored) - set(participant_ids))
    if unknown:
        raise ValueError(f"exchange payloads from unknown participants: {', '.join(unknown)}")
    ordered = [anchored[participant] for participant in participant_ids]

    weights = [float(payload["train_count"]) for payload in ordered]
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("at least one participant needs a positive training count")
    weight_fractions = [weight / total_weight for weight in weights]

    shared_horizons = set.intersection(*(set(payload["horizons"]) for payload in ordered))
    if not shared_horizons:
        raise ValueError("participants share no horizons to aggregate")
    ridge_alphas = {payload["ridge_alpha"] for payload in ordered}
    if len(ridge_alphas) != 1:
        raise ValueError("participant updates were trained with different ridge alphas")
    ridge_alpha = ridge_alphas.pop()
    reference_models = ordered[0]["horizons"]
    for payload in ordered[1:]:
        for horizon in sorted(set(payload["horizons"]) & set(reference_models)):
            if list(payload["horizons"][horizon]["feature_names"]) != list(
                reference_models[horizon]["feature_names"]
            ):
                raise ValueError(
                    "participant updates use different feature sets for "
                    f"horizon {horizon}"
                )

    horizons: dict[str, dict[str, Any]] = {}
    for horizon in shared_horizons:
        models = [payload["horizons"][str(horizon)] for payload in ordered]
        names = models[0]["feature_names"]
        lower_offsets = [model["interval_80_lower_offset"] for model in models]
        upper_offsets = [model["interval_80_upper_offset"] for model in models]
        lower = (
            None
            if any(offset is None for offset in lower_offsets)
            else _weighted_mean([float(offset) for offset in lower_offsets], weights)
        )
        upper = (
            None
            if any(offset is None for offset in upper_offsets)
            else _weighted_mean([float(offset) for offset in upper_offsets], weights)
        )
        horizons[str(horizon)] = {
            "feature_names": list(names),
            "means": {
                name: _weighted_mean(
                    [float(model["means"][name]) for model in models], weights
                )
                for name in names
            },
            "scales": {
                name: _weighted_mean(
                    [float(model["scales"][name]) for model in models], weights
                )
                for name in names
            },
            "intercept": _weighted_mean(
                [float(model["intercept"]) for model in models], weights
            ),
            "coefficients": {
                name: _weighted_mean(
                    [float(model["coefficients"][name]) for model in models], weights
                )
                for name in names
            },
            "ridge_alpha": ridge_alpha,
            "interval_80_lower_offset": lower,
            "interval_80_upper_offset": upper,
            "participant_count": len(ordered),
            "contributing_train_rows_total": int(total_weight),
        }

    participants_block = [
        {
            "participant_id": payload["participant_id"],
            "region_label": payload["region_label"],
            "train_count": payload["train_count"],
            "station_count": payload["station_count"],
            "weight_fraction": weight_fractions[index],
            "update_sha256": payload["update_sha256"],
        }
        for index, payload in enumerate(ordered)
    ]
    horizons_hours = sorted(float(horizon) for horizon in shared_horizons)
    body = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "algorithm": ALGORITHM,
        "feature_schema_version": ordered[0]["feature_schema_version"],
        "region": AGGREGATE_REGION,
        "data_mode": ordered[0]["data_mode"],
        "synthetic_only": True,
        "horizons_hours": horizons_hours,
        "participants": participants_block,
        "models": horizons,
        "exchange": {
            "raw_observation_rows_sent": 0,
            "raw_coordinates_sent": 0,
            "model_updates": len(ordered),
        },
        "limitations": dict(_LIMITATIONS),
    }
    body["artifact_sha256"] = payload_sha256(body)
    return body


# ---------------------------------------------------------------------------
# 5. Evaluation against held-out rows
# ---------------------------------------------------------------------------


def evaluate_aggregate(
    aggregate: dict[str, Any],
    *,
    heldout_by_participant: dict[str, list],
) -> dict[str, Any]:
    """Score the aggregate on each participant's held-out test rows.

    The aggregate describes parameters only; held-out labels stay exactly as
    the participants' own splits produced them. Every metric this produces is
    explicitly marked unusable as real-world evidence.
    """
    horizons_report: list[dict[str, Any]] = []
    heldout_total = 0
    for horizon_text, model in sorted(
        aggregate["models"].items(), key=lambda item: float(item[0])
    ):
        horizon = float(horizon_text)
        model_errors: list[float] = []
        signed_errors: list[float] = []
        baseline_errors: list[float] = []
        seasons: set[str] = set()
        for rows in heldout_by_participant.values():
            for example in rows:
                if example.horizon_hours != horizon:
                    continue
                prediction = example.baseline_pm25 + predict_residual(model, example.features)
                signed_errors.append(prediction - example.target_pm25)
                model_errors.append(abs(prediction - example.target_pm25))
                baseline_errors.append(abs(example.baseline_pm25 - example.target_pm25))
                seasons.add(_india_season(example.target_at))
        if model_errors:
            count = len(model_errors)
            horizons_report.append(
                {
                    "horizon_hours": horizon,
                    "mae_ugm3": sum(model_errors) / count,
                    "baseline_mae_ugm3": sum(baseline_errors) / count,
                    "rmse_ugm3": math.sqrt(sum(e**2 for e in signed_errors) / count),
                    "bias_ugm3": sum(signed_errors) / count,
                    "heldout_count": count,
                    "seasons_seen": sorted(seasons),
                }
            )
            heldout_total += count
    if not horizons_report:
        return {
            "status": "unavailable",
            "reason": "no held-out labels were available for evaluation",
            "usable_as_real_world_evidence": False,
        }
    return {
        "status": "synthetic_evaluation_only",
        "usable_as_real_world_evidence": False,
        "reason": (
            "synthetic-only demonstration labels; these metrics are not "
            "evidence of real-world accuracy"
        ),
        "heldout_examples": heldout_total,
        "horizons": horizons_report,
    }


# ---------------------------------------------------------------------------
# 6. Run orchestration, persistence, status
# ---------------------------------------------------------------------------


def default_run_id(
    *,
    anchor_utc: datetime,
    hours: int,
    station_count: int,
    code_version: str = CODE_VERSION,
) -> str:
    anchor = anchor_utc.strftime("%Y%m%dT%H%MZ")
    return f"federation-{anchor}-h{hours}-s{station_count}-{code_version}"


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def run_federation_demo(
    *,
    out_dir: Path,
    hours: int = 60,
    station_count: int = 6,
    anchor_utc: datetime = datetime(2025, 1, 1, tzinfo=UTC),
    run_id: str | None = None,
    ridge_alpha: float = 1.0,
    persist: bool = False,
) -> dict[str, Any]:
    """Run the complete two-region demonstration; return its status payload.

    Deterministic: the same inputs produce identical update payloads, the
    same aggregate artifact sha256 and the same evaluation metrics. Only
    parameter payloads and the aggregate artifact are written; observation
    rows are never serialized to the exchange directory.
    """
    if hours < 12:
        raise ValueError("federation demo needs at least 12 hourly examples")
    if station_count < 6:
        raise ValueError(
            "federation demo needs at least six stations (three per partition)"
        )

    resolved_run_id = run_id or default_run_id(
        anchor_utc=anchor_utc, hours=hours, station_count=station_count
    )
    started_at = datetime.now(UTC)
    dataset = generate_synthetic_training_dataset(
        hours=hours, station_count=station_count, anchor_utc=anchor_utc
    )
    partitioned = partition_examples(dataset)

    run_dir = Path(out_dir) / resolved_run_id
    updates_dir = run_dir / "updates"
    updates: list[dict[str, Any]] = []
    heldout_by_participant: dict[str, list] = {}
    participant_summaries: list[dict[str, Any]] = []
    for participant_id in PARTICIPANT_IDS:
        payload, heldout = local_fit(
            participant_id=participant_id,
            examples=partitioned[participant_id],
            ridge_alpha=ridge_alpha,
        )
        updates.append(payload)
        heldout_by_participant[participant_id] = heldout
        update_path = updates_dir / f"{participant_id}.json"
        _write_bytes(update_path, canonical_bytes(payload))
        participant_summaries.append(
            {
                "participant_id": participant_id,
                "region_label": payload["region_label"],
                "example_count": payload["example_count"],
                "train_count": payload["train_count"],
                "validation_count": payload["validation_count"],
                "test_count": payload["test_count"],
                "station_count": payload["station_count"],
                "horizon_count": payload["horizon_count"],
                "heldout_examples": payload["heldout_examples"],
                "update_path": str(update_path),
                "update_sha256": payload["update_sha256"],
                # Finalized below from the aggregate's participants block.
                "weight_fraction": 0.0,
            }
        )
    aggregate_payload = aggregate_updates(updates, participant_ids=PARTICIPANT_IDS)
    for summary in participant_summaries:
        summary["weight_fraction"] = next(
            block["weight_fraction"]
            for block in aggregate_payload["participants"]
            if block["participant_id"] == summary["participant_id"]
        )
    evaluation = evaluate_aggregate(
        aggregate_payload, heldout_by_participant=heldout_by_participant
    )
    aggregate_path = run_dir / "aggregate.json"
    _write_bytes(aggregate_path, canonical_bytes(aggregate_payload))

    model_versions = [
        {
            "model_id": f"{resolved_run_id}-h{horizon}",
            "region": AGGREGATE_REGION,
            "horizon_hours": horizon,
            "status": ModelStatus.CANDIDATE.value,
            "synthetic_only": True,
        }
        for horizon in aggregate_payload["horizons_hours"]
    ]

    status_payload: dict[str, Any] = {
        "run_id": resolved_run_id,
        "status": FederationRunStatus.SUCCEEDED.value,
        "participant_count": len(participant_summaries),
        "region_scope": REGION_SCOPE,
        "feature_schema_version": aggregate_payload["feature_schema_version"],
        "horizons_hours": aggregate_payload["horizons_hours"],
        "participants": participant_summaries,
        "aggregate": {
            "artifact_path": str(aggregate_path),
            "artifact_sha256": aggregate_payload["artifact_sha256"],
            "algorithm": aggregate_payload["algorithm"],
            "synthetic_only": True,
        },
        "model_versions": model_versions,
        "evaluation": evaluation,
        "raw_rows_exchanged_to_aggregator": aggregate_payload["exchange"][
            "raw_observation_rows_sent"
        ],
        "limitations": dict(_LIMITATIONS),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
    }
    if persist:
        status_payload = _persist_run(
            out_dir=run_dir,
            payload=status_payload,
            aggregate_payload=aggregate_payload,
            participant_summaries=participant_summaries,
        )
    write_status_file(run_dir / "status.json", status_payload)
    return status_payload


def _persist_run(
    *,
    out_dir: Path,
    payload: dict[str, Any],
    aggregate_payload: dict[str, Any],
    participant_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record the run, its participants and one model_version per horizon."""

    from app.db.session import get_session_factory
    from app.db.repositories import SqlFederationRepository, SqlModelVersionRepository

    session = get_session_factory()()
    try:
        registry = SqlModelVersionRepository(session)
        model_ids: list[str] = []
        for horizon in aggregate_payload["horizons_hours"]:
            horizon_text = str(horizon)
            model = ModelVersion(
                model_id=f"{payload['run_id']}-h{horizon}",
                artifact_uri=str(Path(payload["aggregate"]["artifact_path"]).resolve()),
                artifact_sha256=aggregate_payload["artifact_sha256"],
                feature_schema_version=aggregate_payload["feature_schema_version"],
                feature_names=tuple(
                    aggregate_payload["models"][horizon_text]["feature_names"]
                ),
                trained_at=datetime.fromisoformat(payload["finished_at"]),
                training_start=datetime.fromisoformat(payload["started_at"]),
                training_end=datetime.fromisoformat(payload["finished_at"]),
                region=AGGREGATE_REGION,
                horizon_hours=horizon,
                metrics={
                    "algorithm": ALGORITHM,
                    "participant_count": len(participant_summaries),
                    "mae_ugm3": _evaluation_mae(payload, horizon),
                    "usable_as_real_world_evidence": False,
                    "limitations": dict(_LIMITATIONS),
                },
                synthetic_only=True,
                status=ModelStatus.CANDIDATE,
            )
            registry.upsert(model)
            model_ids.append(model.model_id)
        payload["model_versions"] = [
            {
                "model_id": model_id,
                "region": AGGREGATE_REGION,
                "status": ModelStatus.CANDIDATE.value,
                "synthetic_only": True,
                "registered": True,
            }
            for model_id in model_ids
        ]
        run_domain, participants_domain = _domain_from_payload(payload, participant_summaries)
        repository = SqlFederationRepository(session)
        repository.save(run_domain, participants_domain)
    finally:
        session.close()
    return payload


def _evaluation_mae(payload: dict[str, Any], horizon: float) -> float:
    for entry in payload["evaluation"].get("horizons", []):
        if abs(entry["horizon_hours"] - horizon) < 1e-9:
            return entry["mae_ugm3"]
    return -1.0


def _domain_from_payload(
    payload: dict[str, Any], participant_summaries: list[dict[str, Any]]
) -> tuple[FederationRun, list[FederationParticipant]]:
    finished_at = datetime.fromisoformat(payload["finished_at"])
    started_at = datetime.fromisoformat(payload["started_at"])
    run = FederationRun(
        run_id=payload["run_id"],
        status=FederationRunStatus(payload["status"]),
        participant_count=payload["participant_count"],
        region_scope=payload["region_scope"],
        feature_schema_version=payload["feature_schema_version"],
        horizons_hours=tuple(float(h) for h in payload["horizons_hours"]),
        aggregate_artifact_path=payload["aggregate"]["artifact_path"],
        aggregate_artifact_sha256=payload["aggregate"]["artifact_sha256"],
        model_version_ids=tuple(
            model["model_id"] for model in payload["model_versions"]
        ),
        evaluation=payload["evaluation"],
        raw_rows_exchanged_to_aggregator=payload["raw_rows_exchanged_to_aggregator"],
        provenance={
            "algorithm": ALGORITHM,
            "aggregate_artifact_sha256": payload["aggregate"]["artifact_sha256"],
            "limitations": dict(_LIMITATIONS),
        },
        started_at=started_at,
        finished_at=finished_at,
    )
    participants = [
        FederationParticipant(
            participant_id=summary["participant_id"],
            region_label=summary["region_label"],
            example_count=summary["example_count"],
            train_count=summary["train_count"],
            validation_count=summary["validation_count"],
            test_count=summary["test_count"],
            station_count=summary["station_count"],
            horizon_count=summary["horizon_count"],
            update_path=summary["update_path"],
            update_sha256=summary["update_sha256"],
            weight_fraction=summary["weight_fraction"],
            joined_at=datetime.fromisoformat(payload["finished_at"]),
        )
        for summary in participant_summaries
    ]
    return run, participants


def write_status_file(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.write_text(content, encoding="utf-8", newline="\n")


class FederationStatusReader:
    """Builds the documented status payload from persisted runs."""

    def __init__(self, repository: FederationRepository) -> None:
        self._repository = repository

    def status(self) -> dict[str, Any]:
        run = self._repository.latest()
        if run is None:
            return {
                "status": "no_federation_run",
                "region_scope": REGION_SCOPE,
                "limitations": dict(_LIMITATIONS),
            }
        participants = [
            {
                "participant_id": row.participant_id,
                "region_label": row.region_label,
                "example_count": row.example_count,
                "train_count": row.train_count,
                "validation_count": row.validation_count,
                "test_count": row.test_count,
                "station_count": row.station_count,
                "horizon_count": row.horizon_count,
                "update_path": row.update_path,
                "update_sha256": row.update_sha256,
                "weight_fraction": row.weight_fraction,
                "joined_at": row.joined_at.isoformat(),
            }
            for row in self._repository.list_participants(run.run_id)
        ]
        aggregate = {
            "artifact_path": run.aggregate_artifact_path,
            "artifact_sha256": run.aggregate_artifact_sha256,
            "algorithm": ALGORITHM,
            "synthetic_only": True,
        }
        return {
            "run_id": run.run_id,
            "status": run.status.value,
            "participant_count": run.participant_count,
            "region_scope": run.region_scope,
            "feature_schema_version": run.feature_schema_version,
            "horizons_hours": list(run.horizons_hours),
            "participants": participants,
            "aggregate": aggregate,
            "model_versions": {"model_ids": list(run.model_version_ids)},
            "evaluation": run.evaluation,
            "raw_rows_exchanged_to_aggregator": run.raw_rows_exchanged_to_aggregator,
            "limitations": run.provenance.get("limitations", dict(_LIMITATIONS)),
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat(),
        }
