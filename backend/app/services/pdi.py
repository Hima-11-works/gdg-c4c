"""Concrete PDIModel implementations.

app.domain.pdi.PDIModel is the interface; this module holds
implementations of it, the same split as estimators (interface in
app.domain.estimation, implementation in app.services.estimation) and
providers (interface in app.domain.providers, implementations in
app.ingestion).
"""

from __future__ import annotations

from app.domain.pdi import CellContext, PDIResult

# PDI factor names, in the order factors are evaluated. Kept as a tuple
# (not re-derived from CellContext's fields) so the calculation order —
# and therefore dict iteration order in PDIResult.factors — is explicit
# and stable regardless of dataclass field order.
_PM25 = "pm25"
_ROAD_PRESSURE = "road_pressure"
_INDUSTRIAL_PRESSURE = "industrial_pressure"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class HeuristicPDIModel:
    """v0 pollution pressure index: a weighted blend of normalized
    signals, primarily the current PM2.5 estimate.

    This is a heuristic ranking/triage score, NOT a scientifically exact
    measurement of net emissions or a modeled pollutant budget — it must
    always be described that way wherever it's surfaced (API docs, UI
    labels).

    Only `pm25` has a real data source today. `road_pressure` and
    `industrial_pressure` are extension points already wired into the
    formula (see CellContext) so that adding a real data source later is
    a matter of populating those fields — no change to this class, and no
    change to whatever calls `calculate()`.

    Formula: each available factor is normalized to [0, 1] (`pm25` by
    dividing by `pm25_reference` and clamping; `road_pressure` /
    `industrial_pressure` are assumed already normalized by their
    producer, clamped defensively), then combined as a weight-normalized
    average using the *absolute* value of each configured weight:

        pdi = 100 * sum(normalized_i * weight_i) / sum(abs(weight_i))

    over only the factors present for a cell. With today's non-negative
    default weights this stays in [0, 100] and is dominated by whichever
    factor(s) are actually available (PM2.5 alone, in the MVP). A future
    negative-weighted "sink" factor (e.g. precipitation washout reducing
    pressure) would pull the result toward -100 without requiring a
    formula change — bounded because normalized_i is in [0, 1] and the
    denominator uses absolute weights.

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
    ) -> None:
        if pm25_reference <= 0:
            raise ValueError(f"pm25_reference must be > 0: {pm25_reference}")
        self._pm25_reference = pm25_reference
        self._weights = {
            _PM25: pm25_weight,
            _ROAD_PRESSURE: road_pressure_weight,
            _INDUSTRIAL_PRESSURE: industrial_pressure_weight,
        }

    def calculate(self, cell_context: CellContext) -> PDIResult:
        factors: dict[str, float] = {}
        if cell_context.pm25 is not None:
            factors[_PM25] = _clamp01(cell_context.pm25 / self._pm25_reference)
        if cell_context.road_pressure is not None:
            factors[_ROAD_PRESSURE] = _clamp01(cell_context.road_pressure)
        if cell_context.industrial_pressure is not None:
            factors[_INDUSTRIAL_PRESSURE] = _clamp01(cell_context.industrial_pressure)

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
