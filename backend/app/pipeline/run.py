"""The complete processing pipeline for the configured region:

    OpenAQ -> sensor ingestion -> H3 grid + PM2.5 interpolation -> PDI
    -> Open-Meteo -> weather ingestion -> dispersion model -> 1h/3h/6h
    forecasts -> alerts

Run with:

    python -m app.pipeline.run [--min-lat --min-lon --max-lat --max-lon]

Every stage below is a thin wrapper around a small orchestration service
that already has its own test file and can be constructed and run in
isolation with fakes (SensorIngestionService, WeatherIngestionService,
GridComputationService, ForecastingService, AlertGenerationService) —
this module only wires them together in order, against one shared
session/timestamp/bounding box, and prints a clear per-stage pass/fail
summary. It is not itself where any pollution/forecast/PDI logic lives.

Demo Mode (DEMO_MODE=true): sensor and weather ingestion use the fixed,
deterministic dataset (app.ingestion.demo) for OpenAQ/Open-Meteo — see
app.ingestion.factory. FIRMS is skipped in demo mode so live satellite data
cannot be mixed into synthetic runs; its features are marked unavailable.

Failure handling: a failed EXTERNAL data source (OpenAQ, Open-Meteo, or FIRMS) is
reported as a clear per-stage failure but does NOT abort the run. Every
downstream stage already has a well-defined, tested behavior for
missing/stale/absent upstream data — IDWPollutionEstimator's min_sensors
threshold (null pollution rather than a fabricated estimate),
DeterministicH3DispersionModel's missing-weather handling (decay-only,
not fabricated wind), ForecastingService's "no current GridState" check —
never fabricating a value and never crashing, so continuing with
whatever is already persisted is the correct, most useful behavior for
those two stages specifically. A failure that leaves nothing trustworthy
for ANY stage to work with (the database itself unreachable) is not
caught here: it raises and aborts the whole run, since no stage below
can produce a meaningful result without one.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.repositories import (
    SqlAlertRepository,
    SqlDatasetVersionRepository,
    SqlFireHotspotRepository,
    SqlFireReportRepository,
    SqlForecastRepository,
    SqlGridStateRepository,
    SqlIngestionRunRepository,
    SqlModelVersionRepository,
    SqlPredictionPublicationRepository,
    SqlSensorReadingRepository,
    SqlTrafficObservationRepository,
    SqlWeatherReadingRepository,
)
from app.db.session import get_session_factory
from app.domain.features import DataMode, DatasetRef, FeatureSnapshot, InputKind, WeatherFeature
from app.domain.prediction import DEFAULT_REGION
from app.domain.types import BoundingBox, Forecast, WeatherReading
from app.ingestion.demo_reports import demo_fire_reports
from app.ingestion.factory import build_pollution_provider, build_weather_provider
from app.ingestion.firms import FirmsProvider
from app.ingestion.sentinel5p import CopernicusSentinel5PProvider, normalized_threshold
from app.services.alert_generation import AlertGenerationService
from app.services.dispersion import DeterministicH3DispersionModel
from app.services.environmental_ingestion import EnvironmentalIngestionService
from app.services.estimation import IDWPollutionEstimator
from app.services.features import FIRE_FEATURE_MAX_DISTANCE_KM, FeatureBuilder
from app.services.fire_gradient import PlumeFireGradientModel
from app.services.forecasting import ForecastingService
from app.services.geospatial import GeospatialService
from app.services.grid_computation import GridComputationService
from app.services.hotspot_detection import DetectorConfig, HotspotDetector, build_store
from app.services.ingestion import SensorIngestionService, WeatherIngestionService
from app.services.national_overview import build_national_overview
from app.services.pdi import HeuristicPDIModel
from app.services.prediction_publication import PredictionPublicationService
from app.services.reports import FireReportService, ReportRateLimitedError

logger = logging.getLogger(__name__)

#: The forecast horizons this run publishes, as quarter-hours from now out to
#: 6h. One definition, used by the forecasting stage, by the publication stage
#: (which has to write a row per cell per horizon) and by the coarse national
#: tier - a run whose two tiers advertised different horizons would break the
#: client's timeline, and the client's timeline is built from whatever the meta
#: advertises.
FORECAST_HORIZONS_HOURS: tuple[float, ...] = tuple(i * 0.25 for i in range(1, 25))


@dataclass(frozen=True)
class StageOutcome:
    name: str
    succeeded: bool
    summary: str


@dataclass(frozen=True)
class PipelineReport:
    stages: list[StageOutcome]

    @property
    def succeeded(self) -> bool:
        return all(stage.succeeded for stage in self.stages)


def _weather_features(
    readings: list[WeatherReading], *, fallback_time: datetime
) -> list[WeatherFeature]:
    """Stored weather readings -> the feature inputs the model consumes.

    `WeatherReading` stores speed and the meteorological direction the wind
    blows *from*; `WeatherFeature` wants the u/v components. Converting here
    rather than at write time keeps the stored reading faithful to what the
    provider actually said. The sign convention (negated, "from" not "to") is
    the one the demo scenario generator already uses, so demo and live agree.
    """
    return [
        WeatherFeature(
            h3_cell=reading.h3_cell,
            issued_at=reading.measured_at,
            valid_at=reading.measured_at,
            wind_u_ms=round(
                -reading.wind_speed * math.sin(math.radians(reading.wind_direction)), 4
            ),
            wind_v_ms=round(
                -reading.wind_speed * math.cos(math.radians(reading.wind_direction)), 4
            ),
            wind_speed_ms=round(reading.wind_speed, 4),
            wind_direction_deg=round(reading.wind_direction, 4),
            precipitation_mm=reading.precipitation,
            boundary_layer_height_m=reading.boundary_layer_height,
            temperature_c=reading.temperature,
            relative_humidity_pct=reading.humidity,
            input_kind=InputKind.OBSERVED,
        )
        for reading in readings
    ]


def _publish(
    session: Session,
    settings: Settings,
    pipeline_run_id: str,
    timestamp: datetime,
    forecasts: list[Forecast],
    *,
    fire_feed_available: bool = False,
    fire_dataset_id: str | None = None,
) -> StageOutcome:
    """Publish this run's grid as an immutable, queryable v2 result (F3).

    Everything above this stage wrote rows that nothing in the v2 read path
    looks at: the grid is computed, forecasts are generated, alerts are raised,
    and then the API serves a `demo-fallback-<hour>` run synthesised from those
    same rows, flagged as a fallback because no publication exists. So the
    pipeline ran and reported success while producing no publication at all -
    the exact failure mode F3 is about.

    This stage is what makes the run real. It is the only stage whose failure
    changes what every other stage meant: the grid, forecast and alerts above
    are all persisted and still useful, so a publication failure is reported as
    a failed stage and the rest of the report still prints. Letting it
    propagate would abandon the run's own contract - no stage failure aborts
    the process - and would discard a successful run's worth of work over the
    one step that only affects the v2 read path.
    """
    try:
        return _publish_inner(
            session,
            settings,
            pipeline_run_id,
            timestamp,
            forecasts,
            fire_feed_available=fire_feed_available,
            fire_dataset_id=fire_dataset_id,
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.exception("Publication stage failed for run %s", pipeline_run_id)
        return StageOutcome(
            "publication",
            False,
            f"could not publish run {pipeline_run_id}: {type(exc).__name__}: {exc}",
        )


def _horizon_snapshots(
    base: list[FeatureSnapshot],
    baselines: dict[tuple[str, float], float],
    *,
    issued_at: datetime,
) -> list[FeatureSnapshot]:
    """One snapshot per (cell, horizon) the forecasting stage produced.

    `publish()` derives a result row's horizon from its snapshot's
    `horizon_hours`, so a run only advertises the horizons it has snapshots
    for. The vector is the cell's issue-time vector reused verbatim: these rows
    describe a forecast, and the predicted number is supplied separately as a
    baseline, so nothing here claims to be a later observation.
    """
    horizons_by_cell: dict[str, list[float]] = {}
    for cell, horizon in baselines:
        horizons_by_cell.setdefault(cell, []).append(horizon)

    extra: list[FeatureSnapshot] = []
    for snapshot in base:
        for horizon in sorted(horizons_by_cell.get(snapshot.h3_cell, ())):
            extra.append(
                replace(
                    snapshot,
                    horizon_hours=horizon,
                    valid_at=issued_at + timedelta(hours=horizon),
                )
            )
    return extra


def _publish_inner(
    session: Session,
    settings: Settings,
    pipeline_run_id: str,
    timestamp: datetime,
    forecasts: list[Forecast],
    *,
    fire_feed_available: bool = False,
    fire_dataset_id: str | None = None,
) -> StageOutcome:
    states = SqlGridStateRepository(session).latest()
    if not states:
        # Not a crash: with no grid there is genuinely nothing to publish, and
        # saying so beats publishing an empty run that looks successful.
        return StageOutcome(
            "publication", False, "no grid state available, nothing to publish"
        )

    cells = [state.h3_cell for state in states]
    sensors = SqlSensorReadingRepository(session).list_since(
        timestamp - timedelta(hours=settings.ingest_max_reading_age_hours)
    )
    weather = SqlWeatherReadingRepository(session).list_latest_in_cells(cells)
    fire_detections, fire_dataset_refs = _fire_feature_inputs(
        session,
        settings,
        timestamp,
        _expand_bbox(bbox=_cells_bbox(cells), distance_km=FIRE_FEATURE_MAX_DISTANCE_KM),
        feed_available=fire_feed_available,
        dataset_id=fire_dataset_id,
    )

    snapshots = FeatureBuilder(resolution=settings.h3_resolution).build(
        cells=cells,
        issued_at=timestamp,
        valid_at=timestamp,
        sensor_readings=sensors,
        weather_features=_weather_features(weather, fallback_time=timestamp),
        fire_detections=fire_detections,
        dataset_refs=fire_dataset_refs,
    )
    if not snapshots:
        return StageOutcome(
            "publication", False, "feature builder produced no snapshots, nothing published"
        )

    # The coarse country-wide tier, published into the *same* run. A run may
    # hold cells at more than one resolution: the read path aggregates a run's
    # own cells upward and skips any finer than the request, so a coarse request
    # sees the nationwide cells and a request finer than the coarse resolution
    # sees only the fine grid. That is what makes "coarse everywhere, detailed
    # where measured" one product rather than two competing ones.
    overview = build_national_overview(
        settings, issued_at=timestamp, forecast_horizons=FORECAST_HORIZONS_HOURS
    )
    if overview.unavailable_reason is not None:
        # Reported, not swallowed: an operator seeing a blank country needs to
        # know whether that is "no data" or "not configured", and those need
        # different fixes.
        logger.info("national overview unavailable: %s", overview.unavailable_reason)

    # Deduped: the fine grid and the coarse tier could name the same cell if
    # the coarse resolution ever equalled the fine one, and publish() requires
    # unique cells per horizon.
    fine_cells = {snapshot.h3_cell for snapshot in snapshots}
    coarse_snapshots = [
        snapshot
        for snapshot in overview.snapshots
        if snapshot.h3_cell not in fine_cells
    ]
    all_snapshots = snapshots + coarse_snapshots

    # The forecast horizons. Without a snapshot per (cell, horizon) the run
    # advertises no horizons at all: the meta reports an empty
    # supported_horizons_hours, the client builds no timeline, and every frame
    # except "now" is empty. The predicted value travels as the publication
    # service's baseline map rather than inside `current_pm25`, so a forecast is
    # never relabelled as a measurement and the rows are marked
    # deterministic-dispersion-baseline.
    fine_baselines = {
        (forecast.h3_cell, forecast.forecast_hours): forecast.predicted_pm25
        for forecast in forecasts
    }
    baseline_by_cell_horizon: dict[tuple[str, float], float] = {
        **overview.forecasts,
        **fine_baselines,
    }
    all_snapshots = all_snapshots + _horizon_snapshots(
        snapshots, fine_baselines, issued_at=timestamp
    )

    run, results = PredictionPublicationService(
        SqlPredictionPublicationRepository(session),
        SqlModelVersionRepository(session),
    ).publish(
        run_id=pipeline_run_id,
        feature_run_id=f"{pipeline_run_id}-features",
        region=DEFAULT_REGION,
        mode=DataMode.DEMO if settings.demo_mode else DataMode.LIVE,
        generated_at=timestamp,
        snapshots=all_snapshots,
        baseline_by_cell_horizon=baseline_by_cell_horizon,
    )

    # Distinct cells, not snapshots: the coarse count includes one snapshot per
    # forecast horizon, so counting snapshots would report 25x the cells.
    coarse_cell_count = len({s.h3_cell for s in coarse_snapshots})
    horizons_published = sorted({h for _, h in baseline_by_cell_horizon})
    return StageOutcome(
        "publication",
        True,
        f"published run={run.run_id} fine_cells={len({s.h3_cell for s in snapshots})} "
        f"coarse_cells={coarse_cell_count} horizons={len(horizons_published)} "
        f"results={len(results)}"
        + (
            ""
            if overview.unavailable_reason is None
            else f" (coarse tier absent: {overview.unavailable_reason})"
        ),
    )


def _new_run_id(timestamp: datetime) -> str:
    """One run identity per pipeline execution (F3).

    Unique per *execution*, not per hour, and that is not a detail. A published
    run is immutable by design - see SqlPredictionPublicationRepository.publish,
    which refuses to re-publish an id with different content. An hour-derived id
    therefore collides the moment anyone retries (a re-run, a manual invocation,
    a CI re-run), because the retry recomputes the grid from fresher readings
    and the content differs; that collision aborted the entire pipeline
    mid-run. Each execution is a genuine separate observation, so each gets its
    own id and the read path serves the newest.

    The `run-<timestamp>-<suffix>` shape is deliberate: the timestamp prefix
    keeps ids sortable and recognisable in a log line and on the envelope, which
    matters when an operator is reading one off a dashboard.
    """
    return f"run-{timestamp:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


def _record_source_health(
    session: Session,
    *,
    pipeline_run_id: str,
    dataset_id: str,
    called: bool,
    failed: bool,
    item_count: int,
    stale: bool = False,
    error_summary: str | None = None,
) -> None:
    """Record one source's health for the run in progress (F3).

    Best-effort by design. Health is a diagnostic, and a run that produced good
    data must not be reported as failed because the diagnostic could not be
    written - so a failure here is logged at warning level and swallowed. The
    opposite mistake, which this exists to prevent, is the silent one: a source
    that returned nothing used to be indistinguishable from a source that was
    never asked.
    """
    from app.db.repositories.source_health import SqlSourceHealthRepository, classify

    try:
        SqlSourceHealthRepository(session).record(
            pipeline_run_id=pipeline_run_id,
            dataset_id=dataset_id,
            status=classify(
                item_count=item_count, failed=failed, called=called, stale=stale
            ),
            item_count=item_count,
            error_summary=error_summary,
            fetched_at=datetime.now(UTC),
        )
    except Exception as exc:  # noqa: BLE001 - a diagnostic must not fail a run
        logger.warning("could not record source health for %s: %s", dataset_id, exc)


async def _ingest_sensors(
    session: Session,
    settings: Settings,
    bbox: BoundingBox,
    since: datetime,
    pipeline_run_id: str,
) -> StageOutcome:
    async with httpx.AsyncClient(timeout=settings.openaq_timeout_seconds) as client:
        provider = build_pollution_provider(settings, client)
        if provider is None:
            message = "OPENAQ_API_KEY is not set — skipping sensor ingestion for this run"
            logger.warning(message)
            _record_source_health(
                session,
                pipeline_run_id=pipeline_run_id,
                dataset_id="openaq",
                called=False,
                failed=False,
                item_count=0,
                error_summary="not configured",
            )
            return StageOutcome("sensor_ingestion", False, message)

        result = await SensorIngestionService(provider, SqlSensorReadingRepository(session)).run(
            bbox, since=since
        )

    if not result.succeeded:
        message = f"OpenAQ ingestion failed: {'; '.join(result.errors)}"
        logger.error(message)
        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id="openaq",
            called=True,
            failed=True,
            item_count=0,
            error_summary="; ".join(result.errors)[:500],
        )
        return StageOutcome("sensor_ingestion", False, message)
    _record_source_health(
        session,
        pipeline_run_id=pipeline_run_id,
        dataset_id="openaq",
        called=True,
        failed=False,
        item_count=result.saved,
    )
    return StageOutcome(
        "sensor_ingestion",
        True,
        f"fetched={result.fetched} saved={result.saved} "
        f"skipped_duplicates={result.skipped_duplicates}",
    )


async def _ingest_fires(
    session: Session,
    settings: Settings,
    bbox: BoundingBox,
    pipeline_run_id: str,
) -> tuple[StageOutcome, bool, str | None]:
    """Refresh bounded FIRMS data for this run; baseline inference is optional.

    Returns whether this run has a complete feed that is safe to join into its
    feature snapshots. Demo runs never mix real detections into synthetic
    inputs, and an empty successful response remains distinct from a failure.
    """
    dataset_id = "nasa-firms"
    if settings.demo_mode or settings.firms_map_key is None:
        reason = "demo mode" if settings.demo_mode else "FIRMS_MAP_KEY not configured"
        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id=dataset_id,
            called=False,
            failed=False,
            item_count=0,
            error_summary=reason,
        )
        return (
            StageOutcome("fire_ingestion", True, f"skipped ({reason}); baseline continues"),
            False,
            None,
        )

    try:
        async with httpx.AsyncClient(timeout=settings.firms_timeout_seconds) as client:
            provider = FirmsProvider(
                client,
                map_key=settings.firms_map_key.get_secret_value(),
                source=settings.firms_source,
                base_url=settings.firms_base_url,
                timeout_seconds=settings.firms_timeout_seconds,
                max_retries=settings.firms_max_retries,
                h3_resolution=settings.h3_resolution,
                stale_after_hours=settings.firms_stale_after_hours,
            )
            service = EnvironmentalIngestionService(
                fire_provider=provider,
                fire_repository=SqlFireHotspotRepository(session),
                traffic_repository=SqlTrafficObservationRepository(session),
                dataset_repository=SqlDatasetVersionRepository(session),
                run_repository=SqlIngestionRunRepository(session),
            )
            result = await service.ingest_firms(
                _expand_bbox(bbox=bbox, distance_km=FIRE_FEATURE_MAX_DISTANCE_KM),
                day_range=2,
                region=settings.firms_region,
                source=settings.firms_source,
                stale_after_hours=settings.firms_stale_after_hours,
            )
    except Exception as exc:  # noqa: BLE001 - one upstream source must not abort baseline stages
        message = f"FIRMS ingestion setup failed: {type(exc).__name__}"
        logger.exception(message)
        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id=dataset_id,
            called=True,
            failed=True,
            item_count=0,
            error_summary=message,
        )
        return StageOutcome("fire_ingestion", False, message), False, None

    metrics = result.run.metrics
    fetched = int(metrics.get("fetched_records", 0))
    fresh = int(metrics.get("fresh_records", 0))
    succeeded = result.succeeded
    errors = "; ".join(result.run.errors)
    _record_source_health(
        session,
        pipeline_run_id=pipeline_run_id,
        dataset_id=dataset_id,
        called=True,
        failed=not succeeded,
        item_count=fetched,
        stale=fetched > 0 and fresh == 0,
        error_summary=errors[:500] if errors else None,
    )
    if not succeeded:
        return (
            StageOutcome(
                "fire_ingestion", False, f"FIRMS feed failed: {errors or 'incomplete'}"
            ),
            False,
            None,
        )
    feature_available = not (fetched > 0 and fresh == 0)
    summary = (
        f"fetched={fetched} saved={result.saved} duplicates={result.skipped_duplicates} "
        f"stale={metrics.get('stale_records', 0)}"
    )
    if not feature_available:
        summary += "; stale-only feed leaves fire features unavailable"
    return (
        StageOutcome("fire_ingestion", True, summary),
        feature_available,
        result.run.dataset_id if feature_available else None,
    )


def _sentinel5p_case_id(product_id: str) -> str:
    """Stable, safe case id so retries do not reprocess the same swath."""
    return f"live-s5p-{product_id.lower()}"


async def _scan_live_satellite_hotspots(
    session: Session,
    settings: Settings,
    bbox: BoundingBox,
    timestamp: datetime,
    pipeline_run_id: str,
) -> StageOutcome:
    """Fetch new Sentinel-5P NRT swaths and record QA-filtered candidates."""
    dataset_id = "copernicus-sentinel5p-aer-ai"
    if settings.demo_mode:
        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id=dataset_id,
            called=False,
            failed=False,
            item_count=0,
        )
        return StageOutcome(
            "satellite_hotspot_scanning",
            True,
            "skipped in demo mode; live satellite imagery is not mixed with scenario data",
        )
    if settings.cdse_refresh_token is None:
        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id=dataset_id,
            called=False,
            failed=False,
            item_count=0,
        )
        return StageOutcome(
            "satellite_hotspot_scanning", True, "skipped: CDSE_REFRESH_TOKEN is not configured"
        )

    store = build_store(settings, session=session)
    summaries = store.summaries()
    product_ids = {
        case_id.removeprefix("live-s5p-")
        for summary in summaries
        if isinstance(case_id := summary.get("case_id"), str)
        and case_id.startswith("live-s5p-")
    }

    now = max(timestamp, datetime.now(UTC))
    since = now - timedelta(hours=settings.hotspot_sentinel5p_max_age_hours)
    try:
        async with httpx.AsyncClient(
            timeout=settings.hotspot_sentinel5p_timeout_seconds
        ) as client:
            provider = CopernicusSentinel5PProvider(
                client,
                refresh_token=settings.cdse_refresh_token.get_secret_value(),
                timeout_seconds=settings.hotspot_sentinel5p_timeout_seconds,
                max_products=settings.hotspot_sentinel5p_max_products,
                max_tiles=settings.hotspot_max_tiles,
            )
            artifacts = await provider.fetch_new_artifacts(
                bbox=bbox,
                since=since,
                until=now,
                processed_ids=product_ids,
                h3_resolution=settings.hotspot_sentinel5p_h3_resolution,
                min_quality=settings.hotspot_sentinel5p_min_quality,
            )

        base_config = DetectorConfig.from_settings(settings)
        detector_config = replace(
            base_config,
            smoke_index_threshold=normalized_threshold(
                settings.hotspot_sentinel5p_uvai_threshold
            ),
            strong_index_threshold=normalized_threshold(
                settings.hotspot_sentinel5p_uvai_strong_threshold
            ),
            imagery_max_age_hours=settings.hotspot_sentinel5p_max_age_hours,
        )
        candidates = 0
        for artifact in artifacts:
            product_id = artifact.artifact_id.removeprefix("cdse-s5p:")
            case_id = _sentinel5p_case_id(product_id)
            evaluated_at = max(now, artifact.available_at)
            scan = HotspotDetector(detector_config).detect(
                case_id=case_id,
                case_title=artifact.product,
                h3_resolution=artifact.h3_resolution,
                evaluated_at=evaluated_at,
                imagery=artifact,
            )
            scan = replace(
                scan,
                config={
                    **scan.config,
                    "sentinel5p_uvai_threshold_raw": (
                        settings.hotspot_sentinel5p_uvai_threshold
                    ),
                    "sentinel5p_uvai_strong_threshold_raw": (
                        settings.hotspot_sentinel5p_uvai_strong_threshold
                    ),
                    "sentinel5p_quality_min_exclusive": (
                        settings.hotspot_sentinel5p_min_quality
                    ),
                    "sentinel5p_index_normalization": "clamp((raw_uvai + 1) / 6, 0, 1)",
                },
            )
            store.write(scan)
            candidates += len(scan.candidates)

        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id=dataset_id,
            called=True,
            failed=False,
            item_count=len(artifacts),
        )
        return StageOutcome(
            "satellite_hotspot_scanning",
            True,
            f"new_swaths={len(artifacts)} candidates={candidates} "
            f"(UVAI trigger={settings.hotspot_sentinel5p_uvai_threshold:g}, "
            f"quality>{settings.hotspot_sentinel5p_min_quality:g})",
        )
    except Exception as exc:
        message = f"CDSE Sentinel-5P scan failed: {exc}"
        logger.exception(message)
        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id=dataset_id,
            called=True,
            failed=True,
            item_count=0,
            error_summary=str(exc)[:500],
        )
        return StageOutcome("satellite_hotspot_scanning", False, message[:500])


def _expand_bbox(*, bbox: BoundingBox, distance_km: float) -> BoundingBox:
    """Add a bounded neighborhood so upwind sources outside the view are included."""
    latitude_margin = distance_km / 110.574
    middle_latitude = (bbox.min_lat + bbox.max_lat) / 2
    longitude_scale = max(0.15, abs(math.cos(math.radians(middle_latitude))))
    longitude_margin = distance_km / (111.320 * longitude_scale)
    return BoundingBox(
        min_lat=max(-90.0, bbox.min_lat - latitude_margin),
        min_lon=max(-180.0, bbox.min_lon - longitude_margin),
        max_lat=min(90.0, bbox.max_lat + latitude_margin),
        max_lon=min(180.0, bbox.max_lon + longitude_margin),
    )


def _cells_bbox(cells: list[str]) -> BoundingBox:
    """Return a tight coordinate box around persisted grid cell centers."""
    from app.domain.h3_grid import cell_center

    centers = [cell_center(cell) for cell in cells]
    min_lat = min(point[0] for point in centers)
    max_lat = max(point[0] for point in centers)
    min_lon = min(point[1] for point in centers)
    max_lon = max(point[1] for point in centers)
    if min_lat == max_lat:
        min_lat, max_lat = min_lat - 0.000001, max_lat + 0.000001
    if min_lon == max_lon:
        min_lon, max_lon = min_lon - 0.000001, max_lon + 0.000001
    return BoundingBox(
        min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon
    )


def _fire_feature_inputs(
    session: Session,
    settings: Settings,
    issued_at: datetime,
    bbox: BoundingBox,
    *,
    feed_available: bool,
    dataset_id: str | None,
) -> tuple[list[dict[str, object]] | None, tuple[DatasetRef, ...]]:
    """Load detections available at issue time and attach source provenance."""
    if not feed_available:
        return None, ()
    rows = SqlFireHotspotRepository(session).list_for_window(
        acquired_from=issued_at - timedelta(hours=settings.firms_stale_after_hours),
        acquired_to=issued_at,
        available_by=issued_at,
        bbox=bbox,
    )
    versions = SqlDatasetVersionRepository(session)
    refs: dict[str, DatasetRef] = {}
    if dataset_id is not None:
        version = versions.get(dataset_id)
        if version is not None:
            refs[dataset_id] = DatasetRef(
                dataset_id=version.dataset_id,
                source=version.source,
                product=version.product,
                version=version.version,
                kind=version.kind,
                region=version.region,
                attribution=version.attribution,
                license=version.license,
            )
    detections: list[dict[str, object]] = []
    for row in rows:
        detections.append(asdict(row))
        if row.dataset_id in refs:
            continue
        version = versions.get(row.dataset_id)
        if version is None:
            continue
        refs[row.dataset_id] = DatasetRef(
            dataset_id=version.dataset_id,
            source=version.source,
            product=version.product,
            version=version.version,
            kind=version.kind,
            region=version.region,
            attribution=version.attribution,
            license=version.license,
        )
    return detections, tuple(refs.values())


async def _ingest_weather(
    session: Session,
    settings: Settings,
    bbox: BoundingBox,
    pipeline_run_id: str,
) -> StageOutcome:
    async with httpx.AsyncClient(timeout=settings.open_meteo_timeout_seconds) as client:
        provider = build_weather_provider(settings, client)
        result = await WeatherIngestionService(provider, SqlWeatherReadingRepository(session)).run(
            bbox
        )

    if not result.succeeded:
        message = f"Open-Meteo ingestion failed: {'; '.join(result.errors)}"
        logger.error(message)
        _record_source_health(
            session,
            pipeline_run_id=pipeline_run_id,
            dataset_id="open-meteo",
            called=True,
            failed=True,
            item_count=0,
            error_summary="; ".join(result.errors)[:500],
        )
        return StageOutcome("weather_ingestion", False, message)
    _record_source_health(
        session,
        pipeline_run_id=pipeline_run_id,
        dataset_id="open-meteo",
        called=True,
        failed=False,
        item_count=result.saved,
    )
    return StageOutcome(
        "weather_ingestion",
        True,
        f"fetched={result.fetched} saved={result.saved} "
        f"skipped_duplicates={result.skipped_duplicates}",
    )


def _seed_fire_reports(session: Session, settings: Settings, timestamp: datetime) -> StageOutcome:
    """Demo Mode only: seed the fixed fire sightings so a demo run has a
    guaranteed fire-gradient effect and GET /api/v1/reports has content.

    Live mode seeds nothing - reports are citizen submissions, and the
    stage then just reports that fact. Seeding is idempotent *within* a
    run (the id is bucketed to the run's minute, so a retried run re-saves
    the same row); each new run stamps one fresh sighting per fire, and
    sightings older than FIRE_REPORT_MAX_AGE_HOURS age out of both the
    gradient model and the read side, so a long-lived demo database
    converges to the last ~12h of sightings rather than growing forever.
    """
    if not settings.demo_mode:
        return StageOutcome(
            "fire_reports", True, "live mode - citizen reports only, nothing seeded"
        )

    # F3. Seeding demo sightings must never be able to fail the *forecast*
    # run. It did: `service.submit` enforces F1's per-/24 submission cap, so on
    # a shared egress address - a CI runner, or a developer who has just been
    # clicking the form - the cap was already spent, the seed raised
    # ReportRateLimitedError, and the whole hourly pipeline aborted with
    # "Fatal: pipeline aborted". The grid, the forecast and the alerts were all
    # fine; the run died on two rows of demo data.
    #
    # So a refusal is now counted and reported rather than raised. Seeding is
    # cosmetic (it gives a demo run a visible plume), and a run that skipped it
    # is strictly better than a run that produced nothing. An unexpected error
    # is still caught for the same reason - but it is reported as skipped so it
    # is visible, rather than passing as success.
    service = FireReportService(SqlFireReportRepository(session))
    sightings = demo_fire_reports(reported_at=timestamp, resolution=settings.h3_resolution)
    bucket = timestamp.strftime("%Y%m%d%H%M")
    seeded = 0
    skipped = 0
    last_error: str | None = None
    for index, report in enumerate(sightings):
        try:
            service.submit(
                latitude=report.latitude,
                longitude=report.longitude,
                kind=report.kind,
                smoke_intensity=report.smoke_intensity,
                duration_hours=report.duration_hours,
                notes=report.notes,
                client_report_id=f"demo-fire-{index}-{bucket}",
                reported_at=report.reported_at,
            )
            seeded += 1
        except ReportRateLimitedError as exc:
            # Expected and benign: the cap is doing its job. Every remaining
            # sighting will hit the same wall, so stop asking.
            skipped += 1
            last_error = str(exc)
            break
        except Exception as exc:  # noqa: BLE001 - deliberately broad
            skipped += 1
            last_error = f"{type(exc).__name__}: {exc}"

    if skipped and last_error is not None:
        return StageOutcome(
            "fire_reports",
            True,
            f"seeded={seeded} skipped={skipped} of {len(sightings)} "
            f"(non-fatal: {last_error})",
        )
    return StageOutcome(
        "fire_reports",
        True,
        f"seeded={len(sightings)} (deterministic demo sightings)",
    )


def _compute_grid(
    session: Session, settings: Settings, bbox: BoundingBox, timestamp: datetime
) -> StageOutcome:
    estimator = IDWPollutionEstimator(
        max_distance_km=settings.idw_max_distance_km, min_sensors=settings.idw_min_sensors
    )
    pdi_model = HeuristicPDIModel(
        pm25_reference=settings.pdi_pm25_reference_ugm3,
        pm25_weight=settings.pdi_pm25_weight,
        road_pressure_weight=settings.pdi_road_pressure_weight,
        industrial_pressure_weight=settings.pdi_industrial_pressure_weight,
        vegetation_sink_weight=settings.pdi_vegetation_sink_weight,
        fire_pressure_weight=settings.pdi_fire_pressure_weight,
    )
    geospatial = GeospatialService(resolution=settings.h3_resolution)
    # Citizen fire reports act as modeled point sources, sharpening the
    # gradient near reported fires (see app.services.fire_gradient).
    fire_gradient = PlumeFireGradientModel(
        source_pm25_ugm3=settings.fire_source_pm25_ugm3,
        plume_radius_km=settings.fire_plume_radius_km,
        decay_half_life_hours=settings.fire_decay_half_life_hours,
        max_age_hours=settings.fire_report_max_age_hours,
    )
    service = GridComputationService(
        estimator,
        pdi_model,
        geospatial,
        SqlSensorReadingRepository(session),
        SqlGridStateRepository(session),
        fire_gradient=fire_gradient,
        fire_repository=SqlFireReportRepository(session),
        fire_pm25_cap_ugm3=settings.fire_pm25_cap_ugm3,
    )
    result = service.run(
        bbox,
        timestamp=timestamp,
        sensor_max_age=timedelta(hours=settings.ingest_max_reading_age_hours),
    )

    if not result.succeeded:
        message = f"Grid computation failed: {'; '.join(result.errors)}"
        logger.error(message)
        return StageOutcome("grid_computation", False, message)
    return StageOutcome(
        "grid_computation",
        True,
        f"cells={result.cells} sensors_used={result.sensors_used} saved={result.cells_saved}",
    )


def _forecast(
    session: Session, settings: Settings, timestamp: datetime
) -> tuple[StageOutcome, list[Forecast]]:
    model = DeterministicH3DispersionModel(
        decay_rate_per_hour=settings.dispersion_decay_rate_per_hour,
        wet_removal_rate_per_hour=settings.dispersion_wet_removal_rate_per_hour,
        precipitation_reference_mm=settings.dispersion_precipitation_reference_mm,
        max_transport_fraction=settings.dispersion_max_transport_fraction,
        wind_transport_reference_ms=settings.dispersion_wind_transport_reference_ms,
        calm_wind_threshold_ms=settings.dispersion_calm_wind_threshold_ms,
        wind_cone_half_angle_deg=settings.dispersion_wind_cone_half_angle_deg,
        confidence_decay_per_hour=settings.dispersion_confidence_decay_per_hour,
        missing_weather_confidence_penalty=settings.dispersion_missing_weather_confidence_penalty,
    )
    service = ForecastingService(
        model,
        SqlGridStateRepository(session),
        SqlWeatherReadingRepository(session),
        SqlForecastRepository(session),
    )
    result = service.run(
        generated_at=timestamp,
        hours=list(FORECAST_HORIZONS_HOURS),
        step_minutes=15,
    )

    if not result.succeeded:
        message = f"Forecasting failed: {'; '.join(result.errors)}"
        logger.error(message)
        return StageOutcome("forecasting", False, message), []
    return (
        StageOutcome(
            "forecasting",
            True,
            f"cells={result.cells} generated={result.forecasts_generated} "
            f"saved={result.forecasts_saved}",
        ),
        result.forecasts,
    )


def _generate_alerts(
    session: Session,
    settings: Settings,
    timestamp: datetime,
    forecasts: list[Forecast],
    pipeline_run_id: str,
) -> StageOutcome:
    # Read fresh rather than reusing GridComputationResult.states: this
    # way alerts always evaluate the same "current state" forecasting
    # just used, whether it came from this run's grid computation or
    # (if that stage failed) whatever was already persisted.
    current_state = SqlGridStateRepository(session).latest()
    service = AlertGenerationService(
        SqlAlertRepository(session),
        warning_threshold_ugm3=settings.alert_warning_threshold_ugm3,
        critical_threshold_ugm3=settings.alert_critical_threshold_ugm3,
        sharp_increase_threshold_ugm3=settings.alert_sharp_increase_threshold_ugm3,
        pdi_high_threshold=settings.alert_pdi_high_threshold,
        pdi_worsening_min_increase_ugm3=settings.alert_pdi_worsening_min_increase_ugm3,
        active_lookback=timedelta(hours=settings.alert_active_lookback_hours),
    )
    result = service.run(
        current_state, forecasts, generated_at=timestamp, run_id=pipeline_run_id
    )

    if not result.succeeded:
        message = f"Alert generation failed: {'; '.join(result.errors)}"
        logger.error(message)
        return StageOutcome("alert_generation", False, message)
    return StageOutcome(
        "alert_generation",
        True,
        f"cells_evaluated={result.cells_evaluated} alerts_created={result.alerts_created}",
    )


async def run_pipeline(bbox: BoundingBox, *, timestamp: datetime) -> PipelineReport:
    """Runs every stage once, in order, against one DB session. Never
    raises for a stage-level failure (see module docstring) — only an
    unreachable database propagates, since nothing below can do anything
    useful without one.
    """
    settings = get_settings()
    since = timestamp - timedelta(hours=settings.ingest_max_reading_age_hours)

    # F3: one run identity for everything this execution produces - source
    # health rows, the published prediction_run, and the alerts pinned to it.
    pipeline_run_id = _new_run_id(timestamp)
    logger.info("pipeline run id: %s", pipeline_run_id)

    session = get_session_factory()()
    try:
        sensor_outcome = await _ingest_sensors(
            session, settings, bbox, since, pipeline_run_id
        )
        weather_outcome = await _ingest_weather(session, settings, bbox, pipeline_run_id)
        fire_outcome, fire_feed_available, fire_dataset_id = await _ingest_fires(
            session, settings, bbox, pipeline_run_id
        )
        satellite_hotspot_outcome = await _scan_live_satellite_hotspots(
            session, settings, bbox, timestamp, pipeline_run_id
        )
        # FIRMS's available_at is the fetch completion time. Issue the forecast
        # after that time so only data already fetched can enter its features.
        issue_timestamp = (
            max(timestamp, datetime.now(UTC)) if fire_feed_available else timestamp
        )
        stages = [
            sensor_outcome,
            weather_outcome,
            fire_outcome,
            satellite_hotspot_outcome,
            _seed_fire_reports(session, settings, issue_timestamp),
            _compute_grid(session, settings, bbox, issue_timestamp),
        ]
        forecast_outcome, forecasts = _forecast(session, settings, issue_timestamp)
        stages.append(forecast_outcome)
        stages.append(
            _generate_alerts(
                session, settings, issue_timestamp, forecasts, pipeline_run_id
            )
        )
        # Publication comes last, deliberately: it publishes the grid and
        # forecast that the stages above just produced, so anything that failed
        # above is visible as a failed stage before its output is presented as a
        # published run.
        stages.append(
            _publish(
                session,
                settings,
                pipeline_run_id,
                issue_timestamp,
                forecasts,
                fire_feed_available=fire_feed_available,
                fire_dataset_id=fire_dataset_id,
            )
        )
    finally:
        session.close()

    return PipelineReport(stages=stages)


def _print_report(report: PipelineReport) -> None:
    for stage in report.stages:
        status = "OK  " if stage.succeeded else "FAIL"
        print(f"[{status}] {stage.name}: {stage.summary}")


async def _main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    bbox = BoundingBox(
        min_lat=args.min_lat if args.min_lat is not None else settings.ingest_bbox_min_lat,
        min_lon=args.min_lon if args.min_lon is not None else settings.ingest_bbox_min_lon,
        max_lat=args.max_lat if args.max_lat is not None else settings.ingest_bbox_max_lat,
        max_lon=args.max_lon if args.max_lon is not None else settings.ingest_bbox_max_lon,
    )
    timestamp = datetime.now(UTC)
    print(f"Running full pipeline for {bbox} at {timestamp.isoformat()}...")
    if settings.demo_mode:
        print("DEMO_MODE=true — ingestion stages use the fixed demo dataset (app.ingestion.demo).")

    try:
        report = await run_pipeline(bbox, timestamp=timestamp)
    except Exception as exc:
        # Unlike a per-stage failure (reported below and otherwise
        # non-fatal), this is the database itself being unreachable or
        # some other failure no stage can work around (see module
        # docstring) — print one clear line instead of a raw traceback.
        logger.exception("Pipeline run aborted by an unexpected error")
        print(f"Fatal: pipeline aborted: {exc!r}", file=sys.stderr)
        return 1

    _print_report(report)

    if not report.succeeded:
        print("One or more stages failed — see above.", file=sys.stderr)
    return 0 if report.succeeded else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.pipeline.run",
        description=(
            "Run the complete pipeline once: OpenAQ -> sensor ingestion -> H3/PM2.5 "
            "interpolation -> PDI -> Open-Meteo -> weather ingestion -> dispersion model "
            "-> 1h/3h/6h forecasts -> alerts."
        ),
    )
    parser.add_argument("--min-lat", type=float, default=None)
    parser.add_argument("--min-lon", type=float, default=None)
    parser.add_argument("--max-lat", type=float, default=None)
    parser.add_argument("--max-lon", type=float, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=get_settings().log_level)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
