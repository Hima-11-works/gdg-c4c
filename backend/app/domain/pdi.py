"""Abstract interface (port) for the Pollution Development/Pressure Index.

PDI is a heuristic "pollution pressure index" used to rank and triage
cells at a glance. It is NOT a scientifically exact measurement of net
emissions, a modeled pollutant budget, or a regulatory index — it is a
configurable, weighted blend of normalized signals. Every place PDI is
surfaced (API docs, UI labels, code comments) must describe it that way;
see HeuristicPDIModel (app.services.pdi) for the v0 formula.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CellContext:
    """The inputs available for one H3 cell when computing PDI.

    `pm25` is the current PM2.5 estimate for the cell (e.g. from a
    PollutionEstimator / GridState.pm25) and is the only input guaranteed
    to be present in v0. `road_pressure`, `industrial_pressure`, and
    `vegetation_sink` are extension points: pre-normalized to [0, 1] by
    whatever produces them (there is no real data source for any of them
    yet), and None until one exists. A PDIModel must treat a None factor
    as "not available", never as zero pressure. `vegetation_sink` is a
    *sink* rather than a pressure — 1.0 means "strong pollution sink
    here" (dense vegetation), not "high pollution pressure" — see
    HeuristicPDIModel for how a negative weight turns that into a
    downward pull on the index rather than an upward one.
    """

    h3_cell: str
    pm25: float | None
    road_pressure: float | None = None
    industrial_pressure: float | None = None
    vegetation_sink: float | None = None
    # Citizen fire reports as a pre-normalized pressure factor (see
    # app.services.fire_gradient): None when no active report influences
    # this cell. A pressure, like road/industrial - not a sink.
    fire_pressure: float | None = None


@dataclass(frozen=True, slots=True)
class PDIResult:
    """The output of a PDIModel for one cell.

    `pdi` is None when no input factor was available at all (e.g. no
    PM2.5 estimate and no extension-point data) — a heuristic score with
    zero inputs would be fabricated, not computed, so this never defaults
    to 0. `factors` holds the *normalized* [0, 1] value of each factor
    that actually contributed, keyed by name (e.g. {"pm25": 0.81}) — not
    each factor's weighted contribution — so a caller/UI can show which
    signals drove the score.
    """

    h3_cell: str
    pdi: float | None
    factors: dict[str, float]


class PDIModel(Protocol):
    def calculate(self, cell_context: CellContext) -> PDIResult:
        """Compute a heuristic pollution pressure index for one cell.

        Must never fabricate a score: if `cell_context` carries no usable
        factor (or every available factor is configured with zero
        weight), the result's `pdi` is None, not an invented number.
        """
        ...
