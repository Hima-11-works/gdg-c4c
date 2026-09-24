"""Schemas for corridor events and their evaluations. See
docs/api/corridor-evaluation.md."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "CorridorOut",
    "CorridorEventOut",
    "CorridorEvaluationOut",
    "HorizonPointOut",
    "ScoreSliceOut",
]


class CorridorOut(BaseModel):
    corridor_id: str
    name: str
    kind: str
    region: str
    # Always "illustrative" for the shipped geometry. Kept as an explicit field
    # so a client cannot mistake the corridor cells for a road route.
    geometry_source: str
    geometry_note: str
    geometry_description: str
    h3_resolution: int = Field(ge=0, le=15)
    cell_count: int
    endpoints: list[dict[str, Any]]
    notes: str


class HorizonPointOut(BaseModel):
    horizon_hours: float = Field(ge=0)
    issued_at: datetime
    valid_at: datetime


class CorridorEventOut(BaseModel):
    event_id: str
    corridor_id: str
    corridor_name: str
    run_id: str
    run_mode: str
    run_synthetic: bool
    issued_at: datetime
    horizons: list[HorizonPointOut]
    cell_count: int
    cells: list[str]
    peak_predicted_ugm3: float | None = None
    peak_horizon_hours: float | None = None
    label_count: int
    label_sources: list[str]


class ScoreSliceOut(BaseModel):
    horizon_hours: float
    geography: str
    pairs: int
    station_count: int
    mae_ugm3: float | None = None
    rmse_ugm3: float | None = None
    bias_ugm3: float | None = None
    high_pollution_threshold_ugm3: float
    high_pollution_observed: int
    high_pollution_recall: float | None = None
    high_pollution_precision: float | None = None
    # False means "do not quote these numbers": the slice has too few labels.
    sufficient: bool
    note: str


class CorridorEvaluationOut(BaseModel):
    verdict: str
    usable_as_real_world_evidence: bool
    label_provenance: str
    reasons: list[str]
    min_labels: int
    high_pollution_threshold_ugm3: float
    coverage: dict[str, Any]
    slices: list[ScoreSliceOut]
    evidence: list[dict[str, Any]]
