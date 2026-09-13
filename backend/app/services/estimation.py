"""Concrete PollutionEstimator implementations.

app.domain.estimation.PollutionEstimator is the interface; this module
holds implementations of it, the same split as providers (interface in
app.domain.providers, implementations in app.ingestion).
"""

from __future__ import annotations

from datetime import datetime

from app.domain.h3_grid import cell_center
from app.domain.types import Coordinate, GridState, SensorReading

# Below this distance a sensor is treated as coincident with the cell
# center: its value is used directly rather than divided by a near-zero
# distance. Not configurable (unlike max_distance_km/min_sensors) — it's
# a numerical-stability floor, not a modeling choice.
_MIN_DISTANCE_KM = 0.01  # 10 m


def _non_negative(value: float) -> float:
    """Low-cost PM2.5 sensors commonly report small negative values near
    true-zero concentration (calibration noise) — SensorReading.value is
    intentionally unchecked so the raw station reading is preserved as
    ingested. A physical PM2.5 concentration is never negative, so this
    estimator treats any negative input as zero rather than either
    propagating a negative estimate or letting GridState's >= 0
    validation raise and crash the whole grid's estimation over one
    noisy sensor.
    """
    return max(0.0, value)


# Sensor counts above (min_sensors + this) don't add further confidence.
# A small fixed margin, not a separate configuration knob: it only
# controls how quickly count-based confidence saturates once the
# min_sensors requirement is already met.
_CONFIDENCE_SATURATION_MARGIN = 2


class IDWPollutionEstimator:
    """Inverse-distance-weighted PM2.5 interpolation.

    For each target cell, only sensor readings within `max_distance_km` of
    the cell's center are considered. If fewer than `min_sensors` qualify,
    the cell gets pm25=None and confidence=0.0 — never a fabricated value.
    Otherwise pm25 is the IDW-weighted average of the qualifying readings,
    and confidence is a deterministic function of how many sensors were
    used and how close the nearest one is (see _confidence).

    PDI and wind are always None here: this estimator only ever sees PM2.5
    sensor readings, and has no forecast or weather data to derive them
    from — that is out of scope for "current PM2.5 estimation".
    """

    def __init__(self, *, max_distance_km: float, min_sensors: int, power: float = 2.0) -> None:
        if max_distance_km <= 0:
            raise ValueError(f"max_distance_km must be > 0: {max_distance_km}")
        if min_sensors < 1:
            raise ValueError(f"min_sensors must be >= 1: {min_sensors}")
        if power <= 0:
            raise ValueError(f"power must be > 0: {power}")
        self._max_distance_km = max_distance_km
        self._min_sensors = min_sensors
        self._power = power

    def estimate(
        self,
        grid: list[str],
        sensor_readings: list[SensorReading],
        *,
        timestamp: datetime,
    ) -> list[GridState]:
        points = [
            (reading, Coordinate(reading.latitude, reading.longitude))
            for reading in sensor_readings
        ]
        return [self._estimate_cell(cell, points, timestamp) for cell in grid]

    def _estimate_cell(
        self,
        cell: str,
        points: list[tuple[SensorReading, Coordinate]],
        timestamp: datetime,
    ) -> GridState:
        center_lat, center_lon = cell_center(cell)
        center = Coordinate(center_lat, center_lon)

        nearby = [
            (reading, distance)
            for reading, point in points
            if (distance := center.distance_km(point)) <= self._max_distance_km
        ]

        if len(nearby) < self._min_sensors:
            return GridState(h3_cell=cell, timestamp=timestamp, confidence=0.0)

        nearest_distance = min(distance for _, distance in nearby)

        if nearest_distance <= _MIN_DISTANCE_KM:
            # A sensor is (for practical purposes) inside this cell: use
            # its value directly rather than dividing by a near-zero
            # distance. This is standard IDW practice, not a workaround —
            # an exact/near-exact match is the correct answer, full stop.
            exact = next(reading for reading, distance in nearby if distance <= _MIN_DISTANCE_KM)
            pm25 = _non_negative(exact.value)
        else:
            weights = [1.0 / (distance**self._power) for _, distance in nearby]
            weighted_sum = sum(
                weight * _non_negative(reading.value)
                for (reading, _), weight in zip(nearby, weights, strict=True)
            )
            pm25 = weighted_sum / sum(weights)

        confidence = self._confidence(nearest_distance, len(nearby))
        return GridState(h3_cell=cell, timestamp=timestamp, confidence=confidence, pm25=pm25)

    def _confidence(self, nearest_distance_km: float, sensor_count: int) -> float:
        """Deterministic, bounded to [0, 1]: closer sensors and more of
        them (up to a saturation point) both raise confidence. An exact
        on-site match is always maximum confidence.
        """
        if nearest_distance_km <= _MIN_DISTANCE_KM:
            return 1.0
        distance_factor = max(0.0, 1.0 - nearest_distance_km / self._max_distance_km)
        count_factor = min(1.0, sensor_count / (self._min_sensors + _CONFIDENCE_SATURATION_MARGIN))
        return distance_factor * count_factor
