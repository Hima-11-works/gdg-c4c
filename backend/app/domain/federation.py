"""Domain types for the two-region federation demonstration.

Pure dataclasses and enums only: no SQLAlchemy, no filesystem, and no
services imports (the domain layer may only depend on core). The federation
math (partitioning, local fitting, aggregation, evaluation) lives in
app.services.federation.

See docs/api/federation.md for the wire and status contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.types import _require_utc

# Every run is pinned to this scope label; the demonstration is two disjoint
# partitions of one synthetic regional dataset - never a nationwide claim.
REGION_SCOPE = "two-partition-synthetic-demonstration"

# The two demonstration participants. Deterministic partition assignment is
# documented in the service; the labels here are fixed so the status contract
# is stable between runs.
PARTICIPANT_IDS: tuple[str, str] = ("region-a", "region-b")


class FederationRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class FederationParticipant:
    """One participating client region for one run."""

    participant_id: str
    region_label: str
    example_count: int
    train_count: int
    validation_count: int
    test_count: int
    station_count: int
    horizon_count: int
    update_path: str
    update_sha256: str
    weight_fraction: float
    joined_at: datetime

    def __post_init__(self) -> None:
        for name in ("participant_id", "region_label", "update_path", "update_sha256"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if len(self.update_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.update_sha256
        ):
            raise ValueError("update_sha256 must be a SHA-256 hex digest")
        for name in (
            "example_count",
            "train_count",
            "validation_count",
            "test_count",
            "station_count",
            "horizon_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if not 0.0 <= self.weight_fraction <= 1.0:
            raise ValueError("weight_fraction must be within [0, 1]")
        _require_utc(self.joined_at, "joined_at")


@dataclass(frozen=True, slots=True)
class FederationRun:
    """One recorded federation demonstration run."""

    run_id: str
    status: FederationRunStatus
    participant_count: int
    region_scope: str
    feature_schema_version: str
    horizons_hours: tuple[float, ...]
    aggregate_artifact_path: str
    aggregate_artifact_sha256: str
    model_version_ids: tuple[str, ...]
    evaluation: dict
    raw_rows_exchanged_to_aggregator: int
    provenance: dict
    started_at: datetime
    finished_at: datetime

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not isinstance(self.status, FederationRunStatus):
            raise ValueError("status must use the FederationRunStatus enum")
        if self.participant_count < 2:
            raise ValueError("a federation run needs at least two participants")
        if self.region_scope != REGION_SCOPE:
            raise ValueError(f"region_scope must be {REGION_SCOPE!r}")
        if len(self.aggregate_artifact_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.aggregate_artifact_sha256
        ):
            raise ValueError(
                "aggregate_artifact_sha256 must be a SHA-256 hex digest"
            )
        if not self.aggregate_artifact_path.strip():
            raise ValueError("aggregate_artifact_path must not be empty")
        _require_utc(self.started_at, "started_at")
        _require_utc(self.finished_at, "finished_at")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.raw_rows_exchanged_to_aggregator != 0:
            # The exchange contract is update payloads only; a nonzero raw-row
            # count would invalidate the guarantee the status reports.
            raise ValueError("raw observation rows must not be exchanged")
        for model_id in self.model_version_ids:
            if not model_id.strip():
                raise ValueError("model_version_ids must not contain empty values")

    @property
    def succeeded(self) -> bool:
        return self.status is FederationRunStatus.SUCCEEDED
