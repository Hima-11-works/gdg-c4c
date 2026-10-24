"""Service for managing CAMS coarse regional background data.

Operates under shared PostgreSQL provider budgets and ensures CAMS remains
a distinct background product that cannot masquerade as station observations
or satisfy OpenAQ support requirements.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.domain import h3_grid
from app.domain.cams import (
    CAMS_DEFAULT_RESOLUTION_DEG,
    CAMS_SOURCE,
    CamsBackgroundObservation,
    default_cams_dataset_ref,
)
from app.domain.types import BoundingBox
from app.services.provider_budget import Reservation, SqlProviderRequestBudget

logger = logging.getLogger(__name__)


class CamsBudgetExceededError(RuntimeError):
    """Raised when CAMS quota or rate limit is exhausted."""


class CamsContractViolationError(TypeError):
    """Raised when CAMS data is improperly presented as station observations."""


class CamsBackgroundService:
    """Provides coarse regional background data within measured provider budgets."""

    def __init__(
        self,
        request_budget: SqlProviderRequestBudget | None = None,
        *,
        default_resolution_deg: float = CAMS_DEFAULT_RESOLUTION_DEG,
    ) -> None:
        self._budget = request_budget
        self._resolution_deg = default_resolution_deg

    async def reserve_budget(
        self,
        *,
        bbox: BoundingBox,
        now: datetime | None = None,
    ) -> Reservation:
        if self._budget is None:
            return Reservation(allowed=True)
        key = f"cams:{bbox.min_lat:.2f}:{bbox.min_lon:.2f}:{bbox.max_lat:.2f}:{bbox.max_lon:.2f}"
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        reservation = await self._budget.reserve_request(
            provider="cams",
            request_key_hash=key_hash,
            request_units=1,
            bytes_reserved=10_000,
            now=now or datetime.now(UTC),
        )
        if not reservation.allowed:
            raise CamsBudgetExceededError(
                f"CAMS request refused by provider budget: {reservation.reason}"
            )
        return reservation

    def parse_background_points(
        self,
        records: Sequence[dict[str, Any]],
        *,
        issued_at: datetime,
        valid_at: datetime,
        available_at: datetime,
        h3_resolution: int = 8,
        region: str = "delhi-ncr",
    ) -> list[CamsBackgroundObservation]:
        """Convert gridded CAMS responses into typed background observations."""
        ref = default_cams_dataset_ref(region=region)
        observations: list[CamsBackgroundObservation] = []
        for row in records:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            cell = h3_grid.cell_for(lat, lon, resolution=h3_resolution)
            obs = CamsBackgroundObservation(
                h3_cell=cell,
                latitude=lat,
                longitude=lon,
                issued_at=issued_at,
                valid_at=valid_at,
                available_at=available_at,
                native_resolution_deg=float(row.get("resolution_deg", self._resolution_deg)),
                pm25_ugm3=float(row["pm25"]),
                pm10_ugm3=float(row["pm10"]) if row.get("pm10") is not None else None,
                no2_ugm3=float(row["no2"]) if row.get("no2") is not None else None,
                aod550=float(row["aod550"]) if row.get("aod550") is not None else None,
                dataset_ref=ref,
            )
            observations.append(obs)
        return observations

    @staticmethod
    def assert_never_station_readings(readings: Sequence[Any]) -> None:
        """Prevent CAMS background from being passed into station observation pools."""
        for item in readings:
            if isinstance(item, CamsBackgroundObservation):
                raise CamsContractViolationError(
                    "CAMS background observations must never be passed as station observations "
                    "or used to satisfy OpenAQ support requirements"
                )
            if getattr(item, "source", None) in {"cams", "cams-background", CAMS_SOURCE}:
                raise CamsContractViolationError(
                    "CAMS background cannot masquerade as ground station reading"
                )
