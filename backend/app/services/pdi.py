"""Concrete PDIModel implementations.

app.domain.pdi.PDIModel is the interface; this module holds
implementations of it, the same split as estimators (interface in
app.domain.estimation, implementation in app.services.estimation) and
providers (interface in app.domain.providers, implementations in
app.ingestion).
"""

from __future__ import annotations

from app.domain.numeric import clamp01
from app.domain.pdi import CellContext, PDIResult

# PDI factor names, in the order factors are evaluated. Kept as a tuple
# (not re-derived from CellContext's fields) so the calculation order —
# and therefore dict iteration order in PDIResult.factors — is explicit
# and stable regardless of dataclass field order.
_PM25 = "pm25"
_ROAD_PRESSURE = "road_pressure"
_INDUSTRIAL_PRESSURE = "industrial_pressure"
_VEGETATION_SINK = "vegetation_sink"


class HeuristicPDIModel:
    """v0 pollution pressure index: a weighted blend of normalized
    signals, primarily the current PM2.5 estimate.

    This is a heuristic ranking/triage score, NOT a scientifically exact
    measurement of net emissions or a modeled pollutant budget — it must
    always be described that way wherever it's surfaced (API docs, UI
    labels).

    Only `pm25` has a real data source today. `road_pressure`,
    `industrial_pressure`, and `vegetation_sink` are extension points
    already wired into the formula (see CellContext) so that adding a
    real data source later is a matter of populating those fields — no
    change to this class, and no change to whatever calls `calculate()`.

    Formula: each available factor is normalized to [0, 1] (`pm25` by
    dividing by `pm25_reference` and clamping; `road_pressure` /
    `industrial_pressure` / `vegetation_sink` are assumed already
    normalized by their producer, clamped defensively), then combined as
    a weight-normalized average using the *absolute* value of each
    configured weight:

        pdi = 100 * sum(normalized_i * weight_i) / sum(abs(weight_i))

    over only the factors present for a cell. With today's non-negative
    pressure weights this stays in [0, 100] and is dominated by whichever
    factor(s) are actually available (PM2.5 alone, in the MVP — no real
    source exists yet for the other three). `vegetation_sink` is
    configured with a *negative* weight by default
    (`pdi_vegetation_sink_weight`): a high value there (dense vegetation)
    pulls the result toward -100 instead of up, since the numerator can
    go negative even though the denominator (sum of absolute weights)
    keeps the whole thing bounded in [-100, 100].

    A cell with no available factor (or where every available factor has
    zero configured weight) gets `pdi=None` — never a fabricated score.
    """

    def __init__(
        self,
        *,
        pm25_reference: float,
        pm25_weight: float,
        road_pressure_weight: float,
        industrial_pressure_weight: float,
        vegetation_sink_weight: float,
    ) -> None:
        if pm25_reference <= 0:
            raise ValueError(f"pm25_reference must be > 0: {pm25_reference}")
        self._pm25_reference = pm25_reference
        self._weights = {
            _PM25: pm25_weight,
            _ROAD_PRESSURE: road_pressure_weight,
            _INDUSTRIAL_PRESSURE: industrial_pressure_weight,
            _VEGETATION_SINK: vegetation_sink_weight,
        }

    def calculate(self, cell_context: CellContext) -> PDIResult:
        factors: dict[str, float] = {}
        if cell_context.pm25 is not None:
            factors[_PM25] = clamp01(cell_context.pm25 / self._pm25_reference)
        if cell_context.road_pressure is not None:
            factors[_ROAD_PRESSURE] = clamp01(cell_context.road_pressure)
        if cell_context.industrial_pressure is not None:
            factors[_INDUSTRIAL_PRESSURE] = clamp01(cell_context.industrial_pressure)
        if cell_context.vegetation_sink is not None:
            factors[_VEGETATION_SINK] = clamp01(cell_context.vegetation_sink)

        if not factors:
            return PDIResult(h3_cell=cell_context.h3_cell, pdi=None, factors={})

        weight_total = sum(abs(self._weights[name]) for name in factors)
        if weight_total == 0:
            # Every available factor is configured with zero weight: there is
            # no basis for a score, so don't invent one (e.g. as 0).
            return PDIResult(h3_cell=cell_context.h3_cell, pdi=None, factors=factors)

        weighted_sum = sum(factors[name] * self._weights[name] for name in factors)
        pdi = 100.0 * weighted_sum / weight_total
        return PDIResult(h3_cell=cell_context.h3_cell, pdi=pdi, factors=factors)
