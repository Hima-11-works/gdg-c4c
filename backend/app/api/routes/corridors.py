"""Routes for named corridor / interstate pollution events and their evidence.

Reads a published v2 run over a named corridor (see app.domain.corridor) and
returns the event plus its evaluation against withheld station observations —
or, when those observations do not exist, the exact data gap. The route never
returns a synthetic metric as real accuracy: that decision is made in
app.services.corridor_evaluation and surfaced verbatim here.

See docs/api/corridor-evaluation.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import Envelope
from app.api.schemas_corridor import (
    CorridorEventOut,
    CorridorEvaluationOut,
    CorridorOut,
    HorizonPointOut,
    ScoreSliceOut,
)
from app.core.config import Settings, get_settings
from app.domain.corridor import (
    Corridor,
    CorridorEvaluation,
    EvaluationVerdict,
    get_corridor,
    list_corridors,
)
from app.services.corridor_evaluation import (
    DEFAULT_MIN_LABELS,
    EventNotFoundError,
    corridor_cells,
    evaluate_event,
    event_for_run,
    event_peak,
    evidence_sample,
)

router = APIRouter(prefix="/corridors", tags=["corridors"])


def _corridor_out(corridor: Corridor) -> CorridorOut:
    cells = corridor_cells(corridor)
    return CorridorOut(
        corridor_id=corridor.corridor_id,
        name=corridor.name,
        kind=corridor.kind.value,
        region=corridor.region,
        geometry_source=corridor.geometry_source.value,
        geometry_note=corridor.geometry_note,
        geometry_description=corridor.describe_geometry(),
        h3_resolution=corridor.h3_resolution,
        cell_count=len(cells),
        endpoints=[
            {"label": label, "latitude": latitude, "longitude": longitude}
            for label, latitude, longitude in corridor.endpoints
        ],
        notes=corridor.notes,
    )


def _event_out(corridor: Corridor, evaluation: CorridorEvaluation, peak) -> CorridorEventOut:
    event = evaluation.event
    return CorridorEventOut(
        event_id=event.event_id,
        corridor_id=event.corridor_id,
        corridor_name=corridor.name,
        run_id=event.run_id,
        run_mode=event.run_mode,
        run_synthetic=event.run_synthetic,
        issued_at=event.issued_at,
        horizons=[
            HorizonPointOut(
                horizon_hours=point.horizon_hours,
                issued_at=point.issued_at,
                valid_at=point.valid_at,
            )
            for point in event.horizons
        ],
        cell_count=len(event.cells),
        cells=list(event.cells),
        peak_predicted_ugm3=peak[0],
        peak_horizon_hours=peak[1],
        label_count=event.label_count,
        label_sources=list(event.label_sources),
    )


def _evaluation_out(evaluation: CorridorEvaluation) -> CorridorEvaluationOut:
    return CorridorEvaluationOut(
        verdict=evaluation.verdict.value,
        usable_as_real_world_evidence=evaluation.is_usable_as_real_world_evidence,
        label_provenance=evaluation.label_provenance,
        reasons=list(evaluation.reasons),
        min_labels=evaluation.min_labels,
        high_pollution_threshold_ugm3=evaluation.high_pollution_threshold_ugm3,
        coverage=evaluation.coverage.to_dict(),
        slices=[ScoreSliceOut(**item.to_dict()) for item in evaluation.slices],
        evidence=evidence_sample(evaluation),
    )


@router.get(
    "",
    response_model=Envelope[list[CorridorOut]],
    summary="List named corridor cases (with their geometry provenance)",
)
def list_corridors_route() -> Envelope[list[CorridorOut]]:
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=False,
        data=[_corridor_out(item) for item in list_corridors()],
    )


@router.get(
    "/{corridor_id}",
    response_model=Envelope[CorridorOut],
    summary="One named corridor and the cells that stand in for it",
)
def get_corridor_route(corridor_id: str) -> Envelope[CorridorOut]:
    corridor = get_corridor(corridor_id)
    if corridor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no corridor with id {corridor_id!r}",
        )
    return Envelope(
        generated_at=datetime.now(UTC), is_demo=False, data=_corridor_out(corridor)
    )


@router.get(
    "/{corridor_id}/events/{event_id}",
    response_model=Envelope[dict],
    summary="A corridor event and the evidence (or gap) behind it",
)
def get_corridor_event(
    corridor_id: str,
    event_id: str,
    run_id: str | None = Query(
        default=None, description="Published run to evaluate; the newest for the region by default."
    ),
    min_labels: int = Query(default=DEFAULT_MIN_LABELS, ge=1, le=1000),
    high_pollution_threshold_ugm3: float | None = Query(default=None, ge=0),
    require_unused_stations: bool = Query(
        default=False,
        description=(
            "Require stations that did not contribute to the estimate. Not available "
            "yet: always reported as a data gap rather than approximated."
        ),
    ),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Envelope[dict]:
    """The event, its horizons and source times, and its evaluation.

    `verdict` is `evaluated` or `insufficient_data`; only the former is usable as
    real-world accuracy evidence, and the response says which it is.
    """
    corridor = get_corridor(corridor_id)
    if corridor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no corridor with id {corridor_id!r}",
        )
    try:
        event, _run, results = event_for_run(
            session, corridor=corridor, run_id=run_id
        )
    except EventNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    if event.event_id != event_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"event {event_id!r} is not the event of run {event.run_id!r} for this "
                f"corridor (that one is {event.event_id!r})"
            ),
        )
    evaluation = evaluate_event(
        session,
        corridor=corridor,
        event=event,
        results=results,
        settings=settings,
        min_labels=min_labels,
        high_pollution_threshold_ugm3=high_pollution_threshold_ugm3,
        require_unused_stations=require_unused_stations,
    )
    return Envelope(
        generated_at=datetime.now(UTC),
        is_demo=evaluation.verdict is not EvaluationVerdict.EVALUATED,
        data={
            "event": _event_out(corridor, evaluation, event_peak(results)).model_dump(),
            "evaluation": _evaluation_out(evaluation).model_dump(),
        },
    )
