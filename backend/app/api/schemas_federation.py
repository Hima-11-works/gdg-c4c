"""Schemas for the federation status endpoint. Contract:
docs/api/federation.md."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class FederationParticipantOut(BaseModel):
    participant_id: str
    region_label: str
    example_count: int = Field(ge=0)
    train_count: int = Field(ge=0)
    validation_count: int = Field(ge=0)
    test_count: int = Field(ge=0)
    station_count: int = Field(ge=0)
    horizon_count: int = Field(ge=0)
    update_path: str
    update_sha256: str
    weight_fraction: float = Field(ge=0, le=1)
    joined_at: datetime


class FederationAggregateOut(BaseModel):
    artifact_path: str
    artifact_sha256: str
    algorithm: str
    synthetic_only: bool


class FederationModelVersionOut(BaseModel):
    model_id: str
    region: str
    horizon_hours: float = Field(gt=0)
    status: str
    synthetic_only: bool


class FederationEvaluationHorizonOut(BaseModel):
    horizon_hours: float
    mae_ugm3: float
    baseline_mae_ugm3: float
    rmse_ugm3: float
    bias_ugm3: float | None
    heldout_count: int
    seasons_seen: list[str]


class FederationEvaluationOut(BaseModel):
    status: str = Field(description="synthetic_evaluation_only | unavailable")
    usable_as_real_world_evidence: bool
    reason: str
    heldout_examples: int | None = None
    horizons: list[FederationEvaluationHorizonOut] | None = None


class FederationStatusOut(BaseModel):
    """The status contract. `status` is `succeeded`/`failed` for a recorded
    run, or `no_federation_run` before anything was run."""

    status: str
    run_id: str | None = None
    participant_count: int | None = None
    region_scope: str | None = Field(
        default=None,
        description="Always 'two-partition-synthetic-demonstration' - a labeled "
        "two-partition synthetic demonstration, never a nationwide claim.",
    )
    feature_schema_version: str | None = None
    horizons_hours: list[float] | None = None
    participants: list[FederationParticipantOut] | None = None
    aggregate: dict | None = None
    model_versions: dict | None = None
    evaluation: dict | None = None
    raw_rows_exchanged_to_aggregator: int | None = None
    limitations: dict | None = Field(
        default=None,
        description="Recorded limitations: no established privacy guarantee, "
        "no nationwide deployment, synthetic-only accuracy evidence.",
    )
    started_at: str | None = None
    finished_at: str | None = None
