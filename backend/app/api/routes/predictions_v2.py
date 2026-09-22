"""Published, run-pinned environmental prediction reads for API v2."""

from __future__ import annotations

import math

import h3
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_bbox_query, get_prediction_query_service
from app.api.schemas_v2 import (
    AlertV2Out,
    CellDetailV2Out,
    CoverageOut,
    DatasetRefOut,
    ExposureOut,
    ForecastV2Out,
    GridCurrentV2Out,
    MetaV2Out,
    PredictionMetadataOut,
    QualityFlagsOut,
    StaticFeaturesV2Out,
    V2Envelope,
    WeatherV2Out,
)
from app.core.config import get_settings
from app.domain.features import DataMode, DatasetRef
from app.domain.h3_grid import assert_valid_cell
from app.domain.prediction import PredictionRun
from app.domain.types import BoundingBox
from app.services.prediction_queries import (
    DEFAULT_EXPOSURE_THRESHOLD_PM25,
    ExposureSummary,
    PredictionCellView,
    PredictionQueryService,
)
from app.services.grid_query import resolve_cells

router = APIRouter(prefix="/api/v2", tags=["environmental predictions v2"])
_RESOLUTION_QUERY = Query(None, ge=0, le=15)


def _run_or_404(service: PredictionQueryService, run_id: str | None) -> PredictionRun:
    try:
        return service.run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _target_cells(
    service: PredictionQueryService,
    run: PredictionRun,
    *,
    resolution: int | None,
    bbox: BoundingBox | None,
) -> tuple[int, list[str]]:
    display_resolution = (
        (resolution if resolution is not None else service.native_resolution)
        if bbox is not None
        else service.native_resolution
    )
    if display_resolution > service.native_resolution:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"resolution {display_resolution} exceeds native prediction resolution "
                f"{service.native_resolution}; upscaling is not supported"
            ),
        )
    try:
        cells = resolve_cells(display_resolution, bbox) if bbox is not None else service.target_cells(run, display_resolution)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return display_resolution, cells


def _refs(refs: tuple[DatasetRef, ...] | list[DatasetRef]) -> list[DatasetRefOut]:
    return [
        DatasetRefOut(
            dataset_id=ref.dataset_id,
            source=ref.source,
            product=ref.product,
            version=ref.version,
            kind=ref.kind,
            region=ref.region,
            attribution=ref.attribution,
            license=ref.license,
        )
        for ref in refs
    ]


def _quality(view: PredictionCellView) -> QualityFlagsOut:
    return QualityFlagsOut(
        coverage_fraction=view.quality.coverage_fraction,
        observed_station_count=view.quality.observed_station_count,
        max_observation_age_hours=view.quality.max_observation_age_hours,
        missing_fields=list(view.quality.missing_fields),
        warnings=list(view.quality.warnings),
    )


def _metadata(run: PredictionRun, view: PredictionCellView) -> PredictionMetadataOut:
    return PredictionMetadataOut(
        input_kind=view.input_kind,
        prediction_method=view.prediction_method,
        model_version=view.model_version,
        feature_schema_version=view.feature_schema_version,
        dataset_versions=_refs(view.dataset_refs),
        observed_at=None,
        issued_at=run.generated_at,
        valid_at=view.valid_at,
        synthetic=view.synthetic,
        quality=_quality(view),
    )


def _exposure(value: ExposureSummary) -> ExposureOut:
    return ExposureOut(
        population_weighted_pm25=value.population_weighted_pm25,
        residents_above_threshold=value.residents_above_threshold,
        threshold_pm25=value.threshold_pm25,
        covered_population=value.covered_population,
        unknown_population=value.unknown_population,
        population_dataset_version=value.population_dataset_version,
        scope=value.scope,
    )


def _coverage(run: PredictionRun, resolution: int, requested: int, views: list[PredictionCellView]) -> CoverageOut:
    returned = sum(view.pm25 is not None for view in views)
    fraction = (
        sum(view.spatial_coverage_fraction for view in views) / requested
        if requested
        else 0.0
    )
    return CoverageOut(
        region=run.region,
        resolution=resolution,
        requested_cells=requested,
        returned_cells=returned,
        covered_fraction=min(1.0, fraction),
        unsupported_cells=requested - returned,
    )


def _current(run: PredictionRun, view: PredictionCellView) -> GridCurrentV2Out:
    vector = view.feature_vector
    return GridCurrentV2Out(
        h3_cell=view.h3_cell,
        valid_at=view.valid_at,
        latitude=view.latitude,
        longitude=view.longitude,
        pm25=view.pm25,
        pdi=view.pdi,
        pdi_version="legacy-heuristic-v1" if view.pdi is not None else None,
        confidence=view.confidence,
        wind_speed_ms=vector.get("wind_speed_ms"),
        wind_direction_deg=vector.get("wind_direction_deg"),
        metadata=_metadata(run, view),
        exposure=_exposure(view.exposure),
    )


def _forecast(run: PredictionRun, view: PredictionCellView) -> ForecastV2Out | None:
    if view.pm25 is None:
        return None
    return ForecastV2Out(
        h3_cell=view.h3_cell,
        baseline_pm25=view.baseline_pm25,
        predicted_pm25=view.pm25,
        lower_pm25=view.lower_pm25,
        upper_pm25=view.upper_pm25,
        forecast_hours=view.horizon_hours,
        forecast_time=view.valid_at,
        generated_at=run.generated_at,
        confidence=view.confidence,
        metadata=_metadata(run, view),
        exposure=_exposure(view.exposure),
    )


def _weather(run: PredictionRun, view: PredictionCellView) -> WeatherV2Out:
    vector = view.feature_vector
    u = vector.get("wind_u_ms")
    v = vector.get("wind_v_ms")
    speed = vector.get("wind_speed_ms")
    direction = vector.get("wind_direction_deg")
    if u is not None and v is not None:
        speed = math.hypot(float(u), float(v))
        direction = math.degrees(math.atan2(-float(u), -float(v))) % 360
    return WeatherV2Out(
        h3_cell=view.h3_cell,
        latitude=view.latitude,
        longitude=view.longitude,
        issued_at=run.generated_at,
        valid_at=view.valid_at,
        wind_u_ms=u,
        wind_v_ms=v,
        wind_speed_ms=speed,
        wind_direction_deg=direction,
        precipitation_mm=vector.get("rain_1h_mm", vector.get("precipitation_mm")),
        boundary_layer_height_m=vector.get("boundary_layer_height_m"),
        temperature_c=vector.get("temperature_c"),
        relative_humidity_pct=vector.get("relative_humidity_pct"),
        input_kind=view.input_kind,
        dataset_versions=_refs(view.dataset_refs),
    )


def _static_features(view: PredictionCellView) -> StaticFeaturesV2Out:
    vector = view.feature_vector
    road_density = vector.get("road_length_km_per_km2")
    cell_area_km2 = h3.cell_area(view.h3_cell, unit="km^2")
    return StaticFeaturesV2Out(
        h3_cell=view.h3_cell,
        population_count=vector.get("population_count"),
        population_density_per_km2=vector.get("population_density_per_km2"),
        road_length_km_by_class={
            "all_classes": float(road_density) * cell_area_km2
        } if road_density is not None else {},
        major_road_distance_km=vector.get("major_road_distance_km"),
        built_up_fraction=vector.get("built_up_fraction"),
        vegetation_fraction=vector.get("vegetation_fraction"),
        bare_soil_fraction=vector.get("bare_soil_fraction"),
        industrial_fraction=vector.get("industrial_fraction"),
        dataset_versions=_refs(view.dataset_refs),
    )


@router.get("/grid/current", response_model=V2Envelope[list[GridCurrentV2Out]])
def get_current_grid_v2(
    resolution: int | None = _RESOLUTION_QUERY,
    bbox: BoundingBox | None = Depends(get_bbox_query),
    run_id: str | None = Query(None),
    threshold_pm25: float = Query(DEFAULT_EXPOSURE_THRESHOLD_PM25, ge=0),
    service: PredictionQueryService = Depends(get_prediction_query_service),
) -> V2Envelope[list[GridCurrentV2Out]]:
    run = _run_or_404(service, run_id)
    display_resolution, cells = _target_cells(service, run, resolution=resolution, bbox=bbox)
    views = service.aggregate(
        run,
        target_cells=cells,
        resolution=display_resolution,
        horizon=0,
        threshold_pm25=threshold_pm25,
    )
    return V2Envelope(
        generated_at=run.generated_at,
        run_id=run.run_id,
        mode=run.mode,
        is_demo=run.mode is DataMode.DEMO or any(view.synthetic for view in views),
        data=[_current(run, view) for view in views],
        attribution=_refs(run.dataset_refs),
        coverage=_coverage(run, display_resolution, len(cells), views),
    )


@router.get("/grid/forecast", response_model=V2Envelope[list[ForecastV2Out]])
def get_forecast_grid_v2(
    hours: float = Query(1, gt=0, le=6),
    resolution: int | None = _RESOLUTION_QUERY,
    bbox: BoundingBox | None = Depends(get_bbox_query),
    run_id: str | None = Query(None),
    threshold_pm25: float = Query(DEFAULT_EXPOSURE_THRESHOLD_PM25, ge=0),
    service: PredictionQueryService = Depends(get_prediction_query_service),
) -> V2Envelope[list[ForecastV2Out]]:
    run = _run_or_404(service, run_id)
    anchors = service.horizons(run)
    max_horizon = max(anchors, default=0.0)
    if abs(hours * 4 - round(hours * 4)) > 1e-8 or hours > max_horizon:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"forecast horizon must be a 15-minute step within the published run's "
                f"0–{max_horizon:g}h anchor window; anchors are {anchors}"
            ),
        )
    display_resolution, cells = _target_cells(service, run, resolution=resolution, bbox=bbox)
    views = service.aggregate(
        run,
        target_cells=cells,
        resolution=display_resolution,
        horizon=hours,
        threshold_pm25=threshold_pm25,
    )
    forecasts = [value for view in views if (value := _forecast(run, view)) is not None]
    return V2Envelope(
        generated_at=run.generated_at,
        run_id=run.run_id,
        mode=run.mode,
        is_demo=run.mode is DataMode.DEMO or any(view.synthetic for view in views),
        data=forecasts,
        attribution=_refs(run.dataset_refs),
        coverage=_coverage(run, display_resolution, len(cells), views),
    )


@router.get("/cells/{h3_cell}", response_model=V2Envelope[CellDetailV2Out])
def get_cell_v2(
    h3_cell: str,
    resolution: int | None = Query(None, ge=0, le=15),
    run_id: str | None = Query(None),
    threshold_pm25: float = Query(DEFAULT_EXPOSURE_THRESHOLD_PM25, ge=0),
    service: PredictionQueryService = Depends(get_prediction_query_service),
) -> V2Envelope[CellDetailV2Out]:
    display_resolution = resolution if resolution is not None else get_settings().h3_resolution
    try:
        assert_valid_cell(h3_cell, resolution=display_resolution)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    run = _run_or_404(service, run_id)
    if display_resolution > service.native_resolution:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="upscaling is not supported")
    try:
        current_view = service.detail(run, h3_cell, resolution=display_resolution, threshold_pm25=threshold_pm25)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if current_view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No data for cell {h3_cell!r}")
    forecasts = []
    for horizon in service.horizons(run):
        forecast_view = service.aggregate(
            run,
            target_cells=[h3_cell],
            resolution=display_resolution,
            horizon=horizon,
            threshold_pm25=threshold_pm25,
        )[0]
        forecast = _forecast(run, forecast_view)
        if forecast is not None:
            forecasts.append(forecast)
    data = CellDetailV2Out(
        h3_cell=h3_cell,
        current=_current(run, current_view) if current_view.pm25 is not None or current_view.supported_native_cells else None,
        forecasts=forecasts,
        weather=_weather(run, current_view),
        static_features=_static_features(current_view),
        exposure=_exposure(current_view.exposure),
        pdi_factors=None,
    )
    return V2Envelope(
        generated_at=run.generated_at,
        run_id=run.run_id,
        mode=run.mode,
        is_demo=run.mode is DataMode.DEMO or current_view.synthetic,
        data=data,
        attribution=_refs(run.dataset_refs),
        coverage=CoverageOut(
            region=run.region,
            resolution=display_resolution,
            requested_cells=1,
            returned_cells=1 if current_view.pm25 is not None else 0,
            covered_fraction=current_view.spatial_coverage_fraction,
            unsupported_cells=1 if current_view.pm25 is None else 0,
        ),
    )


@router.get("/weather", response_model=V2Envelope[list[WeatherV2Out]])
def get_weather_v2(
    resolution: int | None = _RESOLUTION_QUERY,
    bbox: BoundingBox | None = Depends(get_bbox_query),
    hours: float = Query(0, ge=0, le=6),
    run_id: str | None = Query(None),
    service: PredictionQueryService = Depends(get_prediction_query_service),
) -> V2Envelope[list[WeatherV2Out]]:
    run = _run_or_404(service, run_id)
    if hours not in ({0.0} | set(service.horizons(run))):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unsupported weather horizon")
    display_resolution, cells = _target_cells(service, run, resolution=resolution, bbox=bbox)
    views = service.aggregate(run, target_cells=cells, resolution=display_resolution, horizon=hours)
    return V2Envelope(
        generated_at=run.generated_at,
        run_id=run.run_id,
        mode=run.mode,
        is_demo=run.mode is DataMode.DEMO or any(view.synthetic for view in views),
        data=[_weather(run, view) for view in views],
        attribution=_refs(run.dataset_refs),
        coverage=_coverage(run, display_resolution, len(cells), views),
    )


@router.get("/alerts", response_model=V2Envelope[list[AlertV2Out]])
def get_alerts_v2(
    run_id: str | None = Query(None),
    service: PredictionQueryService = Depends(get_prediction_query_service),
) -> V2Envelope[list[AlertV2Out]]:
    run = _run_or_404(service, run_id)
    rows = service.results(
        run,
        cells=service.target_cells(run, service.native_resolution)
        if run.run_id.startswith("demo-fallback-")
        else None,
    )
    current_by_cell = {row.h3_cell: row for row in rows if row.horizon_hours == 0}
    alerts = []
    for row in rows:
        if row.horizon_hours <= 0 or row.predicted_pm25 is None or row.predicted_pm25 < 91:
            continue
        current = current_by_cell.get(row.h3_cell)
        severity = "critical" if row.predicted_pm25 >= 250 else "warning" if row.predicted_pm25 >= 91 else "watch"
        alerts.append(
            AlertV2Out(
                h3_cell=row.h3_cell,
                severity=severity,
                message=f"Published run forecasts PM2.5 at {row.predicted_pm25:.0f} µg/m³.",
                created_at=run.generated_at,
                current_pm25=current.predicted_pm25 if current else None,
                forecast_pm25=row.predicted_pm25,
                forecast_hours=row.horizon_hours,
                confidence=row.quality.coverage_fraction,
                forecast_time=row.valid_at,
            )
        )
    return V2Envelope(
        generated_at=run.generated_at,
        run_id=run.run_id,
        mode=run.mode,
        is_demo=run.mode is DataMode.DEMO or any(row.synthetic for row in rows),
        data=alerts,
        attribution=_refs(run.dataset_refs),
    )


@router.get("/exposure", response_model=V2Envelope[ExposureOut])
def get_exposure_v2(
    resolution: int | None = _RESOLUTION_QUERY,
    bbox: BoundingBox | None = Depends(get_bbox_query),
    hours: float = Query(0, ge=0, le=6),
    threshold_pm25: float = Query(DEFAULT_EXPOSURE_THRESHOLD_PM25, ge=0),
    run_id: str | None = Query(None),
    service: PredictionQueryService = Depends(get_prediction_query_service),
) -> V2Envelope[ExposureOut]:
    run = _run_or_404(service, run_id)
    anchors = service.horizons(run)
    max_horizon = max(anchors, default=0.0)
    if hours != 0 and (abs(hours * 4 - round(hours * 4)) > 1e-8 or hours > max_horizon):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="exposure horizon must be a 15-minute step inside the published anchor window",
        )
    display_resolution, cells = _target_cells(service, run, resolution=resolution, bbox=bbox)
    views = service.aggregate(
        run, target_cells=cells, resolution=display_resolution, horizon=hours, threshold_pm25=threshold_pm25
    )
    known = [view for view in views if view.exposure.residents_above_threshold is not None]
    covered_population = sum(view.exposure.covered_population for view in views)
    weighted = _weighted_population_mean(views)
    residents_over = sum(
        view.exposure.residents_above_threshold or 0 for view in known
    ) if known else None
    unknown_values = [view.exposure.unknown_population for view in views if view.exposure.unknown_population is not None]
    summary = ExposureOut(
        population_weighted_pm25=weighted,
        residents_above_threshold=residents_over,
        threshold_pm25=threshold_pm25,
        covered_population=covered_population,
        unknown_population=sum(unknown_values) if unknown_values else None,
        population_dataset_version=next(
            (view.exposure.population_dataset_version for view in views if view.exposure.population_dataset_version),
            None,
        ),
        scope=f"{run.region}; H3 resolution {display_resolution}; {len(cells)} non-overlapping cells; +{hours:g}h",
    )
    return V2Envelope(
        generated_at=run.generated_at,
        run_id=run.run_id,
        mode=run.mode,
        is_demo=run.mode is DataMode.DEMO or any(view.synthetic for view in views),
        data=summary,
        attribution=_refs(run.dataset_refs),
        coverage=_coverage(run, display_resolution, len(cells), views),
    )


def _weighted_population_mean(views: list[PredictionCellView]) -> float | None:
    populated = [
        (view.exposure.population_weighted_pm25, view.exposure.covered_population)
        for view in views
        if view.exposure.population_weighted_pm25 is not None and view.exposure.covered_population > 0
    ]
    total = sum(weight for _, weight in populated)
    return None if total == 0 else sum(value * weight for value, weight in populated) / total


@router.get("/meta", response_model=MetaV2Out)
def get_meta_v2(
    run_id: str | None = Query(None),
    service: PredictionQueryService = Depends(get_prediction_query_service),
) -> MetaV2Out:
    run = _run_or_404(service, run_id)
    return MetaV2Out(
        region=run.region,
        latest_run_id=run.run_id,
        generated_at=run.generated_at,
        native_resolution=service.native_resolution,
        supported_display_resolutions=sorted({3, 4, 5, service.native_resolution}),
        supported_horizons_hours=service.horizons(run),
        feature_schema_version=run.feature_schema_version,
        model_version=", ".join(run.model_versions) if run.model_versions else None,
        data_mode=run.mode,
    )
