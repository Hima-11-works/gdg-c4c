"""Abstract interface (port) for pollution dispersion/forecast models.

DeterministicH3DispersionModel (app.services.dispersion) is the first
implementation: a simple wind-advection + limited-neighbor-diffusion +
decay box model over the H3 grid. It is NOT an atmospheric chemistry
simulator — see its own docstring for exactly what it does and does not
model. Nothing that calls a PollutionForecastModel (an API endpoint, a
future scheduled pipeline) needs to change if this is ever replaced by a
more sophisticated model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.types import Forecast, GridState, WeatherReading


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """The output of one PollutionForecastModel.forecast() call.

    `forecasts` holds one Forecast per (cell, requested horizon) pair —
    cells come from `current_state`, horizons from the `hours` argument
    passed to `forecast()`.

    `domain_outflow_by_hour` is a diagnostic, not a modeling input: the
    total PM2.5 that left the tracked grid at each simulated hour
    (keyed 1..max(hours)) because it was transported toward a neighbor
    cell not present in `current_state` (an open domain boundary — see
    the implementation). It exists so mass-conservation bookkeeping is
    observable and testable rather than an implicit, unverifiable detail.
    """

    generated_at: datetime
    forecasts: list[Forecast]
    domain_outflow_by_hour: dict[int, float]


class PollutionForecastModel(Protocol):
    def forecast(
        self,
        current_state: list[GridState],
        weather: list[WeatherReading],
        hours: Sequence[int] = (1, 3, 6),
        *,
        generated_at: datetime,
    ) -> ForecastResult:
        """Forecast PM2.5 forward from `current_state`, hour by hour, and
        return the state at each horizon in `hours`.

        The modeled domain is exactly the cells present in
        `current_state` — there is no separate grid parameter, since the
        cells we have a current estimate for *are* the cells being
        forecast. `weather` supplies wind/precipitation per cell where
        available (matched to `current_state` by h3_cell); a cell with no
        matching weather entry is forecast decay-only, at a reduced
        confidence, rather than by fabricating wind.

        `generated_at` is supplied by the caller, not read from a clock,
        so any implementation is a pure function of its inputs — the
        same call always produces the same result.

        Every horizon's output is the literal simulated state at that
        many hourly steps (e.g. the 3-hour result is the state after
        exactly 3 of the same per-hour updates used to reach 6 hours),
        never a shortcut/closed-form approximation, so requesting
        multiple horizons from one call is guaranteed internally
        consistent.

        Must never produce a negative predicted_pm25 or a confidence
        outside [0, 1] (enforced by Forecast's own validation regardless
        of the implementation), and must never let total system mass
        (summed across `current_state`'s cells) increase from one
        simulated hour to the next.
        """
        ...
