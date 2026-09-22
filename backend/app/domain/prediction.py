"""Immutable contracts for a published environmental prediction run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from app.domain.features import DataMode, DatasetRef, FeatureQuality, InputKind
from app.domain.types import _require_finite, _require_utc


@dataclass(frozen=True, slots=True)
class PredictionRun:
    run_id: str
    generated_at: datetime
    region: str
    mode: DataMode
    feature_run_id: str
    feature_schema_version: str
    model_versions: tuple[str, ...] = ()
    scenario_id: str | None = None
    dataset_refs: tuple[DatasetRef, ...] = ()
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "region", "feature_run_id", "feature_schema_version"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        _require_utc(self.generated_at, "generated_at")
        if self.published_at is not None:
            _require_utc(self.published_at, "published_at")
            if self.published_at < self.generated_at:
                raise ValueError("published_at must not precede generated_at")


@dataclass(frozen=True, slots=True)
class PredictionResult:
    run_id: str
    h3_cell: str
    horizon_hours: float
    valid_at: datetime
    baseline_pm25: float | None
    predicted_pm25: float | None
    lower_pm25: float | None = None
    upper_pm25: float | None = None
    pdi: float | None = None
    prediction_method: str = "persistence-baseline"
    model_version: str | None = None
    feature_schema_version: str = "environmental-v1"
    input_kind: InputKind = InputKind.MODELED
    synthetic: bool = False
    quality: FeatureQuality = field(default_factory=FeatureQuality)
    dataset_refs: tuple[DatasetRef, ...] = ()
    feature_vector: Mapping[str, float | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("run_id", "h3_cell", "prediction_method", "feature_schema_version"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        _require_utc(self.valid_at, "valid_at")
        _require_finite(self.horizon_hours, "horizon_hours")
        if self.horizon_hours < 0:
            raise ValueError("horizon_hours must be >= 0")
        for name in ("baseline_pm25", "predicted_pm25", "lower_pm25", "upper_pm25"):
            value = getattr(self, name)
            if value is not None:
                _require_finite(value, name)
                if value < 0:
                    raise ValueError(f"{name} must be >= 0")
        if self.pdi is not None:
            _require_finite(self.pdi, "pdi")
        for name, value in self.feature_vector.items():
            if not name.strip():
                raise ValueError("feature vector names must not be empty")
            if value is not None:
                _require_finite(value, f"feature_vector[{name!r}]")
        if self.lower_pm25 is not None and self.upper_pm25 is not None:
            if self.lower_pm25 > self.upper_pm25:
                raise ValueError("lower_pm25 must not exceed upper_pm25")
