"""Corridor / interstate pollution events, and honest scoring of their forecasts.

The synthetic scenario has always had "perfect" accuracy, because it was scored
against the numbers that generated it. This service does the opposite: it scores
a *published* forecast against real station observations that the forecaster
could not have seen, and when those observations do not exist it says exactly
which ones are missing instead of producing a number.

Three rules, enforced here rather than in the route or the CLI:

1. **Withheld means withheld.** A label is a station reading in the target
   window, which post-dates the forecast's issue time. In strict mode
   (`require_unused_stations`) a label must additionally come from a station
   that did not contribute to the cell's interpolated estimate, so it is a true
   input holdout and not only a time holdout.
2. **Real or nothing.** If the run is a demo/synthetic publication, or the label
   rows are synthetic, the result is `insufficient_data` with that as the
   reason. A synthetic metric is never returned as real accuracy.
3. **Counts travel with metrics.** Every metric slice carries its sample size, a
   slice below the minimum is marked insufficient, and the event as a whole is
   only `evaluated` when *every* requested (horizon x geography) slice is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import h3

from app.db.repositories import (
    SqlPredictionPublicationRepository,
    SqlSensorReadingRepository,
)
from app.domain.corridor import (
    Corridor,
    CorridorEvaluation,
    CorridorEvent,
    Coverage,
    EvaluationVerdict,
    HorizonPoint,
    Label,
    LabelWindow,
    ScoreSlice,
    make_event_id,
)
from app.domain.prediction import PredictionResult, PredictionRun
from app.domain.types import SensorReading

#: The window a station observation must fall in to score one horizon. A whole
#: hour centred on the valid time: narrow enough to score a specific horizon,
#: wide enough that an hourly station is not missed.
LABEL_WINDOW = timedelta(hours=1)

#: Below this many scored (cell, horizon) pairs a slice is not reported as a
#: metric. One point is not a measurement of accuracy.
DEFAULT_MIN_LABELS = 5


class CorridorNotFoundError(LookupError):
    """No corridor with the given id."""


class EventNotFoundError(LookupError):
    """No published run, or no such corridor event within it."""


def corridor_cells(corridor: Corridor) -> tuple[str, ...]:
    """The H3 cells that stand in for a corridor, in order from the first
    endpoint to the last.

    Derived from the corridor's straight-line axis (see `Corridor.geometry_note`),
    so the same corridor always yields the same cells.
    """
    (start_label, start_lat, start_lon) = corridor.endpoints[0]
    (end_label, end_lat, end_lon) = corridor.endpoints[-1]
    del start_label, end_label
    cells: list[str] = []
    for index in range(corridor.cell_count):
        fraction = index / (corridor.cell_count - 1)
        latitude = start_lat + (end_lat - start_lat) * fraction
        longitude = start_lon + (end_lon - start_lon) * fraction
        cell = h3.latlng_to_cell(latitude, longitude, corridor.h3_resolution)
        if cell not in cells:
            cells.append(cell)
    return tuple(cells)


def geography_segments(corridor: Corridor, cells: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Split the corridor into three named geography slices.

    Three is enough to say where along the corridor a forecast held up, without
    pretending three numbers per horizon is a statistically meaningful
    stratification.
    """
    if len(cells) < 3:
        middle = max(1, len(cells) // 2)
        return {f"{corridor.endpoints[0][0].lower()}_end": cells[:middle],
                f"{corridor.endpoints[-1][0].lower()}_end": cells[middle:]}
    third = len(cells) / 3
    return {
        f"{corridor.endpoints[0][0].lower()}_end": tuple(cells[: math.ceil(third)]),
        "midway": tuple(cells[math.ceil(third) : math.floor(2 * third) + 1]),
        f"{corridor.endpoints[-1][0].lower()}_end": tuple(cells[math.floor(2 * third) + 1 :]),
    }


def _synthetic_label_sources(settings) -> set[str]:
    """Label sources that cannot be real observations.

    Configured as a comma-separated list (`SYNTHETIC_LABEL_SOURCES`) so a
    deployment that has, say, a scenario-fed station can declare it. A label
    from one of these sources is excluded from scoring, which is what keeps a
    synthetic metric out of a real accuracy report.
    """
    raw = getattr(settings, "synthetic_label_sources", "") or ""
    return {
        value.strip().lower()
        for value in raw.split(",")
        if value.strip()
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


@dataclass(frozen=True, slots=True)
class _LabelIndex:
    """Station labels bucketed by (cell, hour), for fast horizon lookup."""

    by_cell: dict[str, list[tuple[datetime, float, str, str]]]

    def for_window(
        self, cell: str, window: LabelWindow
    ) -> list[tuple[datetime, float, str, str]]:
        return [
            item for item in self.by_cell.get(cell, ()) if window.contains(item[0])
        ]


def _build_event(
    *,
    corridor: Corridor,
    run: PredictionRun,
    results: list[PredictionResult],
    horizons: tuple[float, ...],
) -> CorridorEvent | None:
    """Assemble the event for one published run, or None when the run has no
    result at all in the corridor (there is no event to evaluate)."""
    by_horizon: dict[float, list[PredictionResult]] = {}
    for result in results:
        by_horizon.setdefault(result.horizon_hours, []).append(result)
    points: list[HorizonPoint] = []
    for horizon in horizons:
        rows = by_horizon.get(horizon)
        if not rows:
            continue
        valid_at = min(row.valid_at for row in rows)
        points.append(
            HorizonPoint(
                horizon_hours=horizon,
                issued_at=run.generated_at,
                valid_at=max(row.valid_at for row in rows if row.predicted_pm25 is not None)
                if any(row.predicted_pm25 is not None for row in rows)
                else valid_at,
            )
        )
    if not points:
        return None
    return CorridorEvent(
        event_id=make_event_id(
            corridor_id=corridor.corridor_id, run_id=run.run_id, horizons=horizons
        ),
        corridor_id=corridor.corridor_id,
        run_id=run.run_id,
        issued_at=run.generated_at,
        horizons=tuple(points),
        cells=corridor_cells(corridor),
        run_mode=run.mode.value,
        run_synthetic=any(row.synthetic for row in results),
    )


def _corridor_label(corridor: Corridor) -> str:
    """How to name a corridor inside a sentence.

    Corridor names are self-describing and most already end in "corridor"
    ("Delhi-Kanpur interstate corridor"), so appending the word again produced
    "... interstate corridor corridor" in the not-found message. Append it only
    when the name does not already say it.
    """
    name = corridor.name.strip()
    if "corridor" in name.lower():
        return name
    return f"{name} corridor"


def event_for_run(
    session,
    *,
    corridor: Corridor,
    run_id: str | None = None,
    horizons: tuple[float, ...] | None = None,
) -> tuple[CorridorEvent, PredictionRun, list[PredictionResult]]:
    """The corridor event for a published run, with its raw result rows."""
    repository = SqlPredictionPublicationRepository(session)
    run = (
        repository.get_run(run_id)
        if run_id is not None
        else repository.latest_run(region=corridor.region)
    )
    if run is None:
        raise EventNotFoundError(
            "no published prediction run"
            + (f" with id {run_id!r}" if run_id is not None else "")
            + "; corridor events are built from a published v2 run"
        )
    results = repository.list_results(run.run_id)
    cells = set(corridor_cells(corridor))
    corridor_results = [row for row in results if row.h3_cell in cells]
    if not corridor_results:
        raise EventNotFoundError(
            f"published run {run.run_id} has no results in {_corridor_label(corridor)}"
        )
    wanted = horizons or tuple(sorted({row.horizon_hours for row in corridor_results}))
    event = _build_event(corridor=corridor, run=run, results=corridor_results, horizons=wanted)
    if event is None:
        raise EventNotFoundError(
            f"published run {run.run_id} has no results at the requested horizons"
        )
    return event, run, corridor_results


def _labels_for(
    session,
    *,
    event: CorridorEvent,
    point: HorizonPoint,
    synthetic_sources: set[str],
) -> tuple[list[Label], int]:
    """Station observations in the horizon's target window.

    Returns the labels and the number of candidate readings considered (used for
    the "how many were available" part of the gap report).
    """
    window = LabelWindow(
        from_at=point.valid_at - LABEL_WINDOW, to_at=point.valid_at + LABEL_WINDOW
    )
    readings = SqlSensorReadingRepository(session).list_since(
        window.from_at, pollutant="pm25"
    )
    resolution = h3.get_resolution(event.cells[0]) if event.cells else 7
    labels: list[Label] = []
    considered = 0
    for reading in readings:
        if reading.measured_at > window.to_at:
            continue
        cell = h3.latlng_to_cell(reading.latitude, reading.longitude, resolution)
        if cell not in event.cells:
            continue
        considered += 1
        if reading.source.strip().lower() in synthetic_sources:
            # Counted as available, deliberately not used: this is the
            # synthetic-label case, and it must not become a metric.
            continue
        labels.append(
            Label(
                h3_cell=cell,
                measured_at=reading.measured_at,
                value=reading.value,
                source=reading.source,
                external_sensor_id=reading.external_sensor_id,
                synthetic=False,
            )
        )
    return labels, considered


def _slice_metrics(
    *,
    horizon_hours: float,
    geography: str,
    forecast_by_cell: dict[str, float],
    labels: list[Label],
    threshold: float,
    min_labels: int,
) -> ScoreSlice:
    """Error, bias, high-pollution recall, and coverage for one slice."""
    errors: list[float] = []
    observed_above = 0
    predicted_above = 0
    true_positive = 0
    predicted_positive = 0
    stations: set[tuple[str, str]] = set()
    for label in labels:
        forecast = forecast_by_cell.get(label.h3_cell)
        if forecast is None:
            continue
        errors.append(forecast - label.value)
        stations.add((label.source, label.external_sensor_id))
        if label.value >= threshold:
            observed_above += 1
            if forecast >= threshold:
                true_positive += 1
        if forecast >= threshold:
            predicted_positive += 1
    pairs = len(errors)
    if pairs == 0:
        return ScoreSlice(
            horizon_hours=horizon_hours,
            geography=geography,
            pairs=0,
            station_count=0,
            high_pollution_threshold_ugm3=threshold,
            sufficient=False,
            note="no station observation in this slice's target window",
        )
    if pairs < min_labels:
        return ScoreSlice(
            horizon_hours=horizon_hours,
            geography=geography,
            pairs=pairs,
            station_count=len(stations),
            high_pollution_threshold_ugm3=threshold,
            high_pollution_observed=observed_above,
            sufficient=False,
            note=f"{pairs} scored pair(s), below the {min_labels}-label minimum",
        )
    mean_error = sum(errors) / pairs
    mae = sum(abs(value) for value in errors) / pairs
    rmse = math.sqrt(sum(value * value for value in errors) / pairs)
    recall = (
        true_positive / observed_above if observed_above else None
    )
    precision = (
        true_positive / predicted_positive if predicted_positive else None
    )
    note = ""
    if observed_above == 0:
        # No positive labels: recall is undefined, and saying 0% would be a lie.
        note = (
            f"no observation reached {threshold:g} µg/m³ in this slice; recall and "
            "precision are undefined, not zero"
        )
    return ScoreSlice(
        horizon_hours=horizon_hours,
        geography=geography,
        pairs=pairs,
        station_count=len(stations),
        mae_ugm3=mae,
        rmse_ugm3=rmse,
        bias_ugm3=mean_error,
        high_pollution_threshold_ugm3=threshold,
        high_pollution_observed=observed_above,
        high_pollution_recall=recall,
        high_pollution_precision=precision,
        sufficient=True,
        note=note,
    )


def evaluate_event(
    session,
    *,
    corridor: Corridor,
    event: CorridorEvent,
    results: list[PredictionResult],
    settings,
    min_labels: int = DEFAULT_MIN_LABELS,
    high_pollution_threshold_ugm3: float | None = None,
    require_unused_stations: bool = False,
) -> CorridorEvaluation:
    """Score a corridor event against withheld station observations.

    Returns `insufficient_data` — never a number presented as accuracy — when the
    run is synthetic, the labels are synthetic, or any requested slice is below
    the minimum label count.
    """
    threshold = (
        high_pollution_threshold_ugm3
        if high_pollution_threshold_ugm3 is not None
        else float(getattr(settings, "alert_warning_threshold_ugm3", 90.0))
    )
    reasons: list[str] = []
    if event.run_synthetic or event.run_mode == "demo":
        reasons.append(
            f"the published run {event.run_id} is synthetic/demo "
            f"(mode={event.run_mode}); its error and recall are properties of the "
            "scenario, not real-world accuracy"
        )

    forecast_by_horizon: dict[float, dict[str, float]] = {}
    for row in results:
        if row.predicted_pm25 is None:
            continue
        forecast_by_horizon.setdefault(row.horizon_hours, {})[row.h3_cell] = row.predicted_pm25

    segments = geography_segments(corridor, event.cells)
    slices: list[ScoreSlice] = []
    all_labels: list[Label] = []
    considered_total = 0
    for point in event.horizons:
        labels, considered = _labels_for(
            session,
            event=event,
            point=point,
            synthetic_sources=_synthetic_label_sources(settings),
        )
        considered_total += considered
        all_labels.extend(labels)
        forecast = forecast_by_horizon.get(point.horizon_hours, {})
        slices.append(
            _slice_metrics(
                horizon_hours=point.horizon_hours,
                geography="corridor",
                forecast_by_cell=forecast,
                labels=labels,
                threshold=threshold,
                min_labels=min_labels,
            )
        )
        for name, cells in segments.items():
            cell_set = set(cells)
            slices.append(
                _slice_metrics(
                    horizon_hours=point.horizon_hours,
                    geography=name,
                    forecast_by_cell=forecast,
                    labels=[item for item in labels if item.h3_cell in cell_set],
                    threshold=threshold,
                    min_labels=min_labels,
                )
            )

    scored_cells = {item.h3_cell for item in all_labels}
    coverage = Coverage(
        corridor_cells=len(event.cells),
        cells_with_forecast=len(forecast_by_horizon.get(event.horizons[0].horizon_hours, {})),
        cells_with_labels=len(scored_cells),
        cells_scored=len(
            {
                item.h3_cell
                for point in event.horizons
                for item in all_labels
                if item.h3_cell in forecast_by_horizon.get(point.horizon_hours, {})
            }
        ),
        requested_horizons=len(event.horizons),
        horizons_scored=sum(1 for item in slices if item.sufficient),
        fraction_cells_scored=(
            len(scored_cells) / len(event.cells) if event.cells else None
        ),
        fraction_horizons_scored=(
            sum(1 for point in event.horizons if any(
                item.sufficient and item.horizon_hours == point.horizon_hours
                for item in slices
            ))
            / len(event.horizons)
            if event.horizons
            else None
        ),
    )

    if not all_labels:
        reasons.append(
            f"no pm2.5 station observation fell in the target window of any of the "
            f"{len(event.horizons)} horizon(s) for the {len(event.cells)} corridor cell(s) "
            f"({considered_total} candidate reading(s) checked)"
        )
    insufficient = [item for item in slices if not item.sufficient]
    if insufficient:
        first = insufficient[0]
        reasons.append(
            f"{len(insufficient)} of {len(slices)} horizon x geography slice(s) are below "
            f"the {min_labels}-label minimum (e.g. {first.geography} at "
            f"+{first.horizon_hours:g}h: {first.note})"
        )
    if require_unused_stations:
        reasons.append(
            "strict input-holdout mode was requested but is not available yet: a run "
            "does not record which stations contributed to which cell's interpolation, "
            "so a station cannot be proven to be outside the model's influence. Reported "
            "as a data gap rather than approximated"
        )

    verdict = (
        EvaluationVerdict.EVALUATED
        if not reasons and all(item.sufficient for item in slices)
        else EvaluationVerdict.INSUFFICIENT_DATA
    )
    event_with_labels = CorridorEvent(
        event_id=event.event_id,
        corridor_id=event.corridor_id,
        run_id=event.run_id,
        issued_at=event.issued_at,
        horizons=event.horizons,
        cells=event.cells,
        run_mode=event.run_mode,
        run_synthetic=event.run_synthetic,
        label_window=LabelWindow(
            from_at=min(point.valid_at for point in event.horizons) - LABEL_WINDOW,
            to_at=max(point.valid_at for point in event.horizons) + LABEL_WINDOW,
        ),
        label_sources=tuple(sorted({item.source for item in all_labels})),
        label_count=len(all_labels),
    )
    return CorridorEvaluation(
        event=event_with_labels,
        verdict=verdict,
        reasons=tuple(reasons),
        slices=tuple(
            item for item in slices if item.geography == "corridor" or item.sufficient
        ),
        coverage=coverage,
        label_provenance="observed" if all_labels else "none",
        min_labels=min_labels,
        high_pollution_threshold_ugm3=threshold,
    )


def evidence_sample(
    evaluation: CorridorEvaluation, *, limit: int = 5
) -> list[dict]:
    """A few example scored pairs, so the API can show what "evidence" means.

    Aggregates only; the full label set is not published here.
    """
    return [
        {
            "horizon_hours": slice_.horizon_hours,
            "geography": slice_.geography,
            "pairs": slice_.pairs,
            "station_count": slice_.station_count,
            "sufficient": slice_.sufficient,
            "note": slice_.note,
        }
        for slice_ in evaluation.slices[:limit]
    ]


def event_peak(results: list[PredictionResult]) -> tuple[float | None, float | None]:
    """(peak predicted PM2.5, the horizon it occurred at) across the corridor."""
    values = [
        (row.predicted_pm25, row.horizon_hours)
        for row in results
        if row.predicted_pm25 is not None
    ]
    if not values:
        return None, None
    return max(values, key=lambda item: item[0])


__all__ = [
    "CorridorNotFoundError",
    "DEFAULT_MIN_LABELS",
    "EventNotFoundError",
    "corridor_cells",
    "evaluate_event",
    "event_for_run",
    "event_peak",
    "evidence_sample",
    "geography_segments",
]
