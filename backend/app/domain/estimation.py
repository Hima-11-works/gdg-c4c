"""Abstract interface (port) for pollution interpolation/estimation models.

IDWPollutionEstimator (app.services.estimation) is the first
implementation. Kriging, satellite fusion, or an ML model can implement
this same Protocol later — nothing that calls a PollutionEstimator (an
API endpoint, a future pipeline) needs to change to use a different one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.types import GridState, SensorReading


class PollutionEstimator(Protocol):
    def estimate(
        self,
        grid: list[str],
        sensor_readings: list[SensorReading],
        *,
        timestamp: datetime,
    ) -> list[GridState]:
        """One GridState per cell in `grid`, in the same order, estimated
        from `sensor_readings`.

        `timestamp` is supplied by the caller (not read from a clock here)
        so implementations are pure functions of their inputs — the same
        call always produces the same result, which is what makes this
        testable without mocking time.

        Must never fabricate a value: a cell without enough nearby
        evidence gets pm25=None and confidence=0.0, not a number invented
        to fill the gap. What counts as "enough" is a property of the
        configured implementation (e.g. IDWPollutionEstimator's
        max_distance_km / min_sensors), not of this interface.
        """
        ...
