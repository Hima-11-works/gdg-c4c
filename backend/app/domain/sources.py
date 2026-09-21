"""Abstract interface (port) for turning citizen fire reports into a
per-cell pollution contribution.

app.services.fire_gradient.PlumeFireGradientModel is the first
implementation; a downwind-aware (wind-stretched) or satellite-calibrated
model can implement this same Protocol later without anything above it
changing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.types import FireReport


class FireGradientModel(Protocol):
    def contributions(
        self,
        grid: list[str],
        reports: list[FireReport],
        *,
        timestamp: datetime,
    ) -> list[float]:
        """One additional-PM2.5 value per cell in `grid`, in the same order.

        `timestamp` is supplied by the caller so implementations are pure
        functions of their inputs (same call, same result — testable
        without mocking time). Values are the *modeled* extra emission the
        reports justify, not a measurement: blending them with the sensor
        estimate is the caller's (GridComputationService's) job, along with
        any final cap. Must never return a negative value.
        """
        ...
