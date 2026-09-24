"""Domain types for citizen intake evidence: a photo and/or a local sensor
reading attached to a fire report.

Deliberately separate from app.domain.types.SensorReading: that type is a
*trusted station observation* and is what the pollution model consumes. A
CitizenSensorReading is unverified user evidence, is stored only alongside its
report, and is never fed to the grid. Keeping the two types distinct means the
separation is enforced by the type system, not by convention.

No SQLAlchemy / filesystem imports here — plain dataclasses, like the rest of
the domain layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.types import _require_finite, _require_utc

# The provenance string a citizen reading always carries, so nothing can
# mistake it for a station source like 'openaq'.
CITIZEN_SENSOR_SOURCE = "citizen"


class VerificationStatus(StrEnum):
    """Moderation/verification state of an evidence record.

    Starts at UNVERIFIED. Nothing in the intake path promotes its own
    evidence; a transition to VERIFIED is a separate moderator action
    (out of scope here).
    """

    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"

    @property
    def is_trusted(self) -> bool:
        """Only a VERIFIED record counts as trusted evidence."""
        return self is VerificationStatus.VERIFIED


@dataclass(frozen=True, slots=True)
class MediaRef:
    """Where a stored photo lives and what it is.

    `key` is opaque to callers (a storage-layer detail): the public URL is
    always the report-id route, so the storage backend stays replaceable.
    """

    content_type: str
    byte_size: int
    sha256: str
    key: str
    # True when the bytes are a generated placeholder rather than a real
    # upload — never set by the intake path, but modelled so a future
    # backend that substitutes a stand-in can say so explicitly.
    is_placeholder: bool = False

    def __post_init__(self) -> None:
        if not self.content_type:
            raise ValueError("content_type must not be empty")
        if self.byte_size <= 0:
            raise ValueError("byte_size must be positive")
        if not self.sha256:
            raise ValueError("sha256 must not be empty")
        if not self.key:
            raise ValueError("key must not be empty")


@dataclass(frozen=True, slots=True)
class CitizenSensorReading:
    """A citizen-submitted pollutant value. Unverified evidence, not a
    measurement driving the grid — see the module docstring."""

    pollutant: str
    value: float
    unit: str
    measured_at: datetime
    latitude: float
    longitude: float
    source: str = CITIZEN_SENSOR_SOURCE

    def __post_init__(self) -> None:
        if not self.pollutant:
            raise ValueError("pollutant must not be empty")
        if not self.unit:
            raise ValueError("unit must not be empty")
        _require_finite(self.value, "value")
        if self.value < 0:
            raise ValueError(f"value must be >= 0, got {self.value}")
        _require_utc(self.measured_at, "measured_at")
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude out of range: {self.longitude}")
        if not self.source:
            raise ValueError("source must not be empty")
        if not math.isfinite(self.latitude) or not math.isfinite(self.longitude):
            raise ValueError("coordinates must be finite")


@dataclass(frozen=True, slots=True)
class ReportEvidence:
    """The single evidence record a report may carry: an optional photo, an
    optional citizen sensor reading, provenance, and a moderation status."""

    report_id: int
    verification_status: VerificationStatus
    submitted_at: datetime
    media: MediaRef | None = None
    sensor: CitizenSensorReading | None = None
    notes: str | None = None
    client_report_id: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        if self.media is None and self.sensor is None:
            raise ValueError("an evidence record must carry a photo, a sensor reading, or both")
        _require_utc(self.submitted_at, "submitted_at")

    @property
    def is_verified(self) -> bool:
        return self.verification_status.is_trusted
