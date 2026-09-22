"""Training data and immutable model-registry contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Mapping

from app.domain.features import DataMode, InputKind
from app.domain.types import _require_finite, _require_utc


class ModelStatus(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class TrainingExample:
    """A station-supported target joined to as-of features and baselines."""

    example_id: str
    station_id: str
    h3_cell: str
    region: str
    issued_at: datetime
    target_at: datetime
    horizon_hours: float
    baseline_pm25: float
    target_pm25: float
    features: Mapping[str, float | None]
    data_mode: DataMode
    target_kind: InputKind
    feature_schema_version: str
    dataset_ids: tuple[str, ...] = ()
    spatial_exclusion_verified: bool = False

    def __post_init__(self) -> None:
        for name in ("example_id", "station_id", "h3_cell", "region", "feature_schema_version"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.target_at, "target_at")
        if self.target_at <= self.issued_at:
            raise ValueError("target_at must be after issued_at")
        _require_finite(self.horizon_hours, "horizon_hours")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be > 0")
        expected = (self.target_at - self.issued_at).total_seconds() / 3600
        if not math.isclose(expected, self.horizon_hours, rel_tol=0, abs_tol=1e-6):
            raise ValueError("horizon_hours must agree with target_at - issued_at")
        for name in ("baseline_pm25", "target_pm25"):
            value = getattr(self, name)
            _require_finite(value, name)
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
        if not isinstance(self.data_mode, DataMode) or not isinstance(
            self.target_kind, InputKind
        ):
            raise ValueError("data_mode and target_kind must use their domain enums")
        if self.data_mode is DataMode.LIVE and self.target_kind is not InputKind.OBSERVED:
            raise ValueError("live training examples require observed targets")
        if self.data_mode is DataMode.DEMO and self.target_kind is not InputKind.SYNTHETIC:
            raise ValueError("demo training examples require synthetic targets")
        if not isinstance(self.spatial_exclusion_verified, bool):
            raise ValueError("spatial_exclusion_verified must be a boolean")
        for name, value in self.features.items():
            if not name.strip():
                raise ValueError("feature names must not be empty")
            if value is not None:
                _require_finite(value, f"features[{name!r}]")
        for dataset_id in self.dataset_ids:
            if not dataset_id.strip():
                raise ValueError("dataset_ids must not contain empty values")


@dataclass(frozen=True, slots=True)
class ModelVersion:
    """Registry row for one immutable artifact/horizon pair."""

    model_id: str
    artifact_uri: str
    artifact_sha256: str
    feature_schema_version: str
    feature_names: tuple[str, ...]
    trained_at: datetime
    training_start: datetime
    training_end: datetime
    region: str
    horizon_hours: float
    metrics: Mapping[str, object] = field(default_factory=dict)
    synthetic_only: bool = False
    status: ModelStatus = ModelStatus.CANDIDATE

    def __post_init__(self) -> None:
        if not isinstance(self.status, ModelStatus):
            raise ValueError("status must use the ModelStatus enum")
        for name in (
            "model_id",
            "artifact_uri",
            "artifact_sha256",
            "feature_schema_version",
            "region",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if len(self.artifact_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.artifact_sha256
        ):
            raise ValueError("artifact_sha256 must be a SHA-256 hex digest")
        _require_utc(self.trained_at, "trained_at")
        _require_utc(self.training_start, "training_start")
        _require_utc(self.training_end, "training_end")
        if self.training_end < self.training_start:
            raise ValueError("training_end must not precede training_start")
        _require_finite(self.horizon_hours, "horizon_hours")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be > 0")
        if self.status is ModelStatus.PROMOTED and self.synthetic_only:
            raise ValueError("synthetic-only artifacts cannot be promoted")
        if len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("feature_names must not contain duplicates")
        for name in self.feature_names:
            if not name.strip():
                raise ValueError("feature_names must not contain empty values")


def assert_live_promotion_allowed(model: ModelVersion) -> None:
    """Reject synthetic-only and not-yet-validated candidates at live promotion."""

    if model.synthetic_only:
        raise ValueError("synthetic-only artifacts cannot be promoted to live")
    if model.status not in {ModelStatus.VALIDATED, ModelStatus.PROMOTED}:
        raise ValueError("only validated artifacts can be promoted to live")
