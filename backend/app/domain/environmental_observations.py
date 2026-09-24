"""Retained, provenance-aware fire, forecast-weather and traffic source
observations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from app.domain.types import _require_utc


def _finite_nonnegative(value: float | None, name: str) -> None:
    if value is None:
        return
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and >= 0")


@dataclass(frozen=True, slots=True)
class FireHotspot:
    """One source detection; it is not proof of a confirmed ground fire."""

    detection_id: str
    h3_cell: str
    dataset_id: str
    ingestion_run_id: str
    source: str
    product: str
    product_version: str
    satellite: str
    instrument: str
    latitude: float
    longitude: float
    acquired_at: datetime
    available_at: datetime
    frp_mw: float
    confidence_raw: str
    confidence_class: str
    scan_km: float | None = None
    track_km: float | None = None
    brightness_ti4_k: float | None = None
    brightness_ti5_k: float | None = None
    daynight: str | None = None
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "detection_id", "h3_cell", "dataset_id", "ingestion_run_id", "source",
            "product", "product_version", "satellite", "instrument", "confidence_raw",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not -90 <= self.latitude <= 90 or not math.isfinite(self.latitude):
            raise ValueError("latitude must be finite and within [-90, 90]")
        if not -180 <= self.longitude <= 180 or not math.isfinite(self.longitude):
            raise ValueError("longitude must be finite and within [-180, 180]")
        _require_utc(self.acquired_at, "acquired_at")
        _require_utc(self.available_at, "available_at")
        if self.available_at < self.acquired_at:
            raise ValueError("available_at must not precede acquired_at")
        _finite_nonnegative(self.frp_mw, "frp_mw")
        for name in ("scan_km", "track_km", "brightness_ti4_k", "brightness_ti5_k"):
            _finite_nonnegative(getattr(self, name), name)
        if self.confidence_class not in {"low", "nominal", "high", "unknown"}:
            raise ValueError("confidence_class must be low, nominal, high, or unknown")
        if self.daynight is not None and self.daynight not in {"D", "N"}:
            raise ValueError("daynight must be D, N, or null")


@dataclass(frozen=True, slots=True)
class WeatherForecast:
    """One modeled forecast-weather value for a cell and a future valid time.

    Distinct from `WeatherReading` (an observation) on purpose: a forecast
    carries the time it was *issued* separately from the time it is *valid* for.
    The publication path only accepts a forecast whose `issued_at` is at or
    before the prediction issue time, so a future horizon can never be filled
    with a forecast that did not exist yet when the prediction was made.
    """

    forecast_id: str
    dataset_id: str
    ingestion_run_id: str
    source: str
    h3_cell: str
    issued_at: datetime
    valid_at: datetime
    horizon_hours: float
    wind_speed_ms: float
    wind_direction_deg: float
    precipitation_mm: float
    boundary_layer_height_m: float | None = None
    temperature_c: float | None = None
    relative_humidity_pct: float | None = None

    def __post_init__(self) -> None:
        for name in ("forecast_id", "dataset_id", "ingestion_run_id", "source", "h3_cell"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.valid_at, "valid_at")
        if self.valid_at < self.issued_at:
            raise ValueError("valid_at must not precede issued_at")
        if not math.isfinite(self.horizon_hours) or self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be finite and > 0")
        _finite_nonnegative(self.wind_speed_ms, "wind_speed_ms")
        if not math.isfinite(self.wind_direction_deg) or not (
            0 <= self.wind_direction_deg < 360
        ):
            raise ValueError("wind_direction_deg must be within [0, 360)")
        _finite_nonnegative(self.precipitation_mm, "precipitation_mm")
        _finite_nonnegative(self.boundary_layer_height_m, "boundary_layer_height_m")
        if self.temperature_c is not None and not (
            -90 <= self.temperature_c <= 60
        ):
            raise ValueError("temperature_c must be within [-90, 60]")
        if self.relative_humidity_pct is not None and not (
            0 <= self.relative_humidity_pct <= 100
        ):
            raise ValueError("relative_humidity_pct must be within [0, 100]")

    @property
    def is_usable_for(self, issued_before: datetime) -> bool:
        """Whether this forecast existed by `issued_before` (leakage guard)."""
        return self.issued_at <= issued_before


@dataclass(frozen=True, slots=True)
class TrafficObservation:
    """One sampled road-speed observation from an explicitly licensed feed.

    `observed_free_flow_ratio` follows the feature-plan convention: observed
    speed divided by the reference free-flow speed; lower values mean slower
    traffic. It is retained alongside the original speeds for auditability.
    """

    observation_id: str
    dataset_id: str
    ingestion_run_id: str
    source: str
    road_id: str
    h3_cell: str
    observed_at: datetime
    available_at: datetime
    observed_speed_kph: float
    free_flow_speed_kph: float
    observed_free_flow_ratio: float
    confidence: float | None
    sampled_road_coverage_fraction: float
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "observation_id", "dataset_id", "ingestion_run_id", "source", "road_id", "h3_cell",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        _require_utc(self.observed_at, "observed_at")
        _require_utc(self.available_at, "available_at")
        if self.available_at < self.observed_at:
            raise ValueError("available_at must not precede observed_at")
        if not math.isfinite(self.observed_speed_kph) or self.observed_speed_kph < 0:
            raise ValueError("observed_speed_kph must be finite and >= 0")
        if not math.isfinite(self.free_flow_speed_kph) or self.free_flow_speed_kph <= 0:
            raise ValueError("free_flow_speed_kph must be finite and > 0")
        if not math.isfinite(self.observed_free_flow_ratio) or self.observed_free_flow_ratio < 0:
            raise ValueError("observed_free_flow_ratio must be finite and >= 0")
        expected_ratio = self.observed_speed_kph / self.free_flow_speed_kph
        if not math.isclose(
            self.observed_free_flow_ratio, expected_ratio, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("observed_free_flow_ratio must equal observed/free-flow speed")
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be null or within [0, 1]")
        if not math.isfinite(self.sampled_road_coverage_fraction) or not (
            0 <= self.sampled_road_coverage_fraction <= 1
        ):
            raise ValueError("sampled_road_coverage_fraction must be within [0, 1]")
