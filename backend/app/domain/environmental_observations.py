"""Retained, provenance-aware fire and traffic source observations."""

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
