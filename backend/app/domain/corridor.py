"""Named Indian corridor / interstate pollution-event cases, and how their
forecasts are scored against real stations.

A *corridor event* is a published forecast evaluated over a named geographic
corridor at a set of horizons. Two things this module is deliberately strict
about:

**Geometry is labelled.** The shipped corridor geometry is *illustrative*: a
coarse set of H3 cells along a straight line between two published city
coordinates. It is **not** a road route and is never presented as one. Every
`Corridor` carries `geometry_source="illustrative"` and a note saying what a
sourced route dataset (OSM/GraphHopper/Indian highways shapefile) would have to
replace. Nothing downstream may silently upgrade that label.

**Synthetic performance is never real accuracy.** `EvaluationVerdict` has an
`EVALUATED` value, and reaching it requires *observed* station labels. A
scenario run, a demo-fallback run, or labels whose source is synthetic produces
`INSUFFICIENT_DATA` with the reason spelled out — the result is a statement
about missing data, not a number to quote.

Metrics are reported per horizon and per geography slice, always with the sample
count that produced them, and a slice below the minimum label count is reported
as insufficient rather than as a number computed from a handful of points.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.types import _require_utc

# --- geography ------------------------------------------------------------


class GeometrySource(StrEnum):
    """Where a corridor's geometry came from.

    Only `ILLUSTRATIVE` ships. `ROUTED` exists so a future sourced route
    dataset can be introduced without changing the contract, and every consumer
    is expected to keep treating the two differently.
    """

    ILLUSTRATIVE = "illustrative"
    ROUTED = "routed"


class CorridorKind(StrEnum):
    INTERSTATE = "interstate"
    CORRIDOR = "corridor"


@dataclass(frozen=True, slots=True)
class Corridor:
    """A named corridor and the cells that stand in for it.

    `endpoints` are published city coordinates, not a route. `cell_count` is the
    number of H3 cells sampled along the straight line between them.
    """

    corridor_id: str
    name: str
    kind: CorridorKind
    region: str
    endpoints: tuple[tuple[str, float, float], ...]
    h3_resolution: int
    cell_count: int
    geometry_source: GeometrySource = GeometrySource.ILLUSTRATIVE
    geometry_note: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.corridor_id.strip() or not self.name.strip():
            raise ValueError("corridor_id and name must not be empty")
        if len(self.endpoints) < 2:
            raise ValueError("a corridor needs at least two endpoints")
        for label, latitude, longitude in self.endpoints:
            if not label.strip():
                raise ValueError("endpoint labels must not be empty")
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError(f"endpoint {label!r} coordinates are out of range")
        if not 0 <= self.h3_resolution <= 15:
            raise ValueError("h3_resolution must be within [0, 15]")
        if self.cell_count < 2:
            raise ValueError("cell_count must be at least 2")
        if self.geometry_source is GeometrySource.ILLUSTRATIVE and not self.geometry_note:
            # An illustrative geometry must say so in its own record.
            raise ValueError("illustrative geometry must carry an explanatory note")

    def describe_geometry(self) -> str:
        endpoints = ", ".join(
            f"{label} ({latitude:.4f}, {longitude:.4f})"
            for label, latitude, longitude in self.endpoints
        )
        return (
            f"{self.cell_count} H3 resolution-{self.h3_resolution} cells sampled along a "
            f"straight line between {endpoints}. {self.geometry_note}"
        )


# --- the shipped case -----------------------------------------------------

DELHI_KANPUR = Corridor(
    corridor_id="delhi-kanpur",
    name="Delhi–Kanpur interstate corridor",
    kind=CorridorKind.INTERSTATE,
    region="india",
    endpoints=(
        ("Delhi", 28.6139, 77.2090),
        ("Kanpur", 26.4499, 80.3319),
    ),
    h3_resolution=7,
    cell_count=24,
    geometry_source=GeometrySource.ILLUSTRATIVE,
    geometry_note=(
        "ILLUSTRATIVE geometry: a straight-line Delhi–Kanpur axis, not a road route. "
        "It must not be read as the NH-48/NH-44 alignment. Replacing it requires a "
        "sourced route dataset (OSM, GraphHopper, or the Indian highways shapefile), "
        "after which geometry_source becomes 'routed'."
    ),
    notes=(
        "The Indo-Gangetic plain stretch where stubble-burning smoke is transported "
        "north-east into the Delhi NCR airshed, and where a winter inversion can trap "
        "it — the case the project cares about."
    ),
)

DMIC_CORRIDOR = Corridor(
    corridor_id="dmic",
    name="Delhi–Mumbai Industrial Corridor (DMIC)",
    kind=CorridorKind.CORRIDOR,
    region="india",
    endpoints=(
        ("Dadri/Delhi", 28.5492, 77.5539),
        ("JNPT Mumbai", 18.9499, 72.9515),
    ),
    h3_resolution=7,
    cell_count=36,
    geometry_source=GeometrySource.ILLUSTRATIVE,
    geometry_note=(
        "ILLUSTRATIVE geometry: Delhi-Mumbai high-capacity industrial corridor axis "
        "linking northern logistics nodes with western container ports."
    ),
    notes=(
        "Key economic freight corridor connecting NCR, Rajasthan, Gujarat, and Maharashtra."
    ),
)

EDFC_CORRIDOR = Corridor(
    corridor_id="edfc",
    name="Eastern Dedicated Freight Corridor (EDFC)",
    kind=CorridorKind.CORRIDOR,
    region="india",
    endpoints=(
        ("Ludhiana (Sahnewal)", 30.8525, 75.9863),
        ("Dankuni Kolkata", 22.6865, 88.2985),
    ),
    h3_resolution=7,
    cell_count=42,
    geometry_source=GeometrySource.ILLUSTRATIVE,
    geometry_note=(
        "ILLUSTRATIVE geometry: Sahnewal (Ludhiana) to Dankuni (Kolkata) freight artery "
        "intersecting heavy coal, agricultural, and mineral transport zones."
    ),
    notes=(
        "Traverses Punjab, Haryana, UP, Bihar, Jharkhand, and West Bengal."
    ),
)

CORRIDORS: dict[str, Corridor] = {
    DELHI_KANPUR.corridor_id: DELHI_KANPUR,
    DMIC_CORRIDOR.corridor_id: DMIC_CORRIDOR,
    EDFC_CORRIDOR.corridor_id: EDFC_CORRIDOR,
}


def get_corridor(corridor_id: str) -> Corridor | None:
    return CORRIDORS.get(corridor_id.strip().lower())


def list_corridors() -> list[Corridor]:
    return [CORRIDORS[key] for key in sorted(CORRIDORS)]


# --- events ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HorizonPoint:
    """One forecast horizon of an event, with its issue and valid times."""

    horizon_hours: float
    issued_at: datetime
    valid_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.valid_at, "valid_at")
        if self.horizon_hours < 0:
            raise ValueError("horizon_hours must be >= 0")
        if self.valid_at < self.issued_at:
            raise ValueError("valid_at must not precede issued_at")


@dataclass(frozen=True, slots=True)
class LabelWindow:
    """The time window a label must fall in to score a horizon.

    A label is a *station observation* in the target hour, which by construction
    post-dates the forecast's issue time — the forecaster could not have seen it.
    """

    from_at: datetime
    to_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.from_at, "from_at")
        _require_utc(self.to_at, "to_at")
        if self.to_at < self.from_at:
            raise ValueError("to_at must not precede from_at")

    def contains(self, moment: datetime) -> bool:
        return self.from_at <= moment <= self.to_at


@dataclass(frozen=True, slots=True)
class CorridorEvent:
    """A published forecast over one corridor, with a stable identity.

    The id is a pure function of (corridor, published run, horizons), so the
    same evaluation of the same run always names the same event, and a different
    run or a different horizon set is a different event.
    """

    event_id: str
    corridor_id: str
    run_id: str
    issued_at: datetime
    horizons: tuple[HorizonPoint, ...]
    cells: tuple[str, ...]
    run_mode: str
    run_synthetic: bool
    label_window: LabelWindow | None = None
    label_sources: tuple[str, ...] = ()
    label_count: int = 0

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        _require_utc(self.issued_at, "issued_at")
        if not self.horizons:
            raise ValueError("an event needs at least one horizon")
        if self.label_window is not None:
            _require_utc(self.label_window.from_at, "label_window.from_at")

    @property
    def horizon_hours(self) -> tuple[float, ...]:
        return tuple(point.horizon_hours for point in self.horizons)

    def window_for(self, horizon_hours: float) -> LabelWindow | None:
        """The scoring window for one horizon, centred on its valid time."""
        if self.label_window is None:
            return None
        return self.label_window


def make_event_id(*, corridor_id: str, run_id: str, horizons: tuple[float, ...]) -> str:
    """A stable, readable event id.

    `corridor:<corridor_id>:<run_id>` with the horizons folded into a short
    digest, because the same run evaluated at a different horizon set is a
    different event and must not collide.
    """
    horizon_key = ",".join(f"{value:g}" for value in sorted(horizons))
    digest = hashlib.sha256(f"{corridor_id}|{run_id}|{horizon_key}".encode()).hexdigest()[:8]
    return f"corridor:{corridor_id}:{run_id}:h{digest}"


# --- evaluation -----------------------------------------------------------


class EvaluationVerdict(StrEnum):
    """What an evaluation is allowed to claim."""

    EVALUATED = "evaluated"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class ScoreSlice:
    """Metrics for one (horizon, geography) slice, always with its sample size."""

    horizon_hours: float
    geography: str
    pairs: int = 0
    station_count: int = 0
    mae_ugm3: float | None = None
    rmse_ugm3: float | None = None
    bias_ugm3: float | None = None
    high_pollution_threshold_ugm3: float = 0.0
    high_pollution_observed: int = 0
    high_pollution_recall: float | None = None
    high_pollution_precision: float | None = None
    sufficient: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "horizon_hours": self.horizon_hours,
            "geography": self.geography,
            "pairs": self.pairs,
            "station_count": self.station_count,
            "mae_ugm3": _round(self.mae_ugm3),
            "rmse_ugm3": _round(self.rmse_ugm3),
            "bias_ugm3": _round(self.bias_ugm3),
            "high_pollution_threshold_ugm3": self.high_pollution_threshold_ugm3,
            "high_pollution_observed": self.high_pollution_observed,
            "high_pollution_recall": _round(self.high_pollution_recall),
            "high_pollution_precision": _round(self.high_pollution_precision),
            "sufficient": self.sufficient,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class CorridorEvaluation:
    """The whole evaluation of one event, or the reason there isn't one."""

    event: CorridorEvent
    verdict: EvaluationVerdict
    reasons: tuple[str, ...] = ()
    slices: tuple[ScoreSlice, ...] = ()
    coverage: Coverage = field(default_factory=lambda: Coverage())
    label_provenance: str = "observed"
    min_labels: int = 1
    high_pollution_threshold_ugm3: float = 0.0

    def __post_init__(self) -> None:
        if self.verdict is EvaluationVerdict.EVALUATED:
            if not self.reasons:
                raise ValueError("an evaluated result must name the labels it used")
            if any(not item.sufficient for item in self.slices):
                raise ValueError(
                    "an evaluated result must not contain an insufficient slice"
                )
        elif not self.reasons:
            raise ValueError("an insufficient-data result must state the gap")

    @property
    def is_usable_as_real_world_evidence(self) -> bool:
        """Only an evaluated result with observed labels may be quoted as
        accuracy evidence."""
        return self.verdict is EvaluationVerdict.EVALUATED

    def slice_for(self, horizon_hours: float, geography: str) -> ScoreSlice | None:
        return next(
            (
                item
                for item in self.slices
                if item.horizon_hours == horizon_hours and item.geography == geography
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of the event could be scored at all."""

    corridor_cells: int = 0
    cells_with_forecast: int = 0
    cells_with_labels: int = 0
    cells_scored: int = 0
    requested_horizons: int = 0
    horizons_scored: int = 0
    fraction_cells_scored: float | None = None
    fraction_horizons_scored: float | None = None

    def to_dict(self) -> dict:
        return {
            "corridor_cells": self.corridor_cells,
            "cells_with_forecast": self.cells_with_forecast,
            "cells_with_labels": self.cells_with_labels,
            "cells_scored": self.cells_scored,
            "requested_horizons": self.requested_horizons,
            "horizons_scored": self.horizons_scored,
            "fraction_cells_scored": _round(self.fraction_cells_scored),
            "fraction_horizons_scored": _round(self.fraction_horizons_scored),
        }


@dataclass(frozen=True, slots=True)
class Label:
    """One withheld station observation used as a label."""

    h3_cell: str
    measured_at: datetime
    value: float
    source: str
    external_sensor_id: str
    synthetic: bool = False
    # True when the station did not contribute to this cell's estimate, i.e. a
    # strict input holdout rather than only a time holdout.
    outside_model_influence: bool = False


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def great_circle_km(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    """Great-circle distance, for the straight-line corridor sampling."""
    lat1, lon1 = math.radians(first[0]), math.radians(first[1])
    lat2, lon2 = math.radians(second[0]), math.radians(second[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))
