"""Copernicus Atmosphere Monitoring Service (CAMS) regional background product.

CAMS provides coarse-scale global/regional modeled atmospheric composition
forecasts (e.g., 0.4° or 0.25° native grid).

Rules:
1. CAMS is a distinct background product, never ground station observations.
2. It must never be written to sensor_reading or masquerade as CPCB/OpenAQ monitors.
3. It must never satisfy or bypass OpenAQ ground station support requirements
   (such as IDW minimum sensor counts).
4. Native model resolution and modeled provenance must always be explicitly exposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.features import DatasetRef, InputKind

CAMS_SOURCE = "Copernicus Atmosphere Monitoring Service (CAMS)"
CAMS_PRODUCT = "cams-global-atmospheric-composition"
CAMS_DEFAULT_RESOLUTION_DEG = 0.4
CAMS_LICENSE = "Copernicus License"
CAMS_ATTRIBUTION = "Contains modified Copernicus Atmosphere Monitoring Service information"


@dataclass(frozen=True, slots=True)
class CamsBackgroundObservation:
    """A coarse regional atmospheric background observation per H3 cell."""

    h3_cell: str
    latitude: float
    longitude: float
    issued_at: datetime
    valid_at: datetime
    available_at: datetime
    native_resolution_deg: float
    pm25_ugm3: float
    pm10_ugm3: float | None = None
    no2_ugm3: float | None = None
    aod550: float | None = None
    dataset_ref: DatasetRef | None = None

    def __post_init__(self) -> None:
        if self.pm25_ugm3 < 0:
            raise ValueError(f"pm25_ugm3 must be non-negative: {self.pm25_ugm3}")
        if self.native_resolution_deg <= 0:
            raise ValueError(
                f"native_resolution_deg must be positive: {self.native_resolution_deg}"
            )
        if self.valid_at < self.issued_at:
            raise ValueError("valid_at must not precede issued_at")

    @property
    def is_station_observation(self) -> bool:
        """CAMS is explicitly modeled background, never a station observation."""
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "h3_cell": self.h3_cell,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "issued_at": self.issued_at.isoformat(),
            "valid_at": self.valid_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "native_resolution_deg": self.native_resolution_deg,
            "pm25_ugm3": self.pm25_ugm3,
            "pm10_ugm3": self.pm10_ugm3,
            "no2_ugm3": self.no2_ugm3,
            "aod550": self.aod550,
            "is_station_observation": False,
            "kind": "modeled",
        }


def default_cams_dataset_ref(
    *,
    version: str = "v1",
    region: str = "delhi-ncr",
) -> DatasetRef:
    return DatasetRef(
        dataset_id=f"cams-background-{region}-{version}",
        source=CAMS_SOURCE,
        product=CAMS_PRODUCT,
        version=version,
        kind=InputKind.MODELED,
        region=region,
        attribution=CAMS_ATTRIBUTION,
        license=CAMS_LICENSE,
    )
