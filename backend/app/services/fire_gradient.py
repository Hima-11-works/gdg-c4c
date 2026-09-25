"""Concrete FireGradientModel implementations.

app.domain.sources.FireGradientModel is the interface; this module holds
implementations of it, the same split as estimation (interface in
app.domain.estimation, implementation in app.services.estimation).
"""

from __future__ import annotations

from datetime import datetime

from app.domain.h3_grid import cell_center
from app.domain.report_lifecycle import is_model_qualified
from app.domain.types import Coordinate, FireKind, FireReport

# How strongly each kind of fire pushes the gradient. A triage weighting
# (like the alert thresholds or PDI's weights), not physics: an industrial
# fire plausibly emits more per hour than scattered crop stubble. Kept in
# code rather than Settings because a per-kind knob belongs in the model,
# while the *scale* knobs are configurable (see Settings's fire_* fields).
_KIND_WEIGHTS: dict[FireKind, float] = {
    FireKind.INDUSTRIAL_FIRE: 1.5,
    FireKind.BUILDING_FIRE: 1.2,
    FireKind.FOREST_FIRE: 1.0,
    FireKind.CROP_BURNING: 0.8,
    FireKind.OTHER: 0.6,
}


class PlumeFireGradientModel:
    """Turns **qualified** citizen fire reports into a per-cell extra-PM2.5 field.

    Each active report acts as a point source at its snapped location; a
    target cell receives

        strength x falloff(distance)

    summed over all reports, where

        strength = source_pm25 * (smoke_intensity / 5) * kind_weight
                   * age_decay
        age_decay = 0.5 ** (age_hours / decay_half_life_hours),
                    0 past max_age_hours
        falloff   = max(0, 1 - (distance_km / plume_radius_km) ** 2)

    The falloff is deliberately isotropic: wind-driven *transport* is the
    dispersion/forecast model's job (it reads the resulting grid_state);
    this model only sharpens the near-field gradient that IDW interpolation
    from a sparse sensor network cannot resolve.

    **Only corroborated reports contribute.** A report arrives as a `submitted`
    claim from an unauthenticated endpoint, and before F1 every stored report
    inside the age window moved modeled PM2.5 — one anonymous POST could move the
    air-quality model. `is_model_qualified` is the single predicate (see
    app.domain.report_lifecycle) and it is enforced *here*, where the field is
    actually changed, not only in the query that feeds it: a caller that passes an
    unqualified report still gets no contribution. The repository's
    `list_active_qualified` is the matching filter, so the ordinary path is also
    cheap. Anything that later creates an authority incident (F8) must use the
    same predicate, so "allowed to affect the model" and "allowed to raise an
    incident" cannot diverge.
    """

    def __init__(
        self,
        *,
        source_pm25_ugm3: float,
        plume_radius_km: float,
        decay_half_life_hours: float,
        max_age_hours: float,
    ) -> None:
        if source_pm25_ugm3 <= 0:
            raise ValueError(f"source_pm25_ugm3 must be > 0: {source_pm25_ugm3}")
        if plume_radius_km <= 0:
            raise ValueError(f"plume_radius_km must be > 0: {plume_radius_km}")
        if decay_half_life_hours <= 0:
            raise ValueError(f"decay_half_life_hours must be > 0: {decay_half_life_hours}")
        if max_age_hours <= 0:
            raise ValueError(f"max_age_hours must be > 0: {max_age_hours}")

        self._source_pm25_ugm3 = source_pm25_ugm3
        self._plume_radius_km = plume_radius_km
        self._decay_half_life_hours = decay_half_life_hours
        self._max_age_hours = max_age_hours

    def qualifying_reports(
        self, reports: list[FireReport], *, timestamp: datetime
    ) -> list[FireReport]:
        """The subset of `reports` allowed to alter the modeled field.

        Exposed so a caller can log or count what it is about to use, and so the
        gate is testable without running a whole grid computation.
        """
        return [
            report
            for report in reports
            if is_model_qualified(report.status, expires_at=report.expires_at, now=timestamp)
        ]

    def contributions(
        self,
        grid: list[str],
        reports: list[FireReport],
        *,
        timestamp: datetime,
    ) -> list[float]:
        if not reports:
            return [0.0] * len(grid)

        # A report older than max_age contributes nothing (and would decay to
        # zero anyway); dropping it keeps the per-target loop cheap. An
        # unqualified report is dropped here too — that is the guarantee: no
        # matter who calls this, only corroborated and unexpired reports count.
        sources = []
        for report in self.qualifying_reports(reports, timestamp=timestamp):
            age_hours = (timestamp - report.reported_at).total_seconds() / 3600.0
            if age_hours < 0 or age_hours > self._max_age_hours:
                continue
            strength = (
                self._source_pm25_ugm3
                * (report.smoke_intensity / 5)
                * _KIND_WEIGHTS[report.kind]
                * 0.5 ** (age_hours / self._decay_half_life_hours)
            )
            if strength > 0:
                sources.append((Coordinate(report.latitude, report.longitude), strength))

        if not sources:
            return [0.0] * len(grid)

        values: list[float] = []
        for cell in grid:
            latitude, longitude = cell_center(cell)
            center = Coordinate(latitude, longitude)
            total = 0.0
            for coordinate, strength in sources:
                distance_km = coordinate.distance_km(center)
                falloff = max(0.0, 1.0 - (distance_km / self._plume_radius_km) ** 2)
                if falloff > 0:
                    total += strength * falloff
            values.append(total)
        return values
