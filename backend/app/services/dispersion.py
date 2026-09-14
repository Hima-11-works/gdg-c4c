"""Concrete PollutionForecastModel implementations.

app.domain.dispersion.PollutionForecastModel is the interface; this
module holds implementations of it, the same split as estimators
(app.domain.estimation / app.services.estimation) and PDI models
(app.domain.pdi / app.services.pdi).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.dispersion import ForecastResult
from app.domain.h3_grid import cell_center, grid_disk
from app.domain.types import Coordinate, Forecast, GridState, WeatherReading


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def _angular_difference(bearing_a: float, bearing_b: float) -> float:
    """Minimal absolute difference between two compass bearings, in [0, 180]."""
    return abs((bearing_a - bearing_b + 180.0) % 360.0 - 180.0)


def _downwind_weights(
    neighbor_bearings: list[tuple[str, float]],
    wind_direction: float,
    *,
    half_angle_deg: float,
) -> list[tuple[str, float]]:
    """Which neighbors receive transported mass, and how much of it.

    `wind_direction` is meteorological convention (direction the wind
    blows FROM), so mass actually moves toward `wind_direction + 180`.
    Every neighbor within `half_angle_deg` of that downwind bearing gets
    a share, weighted linearly by how close it is to dead-on-downwind
    (closer = more) — a "cone" rather than a single nearest-bearing pick,
    so a small change in wind direction shifts weights continuously
    instead of flipping 100% of transport from one hex to the next.
    Weights always sum to 1 over the returned list.
    """
    if not neighbor_bearings:
        return []

    downwind_bearing = (wind_direction + 180.0) % 360.0
    scored = [
        (neighbor, half_angle_deg - _angular_difference(bearing, downwind_bearing))
        for neighbor, bearing in neighbor_bearings
        if _angular_difference(bearing, downwind_bearing) <= half_angle_deg
    ]

    if not scored:
        # Degenerate case (shouldn't normally happen given ~60 degree hex
        # neighbor spacing and a wider cone): fall back to the single
        # neighbor nearest the downwind bearing rather than dropping the
        # mass with nowhere to go.
        nearest = min(
            neighbor_bearings, key=lambda nb: _angular_difference(nb[1], downwind_bearing)
        )
        return [(nearest[0], 1.0)]

    total_weight = sum(weight for _, weight in scored)
    if total_weight <= 0:
        # Every candidate sits exactly on the cone's edge (weight 0): no
        # basis to prefer one over another, so split evenly instead of
        # dividing by zero.
        return [(neighbor, 1.0 / len(scored)) for neighbor, _ in scored]

    return [(neighbor, weight / total_weight) for neighbor, weight in scored]


@dataclass(frozen=True, slots=True)
class _CellCoefficients:
    """Per-cell, per-hour update coefficients, computed once per
    forecast() call rather than once per simulated hour: this model
    version holds each cell's weather constant for the whole horizon
    (see DeterministicH3DispersionModel's docstring), so removal,
    transport, and the downwind neighbor split don't change hour to
    hour. A future per-hour weather feed would move this computation
    inside the simulation loop instead.
    """

    removal_fraction: float
    transport_fraction: float
    downwind_weights: list[tuple[str, float]]
    has_weather: bool


class DeterministicH3DispersionModel:
    """A simple wind-advection + limited-neighbor-diffusion + decay box
    model over the H3 grid. Deterministic and explainable by design — NOT
    an atmospheric chemistry simulator: no vertical layers, no chemistry,
    no emissions term (it redistributes and removes pollution that
    already exists in `current_state`; it does not model new sources).

    Each cell is a well-mixed box. Every hour, a cell's mass is (1)
    reduced by a removal fraction (baseline decay, increased by
    precipitation if available), then (2) split between what stays in
    the cell and what's transported to its immediate H3 neighbors,
    weighted toward whichever neighbor(s) lie closest to the wind's
    downwind bearing (see _downwind_weights). Confidence is propagated
    through the same transport as a mass-weighted average, then
    discounted by a flat per-hour factor and (if applicable) a
    missing-weather penalty.

    Every coefficient (removal_fraction, transport_fraction, each
    neighbor weight) is clamped into [0, 1], and transport_fraction is
    additionally capped below 1 by max_transport_fraction — so the
    per-hour update is a substochastic linear map: total mass across the
    modeled domain can only decrease (removal) or leave through an open
    domain boundary (see ForecastResult.domain_outflow_by_hour), never
    increase. Concentrations are floored at 0 defensively, though the
    formula cannot structurally go negative given non-negative inputs.

    Weather is held constant across the whole simulated horizon (there is
    no per-hour weather forecast feed yet) — the single biggest
    simplification versus reality, documented here rather than hidden.
    """

    def __init__(
        self,
        *,
        decay_rate_per_hour: float,
        wet_removal_rate_per_hour: float,
        precipitation_reference_mm: float,
        max_transport_fraction: float,
        wind_transport_reference_ms: float,
        calm_wind_threshold_ms: float,
        wind_cone_half_angle_deg: float,
        confidence_decay_per_hour: float,
        missing_weather_confidence_penalty: float,
    ) -> None:
        if not 0 <= decay_rate_per_hour <= 1:
            raise ValueError(f"decay_rate_per_hour must be within [0, 1]: {decay_rate_per_hour}")
        if not 0 <= wet_removal_rate_per_hour <= 1:
            raise ValueError(
                f"wet_removal_rate_per_hour must be within [0, 1]: {wet_removal_rate_per_hour}"
            )
        if precipitation_reference_mm <= 0:
            raise ValueError(
                f"precipitation_reference_mm must be > 0: {precipitation_reference_mm}"
            )
        if not 0 < max_transport_fraction < 1:
            raise ValueError(
                f"max_transport_fraction must be within (0, 1): {max_transport_fraction}"
            )
        if wind_transport_reference_ms <= 0:
            raise ValueError(
                f"wind_transport_reference_ms must be > 0: {wind_transport_reference_ms}"
            )
        if calm_wind_threshold_ms < 0:
            raise ValueError(f"calm_wind_threshold_ms must be >= 0: {calm_wind_threshold_ms}")
        if not 0 < wind_cone_half_angle_deg <= 180:
            raise ValueError(
                f"wind_cone_half_angle_deg must be within (0, 180]: {wind_cone_half_angle_deg}"
            )
        if not 0 < confidence_decay_per_hour <= 1:
            raise ValueError(
                f"confidence_decay_per_hour must be within (0, 1]: {confidence_decay_per_hour}"
            )
        if not 0 < missing_weather_confidence_penalty <= 1:
            raise ValueError(
                "missing_weather_confidence_penalty must be within (0, 1]: "
                f"{missing_weather_confidence_penalty}"
            )

        self._decay_rate_per_hour = decay_rate_per_hour
        self._wet_removal_rate_per_hour = wet_removal_rate_per_hour
        self._precipitation_reference_mm = precipitation_reference_mm
        self._max_transport_fraction = max_transport_fraction
        self._wind_transport_reference_ms = wind_transport_reference_ms
        self._calm_wind_threshold_ms = calm_wind_threshold_ms
        self._wind_cone_half_angle_deg = wind_cone_half_angle_deg
        self._confidence_decay_per_hour = confidence_decay_per_hour
        self._missing_weather_confidence_penalty = missing_weather_confidence_penalty

    def forecast(
        self,
        current_state: list[GridState],
        weather: list[WeatherReading],
        hours: Sequence[int] = (1, 3, 6),
        *,
        generated_at: datetime,
    ) -> ForecastResult:
        if not current_state:
            return ForecastResult(
                generated_at=generated_at, forecasts=[], domain_outflow_by_hour={}
            )

        horizons = sorted(set(hours))
        if not horizons or horizons[0] <= 0:
            raise ValueError(f"hours must be a non-empty sequence of positive integers: {hours}")

        cells = [state.h3_cell for state in current_state]
        if len(set(cells)) != len(cells):
            raise ValueError("current_state must not contain duplicate h3_cell entries")
        domain = set(cells)

        weather_by_cell = {reading.h3_cell: reading for reading in weather}

        pm25 = {state.h3_cell: (state.pm25 or 0.0) for state in current_state}
        unestimated = {state.h3_cell for state in current_state if state.pm25 is None}
        confidence = {state.h3_cell: state.confidence for state in current_state}
        coefficients = {
            cell: self._coefficients_for(cell, weather_by_cell.get(cell)) for cell in cells
        }

        forecasts: list[Forecast] = []
        domain_outflow_by_hour: dict[int, float] = {}
        max_horizon = horizons[-1]

        for hour in range(1, max_horizon + 1):
            pm25, confidence, outflow = self._step(cells, domain, pm25, confidence, coefficients)
            domain_outflow_by_hour[hour] = outflow

            if hour in horizons:
                forecast_time = generated_at + timedelta(hours=hour)
                # A cell that started with no estimate and still holds exactly
                # 0.0 has received no inflow from any cell with evidence: its
                # value is the `or 0.0` placeholder above, not a prediction,
                # and publishing it would show "clean air" where the truth is
                # "unknown". (A real 0.0 estimate is still published.)
                forecasts.extend(
                    Forecast(
                        h3_cell=cell,
                        generated_at=generated_at,
                        forecast_time=forecast_time,
                        forecast_hours=hour,
                        predicted_pm25=pm25[cell],
                        confidence=confidence[cell],
                    )
                    for cell in cells
                    if not (cell in unestimated and pm25[cell] == 0.0)
                )

        return ForecastResult(
            generated_at=generated_at,
            forecasts=forecasts,
            domain_outflow_by_hour=domain_outflow_by_hour,
        )

    def _coefficients_for(self, cell: str, reading: WeatherReading | None) -> _CellCoefficients:
        if reading is None:
            # No weather for this cell: decay-only, no fabricated wind.
            return _CellCoefficients(
                removal_fraction=self._decay_rate_per_hour,
                transport_fraction=0.0,
                downwind_weights=[],
                has_weather=False,
            )

        precip_factor = _clamp01(reading.precipitation / self._precipitation_reference_mm)
        removal_fraction = _clamp(
            self._decay_rate_per_hour + self._wet_removal_rate_per_hour * precip_factor, 0.0, 1.0
        )

        if reading.wind_speed < self._calm_wind_threshold_ms:
            return _CellCoefficients(
                removal_fraction=removal_fraction,
                transport_fraction=0.0,
                downwind_weights=[],
                has_weather=True,
            )

        transport_fraction = self._max_transport_fraction * _clamp01(
            reading.wind_speed / self._wind_transport_reference_ms
        )
        center = Coordinate(*cell_center(cell))
        neighbor_bearings = [
            (neighbor, center.bearing_to(Coordinate(*cell_center(neighbor))))
            for neighbor in grid_disk(cell, 1)
            if neighbor != cell
        ]
        downwind_weights = _downwind_weights(
            neighbor_bearings, reading.wind_direction, half_angle_deg=self._wind_cone_half_angle_deg
        )
        if not downwind_weights:
            # No neighbors at all (not reachable via h3, which always
            # returns at least 5 — defensive only): nowhere to send
            # transported mass, so don't transport any.
            transport_fraction = 0.0

        return _CellCoefficients(
            removal_fraction=removal_fraction,
            transport_fraction=transport_fraction,
            downwind_weights=downwind_weights,
            has_weather=True,
        )

    def _step(
        self,
        cells: list[str],
        domain: set[str],
        pm25: dict[str, float],
        confidence: dict[str, float],
        coefficients: dict[str, _CellCoefficients],
    ) -> tuple[dict[str, float], dict[str, float], float]:
        new_mass = dict.fromkeys(cells, 0.0)
        confidence_numerator = dict.fromkeys(cells, 0.0)
        confidence_denominator = dict.fromkeys(cells, 0.0)
        outflow = 0.0

        for cell in cells:
            coeff = coefficients[cell]
            remaining = pm25[cell] * (1.0 - coeff.removal_fraction)
            retained = remaining * (1.0 - coeff.transport_fraction)
            transported_out = remaining * coeff.transport_fraction

            new_mass[cell] += retained
            confidence_numerator[cell] += retained * confidence[cell]
            confidence_denominator[cell] += retained

            if transported_out <= 0.0:
                continue
            for neighbor, weight in coeff.downwind_weights:
                share = transported_out * weight
                if neighbor in domain:
                    new_mass[neighbor] += share
                    confidence_numerator[neighbor] += share * confidence[cell]
                    confidence_denominator[neighbor] += share
                else:
                    outflow += share

        new_confidence: dict[str, float] = {}
        for cell in cells:
            denominator = confidence_denominator[cell]
            blended = confidence_numerator[cell] / denominator if denominator > 0 else 0.0
            penalty = (
                1.0 if coefficients[cell].has_weather else self._missing_weather_confidence_penalty
            )
            new_confidence[cell] = _clamp01(blended * self._confidence_decay_per_hour * penalty)

        new_pm25 = {cell: max(0.0, new_mass[cell]) for cell in cells}
        return new_pm25, new_confidence, outflow
