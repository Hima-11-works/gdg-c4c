"""Explicit review, calibration, and validation rules for citizen sensor submissions.

Citizen sensors provide valuable hyper-local context, but they must meet strict
operational criteria before entering spatial interpolation:
1. They must be explicitly human-reviewed and verified (status == 'verified').
2. They must pass physical bounds and apply verified device calibration.
3. They must never displace or compromise independent reference station validation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain import india
from app.domain.types import PM25, SensorReading


class CitizenCalibrationError(ValueError):
    """Raised when a citizen submission fails calibration or review requirements."""


class UnverifiedCitizenSensorError(CitizenCalibrationError):
    """Raised when an unverified citizen submission attempts to enter interpolation."""


class IndependentStationHoldoutTaintedError(RuntimeError):
    """Raised when citizen sensors are improperly used as reference holdout validation."""


@dataclass(frozen=True, slots=True)
class CitizenCalibrationPolicy:
    """Explicit quality and calibration bounds for citizen sensors."""

    require_reviewed: bool = True
    max_reading_age_hours: float = 6.0
    min_pm25_ugm3: float = 0.0
    max_pm25_ugm3: float = 1000.0
    # Optical particle counter hygroscopic growth adjustment factor (slope)
    calibration_slope: float = 0.85
    # Zero-offset adjustment (intercept)
    calibration_intercept: float = 0.0
    # Require location within India geofence
    require_india_geofence: bool = True


@dataclass(frozen=True, slots=True)
class CalibratedCitizenReading:
    """A verified, calibrated citizen reading ready for interpolation."""

    submission_id: str
    client_submission_id: str
    latitude: float
    longitude: float
    raw_pm25_ugm3: float
    calibrated_pm25_ugm3: float
    device_label: str
    measured_at: datetime
    reviewed_at: datetime
    source_prefix: str
    is_reference_station: bool = False

    def to_sensor_reading(self) -> SensorReading:
        """Project into a typed SensorReading with distinct citizen provenance."""
        return SensorReading(
            source="citizen_calibrated",
            external_sensor_id=self.client_submission_id,
            canonical_station_id=f"citizen:{self.client_submission_id}",
            latitude=self.latitude,
            longitude=self.longitude,
            pollutant=PM25,
            value=self.calibrated_pm25_ugm3,
            unit="µg/m³",
            measured_at=self.measured_at,
        )


def qualify_citizen_submission(
    submission: Mapping[str, Any],
    *,
    policy: CitizenCalibrationPolicy | None = None,
    now: datetime | None = None,
) -> CalibratedCitizenReading:
    """Validate explicit human review and apply calibration formula."""
    cfg = policy or CitizenCalibrationPolicy()
    current_time = now or datetime.now(UTC)

    # 1. Explicit review requirement
    status = submission.get("status")
    if cfg.require_reviewed and status != "verified":
        raise UnverifiedCitizenSensorError(
            f"citizen submission status is {status!r}; "
            "only 'verified' readings may enter interpolation"
        )

    # 2. Coordinates & geofence
    lat = float(submission["latitude"])
    lon = float(submission["longitude"])
    if cfg.require_india_geofence and not india.is_inside_india(lat, lon):
        raise CitizenCalibrationError(f"coordinates ({lat}, {lon}) are outside the India geofence")

    # 3. Measurement freshness
    raw_measured = submission["measured_at"]
    if isinstance(raw_measured, str):
        measured_at = datetime.fromisoformat(raw_measured.replace("Z", "+00:00")).astimezone(UTC)
    elif isinstance(raw_measured, datetime):
        measured_at = raw_measured.astimezone(UTC)
    else:
        raise CitizenCalibrationError("invalid measured_at format")

    if measured_at > current_time + timedelta(minutes=5):
        raise CitizenCalibrationError("measured_at is in the future")
    age_hours = (current_time - measured_at).total_seconds() / 3600.0
    if age_hours > cfg.max_reading_age_hours:
        raise CitizenCalibrationError(
            f"citizen reading is {age_hours:.1f}h old; "
            f"exceeds max allowed {cfg.max_reading_age_hours:g}h"
        )

    # 4. Physical bounds
    raw_pm25 = float(submission["pm25_ugm3"])
    if raw_pm25 < cfg.min_pm25_ugm3 or raw_pm25 > cfg.max_pm25_ugm3:
        raise CitizenCalibrationError(
            f"raw PM2.5 {raw_pm25} µg/m³ is outside calibrated physical bounds "
            f"[{cfg.min_pm25_ugm3}, {cfg.max_pm25_ugm3}]"
        )

    # 5. Device calibration calculation: PM2.5_cal = slope * PM2.5_raw + intercept
    calibrated_pm25 = max(0.0, raw_pm25 * cfg.calibration_slope + cfg.calibration_intercept)

    reviewed_at = submission.get("reviewed_at")
    if isinstance(reviewed_at, str):
        rev_dt = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00")).astimezone(UTC)
    elif isinstance(reviewed_at, datetime):
        rev_dt = reviewed_at.astimezone(UTC)
    else:
        rev_dt = current_time

    return CalibratedCitizenReading(
        submission_id=str(submission.get("id", "")),
        client_submission_id=str(submission["client_submission_id"]),
        latitude=lat,
        longitude=lon,
        raw_pm25_ugm3=raw_pm25,
        calibrated_pm25_ugm3=calibrated_pm25,
        device_label=str(submission.get("device_label", "unknown")),
        measured_at=measured_at,
        reviewed_at=rev_dt,
        source_prefix=str(submission.get("source_prefix", "unknown")),
        is_reference_station=False,
    )


def assert_retains_independent_station_validation(
    holdout_station_ids: Sequence[str],
    *,
    context: str = "evaluation holdout",
) -> None:
    """Verify that independent validation stations contain only reference monitors.

    Citizen sensors must never be treated as independent validation stations
    or counted toward holdout station requirements.
    """
    for station_id in holdout_station_ids:
        if station_id.startswith("citizen:") or "citizen" in station_id.lower():
            raise IndependentStationHoldoutTaintedError(
                f"citizen sensor {station_id!r} cannot be used as an independent reference station "
                f"for {context}; independent validation requires official reference stations"
            )
