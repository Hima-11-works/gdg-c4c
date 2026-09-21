"""Typed feature and provenance contracts for environmental predictions.

This module contains no provider, database, or ML-library imports.  It is the
shared vocabulary for feature collection, training, inference, API adapters,
and deterministic demo scenarios.  The values are deliberately explicit: a
future feature addition should change the schema version rather than silently
changing the meaning of an existing model artifact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from app.domain.types import _require_utc, _require_finite

FEATURE_SCHEMA_VERSION = "environmental-v1"


class DataMode(StrEnum):
    LIVE = "live"
    DEMO = "demo"
    MIXED = "mixed"


class InputKind(StrEnum):
    OBSERVED = "observed"
    MODELED = "modeled"
    SYNTHETIC = "synthetic"
    DERIVED = "derived"


def _bounded(value: float, field_name: str, lower: float, upper: float) -> None:
    _require_finite(value, field_name)
    if not lower <= value <= upper:
        raise ValueError(f"{field_name} must be within [{lower}, {upper}]: {value}")


@dataclass(frozen=True, slots=True)
class DatasetRef:
    """A versioned input reference carried to the API and model registry."""

    dataset_id: str
    source: str
    product: str
    version: str
    kind: InputKind
    region: str
    attribution: str
    license: str

    def __post_init__(self) -> None:
        for name in (
            "dataset_id",
            "source",
            "product",
            "version",
            "region",
            "attribution",
            "license",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class FeatureQuality:
    """Coverage and missingness information for one feature snapshot."""

    coverage_fraction: float = 1.0
    observed_station_count: int = 0
    max_observation_age_hours: float | None = None
    missing_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _bounded(self.coverage_fraction, "coverage_fraction", 0.0, 1.0)
        if self.observed_station_count < 0:
            raise ValueError("observed_station_count must be >= 0")
        if self.max_observation_age_hours is not None:
            _require_finite(self.max_observation_age_hours, "max_observation_age_hours")
            if self.max_observation_age_hours < 0:
                raise ValueError("max_observation_age_hours must be >= 0")


@dataclass(frozen=True, slots=True)
class CellStaticFeatures:
    """Slow-changing attributes aggregated to one H3 cell."""

    h3_cell: str
    population_count: float | None = None
    population_density_per_km2: float | None = None
    road_length_km_by_class: Mapping[str, float] = field(default_factory=dict)
    major_road_distance_km: float | None = None
    built_up_fraction: float | None = None
    vegetation_fraction: float | None = None
    bare_soil_fraction: float | None = None
    industrial_fraction: float | None = None
    coverage_fraction: float = 1.0
    dataset_refs: tuple[DatasetRef, ...] = ()
    valid_from: datetime | None = None
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.h3_cell.strip():
            raise ValueError("h3_cell must not be empty")
        for name in ("population_count", "population_density_per_km2", "major_road_distance_km"):
            value = getattr(self, name)
            if value is not None:
                _require_finite(value, name)
                if value < 0:
                    raise ValueError(f"{name} must be >= 0")
        for name in (
            "built_up_fraction",
            "vegetation_fraction",
            "bare_soil_fraction",
            "industrial_fraction",
        ):
            value = getattr(self, name)
            if value is not None:
                _bounded(value, name, 0.0, 1.0)
        _bounded(self.coverage_fraction, "coverage_fraction", 0.0, 1.0)
        for road_class, length in self.road_length_km_by_class.items():
            if not road_class.strip():
                raise ValueError("road class must not be empty")
            _require_finite(length, f"road_length_km_by_class[{road_class!r}]")
            if length < 0:
                raise ValueError(f"road length must be >= 0: {road_class}")
        for name, value in (("valid_from", self.valid_from), ("available_at", self.available_at)):
            if value is not None:
                _require_utc(value, name)


@dataclass(frozen=True, slots=True)
class WeatherFeature:
    """Issue-specific weather input; valid time is distinct from issue time."""

    h3_cell: str
    issued_at: datetime
    valid_at: datetime
    wind_u_ms: float | None = None
    wind_v_ms: float | None = None
    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    precipitation_mm: float | None = None
    boundary_layer_height_m: float | None = None
    temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    input_kind: InputKind = InputKind.MODELED

    def __post_init__(self) -> None:
        if not self.h3_cell.strip():
            raise ValueError("h3_cell must not be empty")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.valid_at, "valid_at")
        if self.valid_at < self.issued_at:
            raise ValueError("valid_at must not precede issued_at")
        for name in (
            "wind_u_ms",
            "wind_v_ms",
            "wind_speed_ms",
            "precipitation_mm",
            "boundary_layer_height_m",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_finite(value, name)
                if value < 0 and name in {
                    "wind_speed_ms",
                    "precipitation_mm",
                    "boundary_layer_height_m",
                }:
                    raise ValueError(f"{name} must be >= 0")
        if self.wind_direction_deg is not None:
            _bounded(self.wind_direction_deg, "wind_direction_deg", 0.0, 359.999999)
        if self.temperature_c is not None:
            _bounded(self.temperature_c, "temperature_c", -90.0, 60.0)
        if self.relative_humidity_pct is not None:
            _bounded(self.relative_humidity_pct, "relative_humidity_pct", 0.0, 100.0)


@dataclass(frozen=True, slots=True)
class CellFeatureVector:
    """Explicit model inputs for one cell and target valid time."""

    current_pm25: float | None = None
    pm25_lag_1h: float | None = None
    pm25_lag_3h: float | None = None
    pm25_lag_6h: float | None = None
    pm25_lag_24h: float | None = None
    pm25_mean_6h: float | None = None
    pm25_slope_3h: float | None = None
    rain_1h_mm: float | None = None
    rain_6h_mm: float | None = None
    rain_24h_mm: float | None = None
    hours_since_rain: float | None = None
    wind_u_ms: float | None = None
    wind_v_ms: float | None = None
    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    boundary_layer_height_m: float | None = None
    temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    hour_sin: float | None = None
    hour_cos: float | None = None
    day_of_year_sin: float | None = None
    day_of_year_cos: float | None = None
    is_weekend: float | None = None
    population_count: float | None = None
    population_density_per_km2: float | None = None
    road_length_km_per_km2: float | None = None
    major_road_distance_km: float | None = None
    built_up_fraction: float | None = None
    vegetation_fraction: float | None = None
    bare_soil_fraction: float | None = None
    industrial_fraction: float | None = None
    fire_frp_upwind_mw: float | None = None
    fire_count_upwind: float | None = None
    fire_age_hours_min: float | None = None
    traffic_congestion_ratio: float | None = None

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if value is not None:
                _require_finite(value, field_name)
        for name in (
            "rain_1h_mm",
            "rain_6h_mm",
            "rain_24h_mm",
            "hours_since_rain",
            "fire_frp_upwind_mw",
            "fire_count_upwind",
            "fire_age_hours_min",
            "population_count",
            "population_density_per_km2",
            "road_length_km_per_km2",
            "major_road_distance_km",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be >= 0")
        for name in (
            "built_up_fraction",
            "vegetation_fraction",
            "bare_soil_fraction",
            "industrial_fraction",
            "is_weekend",
        ):
            value = getattr(self, name)
            if value is not None:
                _bounded(value, name, 0.0, 1.0)
        if self.traffic_congestion_ratio is not None and self.traffic_congestion_ratio < 0:
            raise ValueError("traffic_congestion_ratio must be >= 0")


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    """The reproducible unit passed to training or inference."""

    h3_cell: str
    issued_at: datetime
    valid_at: datetime
    horizon_hours: float
    feature_schema_version: str
    vector: CellFeatureVector
    quality: FeatureQuality = field(default_factory=FeatureQuality)
    dataset_refs: tuple[DatasetRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.h3_cell.strip():
            raise ValueError("h3_cell must not be empty")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.valid_at, "valid_at")
        if self.valid_at < self.issued_at:
            raise ValueError("valid_at must not precede issued_at")
        _require_finite(self.horizon_hours, "horizon_hours")
        if self.horizon_hours < 0:
            raise ValueError("horizon_hours must be >= 0")
        if not self.feature_schema_version.strip():
            raise ValueError("feature_schema_version must not be empty")
