"""Development CLI.

    python -m app.cli ingest [--min-lat --min-lon --max-lat --max-lon]
    python -m app.cli ingest-weather [--min-lat --min-lon --max-lat --max-lon]
    python -m app.cli export-grid [--out grid.geojson] [--min-lat ...]
    python -m app.cli forecast
    python -m app.cli demo-generate --profile tiny-ci --out demo.json
    python -m app.cli demo-replay --profile regional-demo --at 2025-01-15T12:00:00Z
    python -m app.cli verify-media-storage
    python -m app.cli hotspot-scan --fixture tests/fixtures/hotspots/positive_hotspot.json

Runs one ingestion pass against the bounding box from .env (overridable
per-call with the flags above) and prints a summary. This is a manual
trigger for local development — see docs/architecture.md for where a real
scheduler/worker will eventually call the same services. `export-grid`
writes the configured MVP region's H3 coverage as a GeoJSON
FeatureCollection, for visual inspection (e.g. geojson.io or a GIS tool).
`forecast` runs DeterministicH3DispersionModel against the latest
GridState/WeatherReading rows and persists the resulting 1h/3h/6h
Forecast rows (see app.services.forecasting.ForecastingService).

Not subject to the app/* layer-import rules in tests/test_architecture.py
(only directories under app/ are checked) — same treatment as app/main.py,
the other composition root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.repositories import (
    SqlDatasetVersionRepository,
    SqlFeatureSnapshotRepository,
    SqlFireHotspotRepository,
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
from app.domain.corridor import get_corridor, list_corridors
from app.domain.features import DataMode, WeatherFeature
from app.domain.hotspots import DETECTOR_VERSION, ScanVerdict
from app.domain.training import ModelStatus, ModelVersion
from app.domain.types import BoundingBox
from app.ingestion.demo_scenarios import ScenarioGenerator
from app.ingestion.factory import build_pollution_provider, build_weather_provider
from app.ingestion.firms import FirmsProvider
from app.services.corridor_evaluation import (
    DEFAULT_MIN_LABELS,
    EventNotFoundError,
    evaluate_event,
    event_for_run,
    event_peak,
)
from app.services.dispersion import DeterministicH3DispersionModel
from app.services.environmental_ingestion import EnvironmentalIngestionService
from app.services.features import (
    FeatureBuilder,
    feature_snapshot_from_dict,
    feature_snapshot_to_dict,
)
from app.services.federation import run_federation_demo
from app.services.forecasting import ForecastingResult, ForecastingService
from app.services.geospatial import GeospatialService
from app.services.hotspot_detection import (
    DetectorConfig,
    HotspotDetector,
    HotspotInputError,
    HotspotScanStore,
    load_case,
    run_case,
)
from app.services.ingestion import IngestionResult, SensorIngestionService, WeatherIngestionService
from app.services.media_storage import MediaStoreError, MediaStoreUnavailable, build_media_store
from app.services.model_operations import (
    activate_registry_model,
    assert_model_can_be_activated,
    model_from_registry_row,
    set_registry_model_status,
    summarize_model_monitoring,
    validate_candidate,
    write_registry_atomic,
)
from app.services.model_training import (
    evaluate_artifact,
    evaluate_incremental_feature_group,
    train_candidate,
)
from app.services.prediction_publication import (
    PredictionPublicationService,
    assert_live_snapshots_available,
)
from app.services.prediction_queries import (
    DEFAULT_EXPOSURE_THRESHOLD_PM25,
    PredictionQueryService,
)
from app.services.training_data import (
    export_training_dataset,
    generate_synthetic_training_dataset,
    parse_utc,
    read_records,
    write_json,
)

logger = logging.getLogger(__name__)


def _bbox_from_args(args: argparse.Namespace) -> BoundingBox:
    settings = get_settings()
    return BoundingBox(
        min_lat=args.min_lat if args.min_lat is not None else settings.ingest_bbox_min_lat,
        min_lon=args.min_lon if args.min_lon is not None else settings.ingest_bbox_min_lon,
        max_lat=args.max_lat if args.max_lat is not None else settings.ingest_bbox_max_lat,
        max_lon=args.max_lon if args.max_lon is not None else settings.ingest_bbox_max_lon,
    )


def _report(result: IngestionResult) -> int:
    if not result.succeeded:
        print(f"Ingestion failed: {'; '.join(result.errors)}", file=sys.stderr)
        return 1
    print(
        f"Ingestion complete: fetched={result.fetched} saved={result.saved} "
        f"skipped_duplicates={result.skipped_duplicates}"
    )
    return 0


async def _run_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    bbox = _bbox_from_args(args)
    since = datetime.now(UTC) - timedelta(hours=settings.ingest_max_reading_age_hours)
    print(f"Ingesting PM2.5 readings for {bbox} since {since.isoformat()}...")
    if settings.demo_mode:
        print("DEMO_MODE=true — using the fixed demo dataset, not OpenAQ.")

    session = get_session_factory()()
    try:
        async with httpx.AsyncClient(timeout=settings.openaq_timeout_seconds) as client:
            provider = build_pollution_provider(settings, client)
            if provider is None:
                print(
                    "OPENAQ_API_KEY is not set in .env - see .env.example.",
                    file=sys.stderr,
                )
                return 1
            service = SensorIngestionService(provider, SqlSensorReadingRepository(session))
            result = await service.run(bbox, since=since)
    finally:
        session.close()

    return _report(result)


async def _run_ingest_weather(args: argparse.Namespace) -> int:
    settings = get_settings()
    bbox = _bbox_from_args(args)
    print(
        f"Ingesting weather for {bbox} at resolution {settings.weather_h3_resolution} "
        f"(fanned out to grid resolution {settings.h3_resolution})..."
    )
    if settings.demo_mode:
        print("DEMO_MODE=true — using the fixed demo dataset, not Open-Meteo.")

    session = get_session_factory()()
    try:
        async with httpx.AsyncClient(timeout=settings.open_meteo_timeout_seconds) as client:
            provider = build_weather_provider(settings, client)
            service = WeatherIngestionService(provider, SqlWeatherReadingRepository(session))
            result = await service.run(bbox)
    finally:
        session.close()

    return _report(result)


async def _run_ingest_fires(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.firms_map_key is None:
        print(
            "FIRMS_MAP_KEY is not set; request a free NASA FIRMS MAP_KEY and configure it in .env.",
            file=sys.stderr,
        )
        return 1
    session = get_session_factory()()
    try:
        async with httpx.AsyncClient() as client:
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
                _bbox_from_args(args),
                day_range=args.days,
                region=args.region,
                source=settings.firms_source,
                stale_after_hours=settings.firms_stale_after_hours,
            )
    finally:
        session.close()
    print(
        "FIRMS ingestion "
        f"{'complete' if result.succeeded else 'failed'}: run={result.run.run_id} "
        f"fetched={result.run.metrics.get('fetched_records', 0)} saved={result.saved} "
        f"duplicates={result.skipped_duplicates} stale={result.run.metrics.get('stale_records', 0)} "
        f"complete_query={result.run.metrics.get('query_complete', False)}"
    )
    for error in result.run.errors:
        print(f"  {error}", file=sys.stderr)
    return 0 if result.succeeded else 1


async def _run_ingest_traffic(args: argparse.Namespace) -> int:
    settings = get_settings()
    payload = Path(args.input).read_text(encoding="utf-8")
    session = get_session_factory()()
    try:
        service = EnvironmentalIngestionService(
            fire_provider=None,  # This command imports only the supplied traffic feed.
            fire_repository=SqlFireHotspotRepository(session),
            traffic_repository=SqlTrafficObservationRepository(session),
            dataset_repository=SqlDatasetVersionRepository(session),
            run_repository=SqlIngestionRunRepository(session),
        )
        result = service.import_traffic_jsonl(
            payload,
            source=args.source,
            product=args.product,
            version=args.version,
            region=args.region,
            attribution=args.attribution,
            license=args.license,
            stale_after_hours=settings.traffic_stale_after_hours,
            h3_resolution=settings.h3_resolution,
        )
    finally:
        session.close()
    print(
        "Traffic import "
        f"{'complete' if result.succeeded else 'failed'}: run={result.run.run_id} "
        f"samples={result.run.metrics.get('sample_count', 0)} saved={result.saved} "
        f"duplicates={result.skipped_duplicates} "
        f"stale={result.run.metrics.get('stale_records', 0)} "
        f"coverage={result.run.metrics.get('mean_sampled_road_coverage_fraction', 'unknown')}"
    )
    for error in result.run.errors:
        print(f"  {error}", file=sys.stderr)
    return 0 if result.succeeded else 1


async def _run_export_grid(args: argparse.Namespace) -> int:
    settings = get_settings()
    bbox = _bbox_from_args(args)
    service = GeospatialService(resolution=settings.h3_resolution)
    feature_collection = service.region_geojson(bbox)

    output_path = Path(args.out)
    output_path.write_text(json.dumps(feature_collection, indent=2))
    print(
        f"Exported {len(feature_collection['features'])} H3 cell(s) at resolution "
        f"{settings.h3_resolution} for {bbox} to {output_path}"
    )
    return 0


def _report_forecast(result: ForecastingResult) -> int:
    if not result.succeeded:
        print(f"Forecasting failed: {'; '.join(result.errors)}", file=sys.stderr)
        return 1
    print(
        f"Forecasting complete: cells={result.cells} "
        f"generated={result.forecasts_generated} saved={result.forecasts_saved}"
    )
    if result.domain_outflow_by_hour:
        outflow = ", ".join(
            f"h{hour}={loss:.3f}" for hour, loss in sorted(result.domain_outflow_by_hour.items())
        )
        print(f"Domain boundary outflow (PM2.5 units left the modeled grid): {outflow}")
    return 0


async def _run_forecast(args: argparse.Namespace) -> int:
    settings = get_settings()
    session = get_session_factory()()
    try:
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
        result = service.run(generated_at=datetime.now(UTC))
    finally:
        session.close()

    return _report_forecast(result)


def _parse_utc_argument(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("time must be an RFC 3339 UTC timestamp, for example 2025-01-15T12:00:00Z")
    return parsed.astimezone(UTC)


def _parse_utc_date_boundary(value: str) -> datetime:
    """Accept a UTC date or timestamp for inclusive-start/exclusive-end filters."""

    if len(value) == 10:
        value = f"{value}T00:00:00Z"
    return parse_utc(value)


async def _run_training_export(args: argparse.Namespace) -> int:
    mode = DataMode(args.mode)
    records = read_records(Path(args.input))
    dataset = export_training_dataset(
        records,
        mode=mode,
        start=_parse_utc_date_boundary(args.start) if args.start else None,
        end=_parse_utc_date_boundary(args.end) if args.end else None,
    )
    changed = write_json(Path(args.out), dataset)
    print(
        f"Training export {'wrote' if changed else 'unchanged'}: mode={mode.value} "
        f"examples={dataset['example_count']} stations={dataset['station_count']} "
        f"path={args.out}"
    )
    return 0


async def _run_training_smoke_data(args: argparse.Namespace) -> int:
    dataset = generate_synthetic_training_dataset(
        hours=args.hours,
        station_count=args.stations,
        anchor_utc=_parse_utc_argument(args.anchor),
    )
    changed = write_json(Path(args.out), dataset)
    print(
        f"Synthetic training data {'wrote' if changed else 'unchanged'}: "
        f"examples={dataset['example_count']} stations={dataset['station_count']} "
        f"path={args.out}; not for scientific validation"
    )
    return 0


def _registry_rows(artifact: dict, artifact_path: Path) -> list[ModelVersion]:
    trained_at = datetime.now(UTC)
    versions = []
    for horizon, model in sorted(artifact["models"].items(), key=lambda item: float(item[0])):
        training_range = artifact["training_ranges_by_horizon"][horizon]
        versions.append(
            ModelVersion(
                model_id=f"{artifact['artifact_sha256'][:40]}-h{horizon.replace('.', '_')}",
                artifact_uri=str(artifact_path),
                artifact_sha256=artifact["artifact_sha256"],
                feature_schema_version=artifact["feature_schema_version"],
                feature_names=tuple(model["feature_names"]),
                trained_at=trained_at,
                training_start=parse_utc(training_range["start"]),
                training_end=parse_utc(training_range["end"]),
                region=artifact["region"],
                horizon_hours=float(horizon),
                metrics=artifact["evaluation"][horizon]["temporal"]["metrics"],
                synthetic_only=artifact["synthetic_only"],
                status=ModelStatus.CANDIDATE,
            )
        )
    return versions


async def _run_train(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(dataset, dict) or dataset.get("schema_version") != "training-dataset-v1":
        raise ValueError("--dataset must be a training-dataset-v1 manifest from training-export")
    artifact = train_candidate(
        dataset,
        ridge_alpha=args.ridge_alpha,
        allow_synthetic=args.allow_synthetic,
    )
    artifact_dir = Path(args.artifact_dir)
    artifact_path = artifact_dir / f"{artifact['artifact_sha256']}.json"
    if artifact_path.exists():
        existing_artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        evaluate_artifact(existing_artifact, dataset)
        if existing_artifact != artifact:
            raise ValueError("existing artifact at the content-addressed path is not identical")
    else:
        write_json(artifact_path, artifact)

    versions = _registry_rows(artifact, artifact_path)
    registry_path = Path(args.registry)
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(registry, dict) or not isinstance(registry.get("models"), list):
            raise ValueError("registry file must contain a models array")
    else:
        registry = {"schema_version": "model-registry-v1", "models": []}
    by_id = {row["model_id"]: row for row in registry["models"]}
    for version in versions:
        existing = by_id.get(version.model_id)
        if existing is not None:
            if existing.get("artifact_sha256") != version.artifact_sha256:
                raise ValueError(f"registry model id collision: {version.model_id}")
            continue
        by_id[version.model_id] = {
            "model_id": version.model_id,
            "artifact_uri": version.artifact_uri,
            "artifact_sha256": version.artifact_sha256,
            "feature_schema_version": version.feature_schema_version,
            "feature_names": list(version.feature_names),
            "trained_at": version.trained_at.isoformat().replace("+00:00", "Z"),
            "training_start": version.training_start.isoformat().replace("+00:00", "Z"),
            "training_end": version.training_end.isoformat().replace("+00:00", "Z"),
            "region": version.region,
            "horizon_hours": version.horizon_hours,
            "metrics": dict(version.metrics),
            "synthetic_only": version.synthetic_only,
            "status": version.status.value,
        }
    registry["models"] = [by_id[key] for key in sorted(by_id)]
    write_json(registry_path, registry)

    if args.register_db:
        session = get_session_factory()()
        try:
            repository = SqlModelVersionRepository(session)
            for version in versions:
                existing = repository.get(version.model_id)
                if existing is not None:
                    if existing.artifact_sha256 != version.artifact_sha256:
                        raise ValueError(f"registry model id collision: {version.model_id}")
                    continue
                repository.upsert(version)
        finally:
            session.close()
    print(
        f"Candidate {'synthetic-only ' if artifact['synthetic_only'] else ''}training complete: "
        f"sha256={artifact['artifact_sha256']} horizons={','.join(artifact['models'])} "
        f"artifact={artifact_path} registry={registry_path}"
    )
    return 0


async def _run_evaluate(args: argparse.Namespace) -> int:
    artifact = json.loads(Path(args.model).read_text(encoding="utf-8"))
    dataset = json.loads(Path(args.split).read_text(encoding="utf-8"))
    report = evaluate_artifact(artifact, dataset)
    changed = write_json(Path(args.out), report)
    print(
        f"Evaluation {'wrote' if changed else 'unchanged'}: sha256={report['artifact_sha256']} "
        f"horizons={len(report['horizons'])} path={args.out}"
    )
    return 0


async def _run_evaluate_feature_group(args: argparse.Namespace) -> int:
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    report = evaluate_incremental_feature_group(
        dataset,
        group=args.group,
        as_of_verified=args.as_of_verified,
        ridge_alpha=args.ridge_alpha,
    )
    changed = write_json(Path(args.out), report)
    improved = sum(
        bool(item.get("mae_improved"))
        for item in report["horizons"].values()
        if item.get("supported")
    )
    print(
        f"Incremental {args.group} evaluation {'wrote' if changed else 'unchanged'}: "
        f"horizons={len(report['horizons'])} MAE_improved={improved} "
        f"auto_promoted=false path={args.out}"
    )
    return 0


def _load_local_registry(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"model registry does not exist: {path}")
    registry = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(registry, dict)
        or registry.get("schema_version") != "model-registry-v1"
        or not isinstance(registry.get("models"), list)
    ):
        raise ValueError("registry file must contain a model-registry-v1 models array")
    return registry


def _read_incremental_reports(paths: list[str]) -> dict[str, dict]:
    reports = {}
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"incremental evaluation report must be an object: {path}")
        group = payload.get("feature_group")
        if group not in {"fires", "traffic"}:
            raise ValueError(f"incremental evaluation report has an unsupported group: {path}")
        if group in reports:
            raise ValueError(f"more than one incremental report supplied for {group}")
        reports[group] = payload
    return reports


async def _run_model_validate(args: argparse.Namespace) -> int:
    reports = _read_incremental_reports(args.incremental_report)
    if args.register_db:
        session = get_session_factory()()
        try:
            repository = SqlModelVersionRepository(session)
            model = repository.get(args.model_id)
            if model is None:
                raise ValueError(
                    f"model {args.model_id!r} does not exist in the database registry"
                )
            if model.status is ModelStatus.VALIDATED:
                print(f"Model already validated: model_id={model.model_id}")
                return 0
            artifact = json.loads(Path(model.artifact_uri).read_text(encoding="utf-8"))
            validated = validate_candidate(model, artifact, reports)
            repository.set_status(
                model.model_id, expected=model.status, status=validated.status
            )
        finally:
            session.close()
    else:
        path = Path(args.registry)
        registry = _load_local_registry(path)
        matches = [row for row in registry["models"] if row.get("model_id") == args.model_id]
        if len(matches) != 1:
            raise ValueError(f"model {args.model_id!r} must exist exactly once in the registry")
        model = model_from_registry_row(matches[0])
        if model.status is ModelStatus.VALIDATED:
            print(f"Model already validated: model_id={model.model_id}")
            return 0
        artifact = json.loads(Path(model.artifact_uri).read_text(encoding="utf-8"))
        validated = validate_candidate(model, artifact, reports)
        set_registry_model_status(registry, model.model_id, validated.status)
        write_registry_atomic(path, registry)
    print(f"Model validated for manual promotion: model_id={args.model_id}")
    return 0


async def _run_model_activate(args: argparse.Namespace, *, rollback: bool = False) -> int:
    if args.register_db:
        session = get_session_factory()()
        try:
            repository = SqlModelVersionRepository(session)
            model = repository.get(args.model_id)
            if model is None:
                raise ValueError(
                    f"model {args.model_id!r} does not exist in the database registry"
                )
            artifact = json.loads(Path(model.artifact_uri).read_text(encoding="utf-8"))
            expected = ModelStatus.RETIRED if rollback else ModelStatus.VALIDATED
            if model.status not in {expected, ModelStatus.PROMOTED}:
                raise ValueError(
                    f"{model.status.value} model cannot be "
                    f"{'rolled back to' if rollback else 'promoted'}"
                )
            assert_model_can_be_activated(model, artifact)
            repository.activate(model.model_id, allow_retired=rollback)
        finally:
            session.close()
    else:
        path = Path(args.registry)
        registry = _load_local_registry(path)
        activate_registry_model(registry, args.model_id, rollback=rollback)
        write_registry_atomic(path, registry)
    verb = "rollback complete" if rollback else "promotion complete"
    print(f"Model {verb}: model_id={args.model_id}; previous active version retired")
    return 0


async def _run_model_promote(args: argparse.Namespace) -> int:
    return await _run_model_activate(args)


async def _run_model_rollback(args: argparse.Namespace) -> int:
    return await _run_model_activate(args, rollback=True)


async def _run_model_monitor(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("monitor input must be a JSON object")
    report = summarize_model_monitoring(payload)
    changed = write_json(Path(args.out), report)
    metrics = report["metrics"]
    print(
        f"Model monitor {'wrote' if changed else 'unchanged'}: model_id={report['model_id']} "
        f"labels={report['coverage']['labelled_prediction_count']} "
        f"MAE={metrics['mae_ugm3']} drift_alerts={len(report['drift_alerts'])} "
        f"status={report['status']} auto_promoted=false path={args.out}"
    )
    return 0


async def _run_demo_snapshot(args: argparse.Namespace) -> int:
    generator = ScenarioGenerator.from_manifest(
        args.profile,
        manifest_path=Path(args.manifest) if args.manifest else None,
        scenario_id=args.scenario,
        seed=args.seed,
        anchor_utc=_parse_utc_argument(args.anchor) if args.anchor else None,
    )
    if args.at:
        snapshot = generator.generate_at(_parse_utc_argument(args.at))
    else:
        snapshot = generator.generate(args.replay_hour)
    output_path = Path(args.out)
    changed = snapshot.write_json(output_path)
    state = "wrote" if changed else "unchanged"
    print(
        f"Demo snapshot {state}: profile={snapshot.profile} scenario={snapshot.scenario_id} "
        f"replay_at={snapshot.replay_at.isoformat()} cells={len(snapshot.cells)} "
        f"stations={len(snapshot.sensor_readings)} checksum={snapshot.checksum} path={output_path}"
    )
    return 0


async def _run_demo_features(args: argparse.Namespace) -> int:
    if args.replay_hour < 0 or args.history_hours < 0:
        raise ValueError("replay-hour and history-hours must be >= 0")
    generator = ScenarioGenerator.from_manifest(
        args.profile,
        manifest_path=Path(args.manifest) if args.manifest else None,
        scenario_id=args.scenario,
        seed=args.seed,
        anchor_utc=_parse_utc_argument(args.anchor) if args.anchor else None,
    )
    target = generator.generate(args.replay_hour)
    history_start = max(0, args.replay_hour - args.history_hours)
    history = [generator.generate(hour) for hour in range(history_start, args.replay_hour + 1)]
    sensor_readings = [reading for snapshot in history for reading in snapshot.sensor_readings]
    weather_features = [sample for snapshot in history for sample in snapshot.weather]
    builder = FeatureBuilder(resolution=8)
    snapshots = []
    for horizon in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0):
        valid_at = target.replay_at + timedelta(hours=horizon)
        forecast_weather: list[WeatherFeature] = []
        if horizon > 0:
            # Future weather in this demo is generated as a synthetic forecast
            # issued at the replay clock. Live inference must use an actual
            # provider forecast whose issue time is no later than that clock.
            future = generator.generate(args.replay_hour + int(horizon))
            forecast_weather = [
                replace(sample, issued_at=target.replay_at, valid_at=valid_at)
                for sample in future.weather
            ]
        snapshots.extend(
            builder.build(
                cells=target.cells,
                issued_at=target.replay_at,
                valid_at=valid_at,
                horizon_hours=horizon,
                sensor_readings=sensor_readings,
                weather_features=[*weather_features, *forecast_weather],
                static_features=target.static_features,
                traffic_observations=target.roads,
                fire_detections=target.fires,
                dataset_refs=target.dataset_refs,
            )
        )
    payload = {
        "schema_version": "feature-export-v1",
        "profile": target.profile,
        "scenario_id": target.scenario_id,
        "replay_at": target.replay_at.isoformat().replace("+00:00", "Z"),
        "history_hours": args.history_hours,
        "feature_count": len(snapshots),
        "features": [feature_snapshot_to_dict(snapshot) for snapshot in snapshots],
    }
    output_path = Path(args.out)
    content = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    changed = not output_path.exists() or output_path.read_text(encoding="utf-8") != content
    if changed:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8", newline="\n")
    print(
        f"Feature export {'wrote' if changed else 'unchanged'}: profile={target.profile} "
        f"scenario={target.scenario_id} features={len(snapshots)} path={output_path}"
    )
    return 0


async def _run_prediction_publish(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "feature-export-v1":
        raise ValueError("--input must be a feature-export-v1 file from demo-features")
    raw_features = payload.get("features")
    if not isinstance(raw_features, list) or not raw_features:
        raise ValueError("feature-export-v1 must contain a non-empty features array")
    snapshots = [feature_snapshot_from_dict(item) for item in raw_features]
    generated_at = _parse_utc_argument(args.generated_at) if args.generated_at else parse_utc(
        payload.get("replay_at", snapshots[0].issued_at.isoformat())
    )
    feature_run_id = args.feature_run_id
    run_id = args.run_id or f"prediction-{feature_run_id}"
    mode = DataMode(args.mode)
    assert_live_snapshots_available(mode, snapshots)

    session = get_session_factory()()
    try:
        SqlFeatureSnapshotRepository(session).upsert_many(feature_run_id, snapshots)
        forecasts = SqlForecastRepository(session)
        baselines: dict[tuple[str, float], float] = {}
        for horizon in sorted({item.horizon_hours for item in snapshots if item.horizon_hours > 0}):
            for forecast in forecasts.latest_for_horizon(horizon):
                if abs((forecast.generated_at - generated_at).total_seconds()) <= 3_600:
                    baselines[(forecast.h3_cell, horizon)] = forecast.predicted_pm25
        pdi = {
            state.h3_cell: state.pdi
            for state in SqlGridStateRepository(session).latest()
            if state.pdi is not None and state.timestamp >= generated_at - timedelta(hours=3)
        }
        publisher = PredictionPublicationService(
            SqlPredictionPublicationRepository(session), SqlModelVersionRepository(session)
        )
        run, results = publisher.publish(
            run_id=run_id,
            feature_run_id=feature_run_id,
            region=args.region,
            mode=mode,
            generated_at=generated_at,
            snapshots=snapshots,
            baseline_by_cell_horizon=baselines,
            pdi_by_cell=pdi,
            scenario_id=args.scenario_id or payload.get("scenario_id"),
        )
    finally:
        session.close()

    print(
        f"Prediction run published: run_id={run.run_id} mode={run.mode.value} "
        f"cells={len({result.h3_cell for result in results})} results={len(results)} "
        f"models={','.join(run.model_versions) or 'baseline-only'}"
    )
    return 0


async def _run_expire_reports(args: argparse.Namespace) -> int:
    """Persist `expired` for open claims whose report window has closed.

    Expiry is a fact about the clock, not a judgement, so this is a sweep: the
    read side already reports an aged-out claim as expired without it (see
    app.domain.report_lifecycle.effective_status). Running it only makes the
    *stored* status agree, which matters for the audit trail and for any query
    that filters on status.

    Safe to run repeatedly, and safe to run alongside submissions: it only
    touches claims whose window has already closed.

    Exit 0 when the sweep ran (including "nothing to expire"), 1 on failure.
    """
    from app.db.repositories import SqlFireReportRepository
    from app.db.session import get_session_factory
    from app.services.reports import FireReportService

    settings = get_settings()
    try:
        session = get_session_factory()()
    except Exception as exc:  # noqa: BLE001 - one clear line instead of a traceback
        print(f"Could not open a database session: {exc}", file=sys.stderr)
        return 1
    try:
        service = FireReportService(SqlFireReportRepository(session), settings)
        expired = service.expire_due()
    except Exception as exc:  # noqa: BLE001 - one clear line, not a traceback
        print(f"Expiry sweep failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()

    print(f"Expired {len(expired)} report(s) whose window had closed.")
    for report in expired:
        print(
            f"  report {report.id} ({report.h3_cell}) was reported at "
            f"{report.reported_at.isoformat()}"
        )
    return 0


async def _run_hotspot_scan(args: argparse.Namespace) -> int:
    """Run the candidate-hotspot detector over one or more case fixtures.

    Each case is an authored document: a versioned georeferenced imagery
    artifact, the FIRMS and station signals offered as support, and the labels
    used to score the result. The command prints the candidates with their
    provenance, records the scan under HOTSPOT_SCAN_DIR (where
    GET /api/v1/hotspots/{scan_id} serves it), and reports the false-positive /
    missed-detections assessment.

    Exit codes, chosen so a script can tell the three situations apart:

    * `0` — the detector ran. This includes "it ran and found nothing": an empty
      candidate list with a `candidates` verdict.
    * `2` — insufficient evidence: no usable imagery, so nothing was detected.
      Not a failure, and never a silent zero.
    * `1` — the command could not run: unreadable fixture, bad georeferencing, or
      a store that could not be written.

    The scan time defaults to the case's own `scan_time`, so a fixture produces
    the same result on any machine on any day; `--evaluated-at` overrides it.
    """
    settings = get_settings()
    config = DetectorConfig.from_settings(settings)
    detector = HotspotDetector(config)
    store = HotspotScanStore(args.out_dir or settings.hotspot_scan_dir)

    fixtures: list[Path] = [Path(value) for value in (args.fixture or [])]
    if args.fixture_dir:
        fixtures.extend(sorted(Path(args.fixture_dir).glob("*.json")))
    if not fixtures:
        print(
            "Nothing to scan: pass --fixture <path> (repeatable) or --fixture-dir <dir>.",
            file=sys.stderr,
        )
        return 1

    if args.evaluated_at:
        try:
            evaluated_at = _parse_utc_argument(args.evaluated_at)
        except ValueError as exc:
            print(f"Invalid --evaluated-at: {exc}", file=sys.stderr)
            return 1
    else:
        evaluated_at = None

    exit_code = 0
    report: dict = {"detector_version": DETECTOR_VERSION, "scans": []}
    for fixture in fixtures:
        try:
            case = load_case(fixture)
        except HotspotInputError as exc:
            print(f"Refusing {fixture}: {exc}", file=sys.stderr)
            return 1
        try:
            scan = run_case(case, detector=detector, evaluated_at=evaluated_at)
        except HotspotInputError as exc:
            print(f"Refusing {fixture}: {exc}", file=sys.stderr)
            return 1
        try:
            path = store.write(scan)
        except (OSError, ValueError) as exc:
            print(f"Could not record the scan for {fixture}: {exc}", file=sys.stderr)
            return 1

        report["scans"].append(scan.to_dict())
        _print_hotspot_scan(scan, fixture=fixture, recorded_at=path)
        if scan.verdict is ScanVerdict.INSUFFICIENT_EVIDENCE:
            exit_code = 2

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"report written to {out_path}")
    return exit_code


def _print_hotspot_scan(scan, *, fixture: Path, recorded_at: Path) -> None:
    """Print one scan the way a reviewer needs to read it."""
    print(f"Case: {scan.case_title} ({scan.case_id})")
    print(f"  fixture: {fixture}")
    print(f"  scan_id: {scan.scan_id}")
    print(f"  detector: {scan.detector_version}   evaluated_at: {scan.evaluated_at.isoformat()}")
    print(f"  verdict: {scan.verdict.value}")
    for reason in scan.reasons:
        print(f"    - {reason}")
    if scan.imagery is not None:
        first, last = scan.imagery.acquisition_window
        print(
            f"  imagery: {scan.imagery.source} {scan.imagery.product} "
            f"{scan.imagery.product_version} index={scan.imagery.index_name} "
            f"license={scan.imagery.license} synthetic={scan.imagery.synthetic}"
        )
        print(
            f"    acquisition {first.isoformat()} .. {last.isoformat()} "
            f"({scan.imagery.tile_count} tile(s), H3 res {scan.imagery.h3_resolution})"
        )
        print(f"    digest: {scan.imagery_digest}")
    print(
        f"  tiles: " + ", ".join(f"{name}={count}" for name, count in scan.tile_counts.items())
    )
    print(f"  supporting signals supplied: fires={scan.fire_count} stations={scan.station_count}")

    if scan.candidates:
        print(f"  candidate hotspots ({len(scan.candidates)}):")
        for candidate in scan.candidates:
            print(
                f"    {candidate.h3_cell} ({candidate.latitude:.4f}, {candidate.longitude:.4f}) "
                f"acquired {candidate.acquired_at.isoformat()}"
            )
            print(
                f"      confidence={candidate.confidence.value} "
                f"({candidate.confidence_score:.2f}) "
                f"sources={'+'.join(source.value for source in candidate.supporting_sources)}"
            )
            print(f"      review_status={candidate.review_status.value} pm25_ugm3={candidate.pm25_ugm3}")
            for item in candidate.evidence:
                print(f"        {item.source.value} @ {item.observed_at.isoformat()}: {item.detail}")
    else:
        print("  candidate hotspots: none")

    evaluation = scan.evaluation
    print(
        f"  evaluation: {evaluation.status.value} "
        f"(sufficient={evaluation.sufficient}, labels={evaluation.label_provenance})"
    )
    for reason in evaluation.reasons:
        print(f"    - {reason}")
    print(
        f"    tp={evaluation.true_positives} fp={evaluation.false_positives} "
        f"fn={evaluation.false_negatives} unlabelled={evaluation.unlabelled_predictions} "
        f"precision={_fmt(evaluation.precision)} recall={_fmt(evaluation.recall)}"
    )
    if evaluation.false_positive_cells:
        print(f"    false-positive cells: {', '.join(evaluation.false_positive_cells)}")
    if evaluation.missed_cells:
        print(f"    missed cells: {', '.join(evaluation.missed_cells)}")
    if evaluation.unlabelled_cells:
        print(f"    unlabelled predicted cells: {', '.join(evaluation.unlabelled_cells)}")
    print(f"  recorded at: {recorded_at}")
    print(
        "  note: a candidate is a location for human review - not a PM2.5 value, not an "
        "identified source, and never auto-confirmed."
    )


async def _run_corridor_evaluate(args: argparse.Namespace) -> int:
    """Score a named corridor's published forecast against real stations.

    Prints (and optionally writes) one report: the event, its horizons and
    source times, the metrics per horizon and geography with their sample
    counts, and the coverage. When the real labels are not there, the report is
    an explicit insufficient-data result naming the gap — the command exits 2,
    distinct from a failure (1), because "not enough real data" is an answer,
    not a crash, and never a synthetic number dressed up as accuracy.
    """
    settings = get_settings()
    corridor = get_corridor(args.corridor)
    if corridor is None:
        print(f"No corridor with id {args.corridor!r}.", file=sys.stderr)
        print(f"Known corridors: {', '.join(c.corridor_id for c in list_corridors())}", file=sys.stderr)
        return 1
    horizons = (
        tuple(float(value) for value in args.horizons.split(",") if value.strip())
        if args.horizons
        else None
    )
    session = get_session_factory()()
    try:
        try:
            event, run, results = event_for_run(
                session, corridor=corridor, run_id=args.run_id, horizons=horizons
            )
        except EventNotFoundError as exc:
            print(f"Corridor evaluation unavailable: {exc}", file=sys.stderr)
            return 1
        except SQLAlchemyError as exc:
            # An unreachable database is a clear answer, not a crash: a corridor
            # event is built from a published v2 run, so there is nothing to
            # score without one.
            print(
                f"Corridor evaluation unavailable: the database is unreachable ({exc!r}). "
                "A corridor event is built from a published v2 run, so there is nothing "
                "to score without it.",
                file=sys.stderr,
            )
            return 1
        evaluation = evaluate_event(
            session,
            corridor=corridor,
            event=event,
            results=results,
            settings=settings,
            min_labels=args.min_labels,
            high_pollution_threshold_ugm3=args.threshold,
            require_unused_stations=args.require_unused_stations,
        )
        peak_value, peak_horizon = event_peak(results)
    finally:
        session.close()

    report = {
        "corridor": {
            "corridor_id": corridor.corridor_id,
            "name": corridor.name,
            "kind": corridor.kind.value,
            "geometry_source": corridor.geometry_source.value,
            "geometry_description": corridor.describe_geometry(),
        },
        "event": {
            "event_id": event.event_id,
            "run_id": run.run_id,
            "run_mode": run.mode.value,
            "run_synthetic": evaluation.event.run_synthetic,
            "issued_at": event.issued_at.isoformat(),
            "horizons": [
                {
                    "horizon_hours": point.horizon_hours,
                    "issued_at": point.issued_at.isoformat(),
                    "valid_at": point.valid_at.isoformat(),
                }
                for point in event.horizons
            ],
            "cells": len(event.cells),
            "peak_predicted_ugm3": peak_value,
            "peak_horizon_hours": peak_horizon,
        },
        "evaluation": {
            "verdict": evaluation.verdict.value,
            "usable_as_real_world_evidence": evaluation.is_usable_as_real_world_evidence,
            "label_provenance": evaluation.label_provenance,
            "label_count": evaluation.event.label_count,
            "label_sources": list(evaluation.event.label_sources),
            "min_labels": evaluation.min_labels,
            "high_pollution_threshold_ugm3": evaluation.high_pollution_threshold_ugm3,
            "reasons": list(evaluation.reasons),
            "coverage": evaluation.coverage.to_dict(),
            "slices": [item.to_dict() for item in evaluation.slices],
        },
    }

    print(f"Corridor: {corridor.name} ({corridor.corridor_id})")
    print(f"  geometry: {evaluation_label_geometry(corridor)}")
    print(f"  event: {event.event_id}")
    print(f"  run: {run.run_id} (mode={run.mode.value}, synthetic={evaluation.event.run_synthetic})")
    print(
        f"  issued_at={event.issued_at.isoformat()} "
        f"horizons={[point.horizon_hours for point in event.horizons]} "
        f"peak={peak_value} µg/m³ at +{peak_horizon}h"
    )
    print(
        f"  verdict: {evaluation.verdict.value} "
        f"(usable as real-world evidence: {evaluation.is_usable_as_real_world_evidence})"
    )
    for reason in evaluation.reasons:
        print(f"    - {reason}")
    print(
        f"  labels: {evaluation.event.label_count} observation(s) "
        f"from {', '.join(evaluation.event.label_sources) or 'no source'}"
    )
    print("  coverage:")
    for name, value in evaluation.coverage.to_dict().items():
        print(f"    {name}={value}")
    if evaluation.slices:
        print("  metrics by horizon x geography (only sufficient slices are quoted):")
        for item in evaluation.slices:
            if not item.sufficient:
                print(
                    f"    +{item.horizon_hours:g}h {item.geography}: INSUFFICIENT "
                    f"({item.note})"
                )
                continue
            print(
                f"    +{item.horizon_hours:g}h {item.geography}: "
                f"n={item.pairs} mae={_fmt(item.mae_ugm3)} rmse={_fmt(item.rmse_ugm3)} "
                f"bias={_fmt(item.bias_ugm3)} "
                f"recall@{item.high_pollution_threshold_ugm3:g}="
                f"{_fmt(item.high_pollution_recall)} "
                f"precision={_fmt(item.high_pollution_precision)}"
                + (f"  [{item.note}]" if item.note else "")
            )
    else:
        print("  metrics: none — no slice had enough real labels")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"  report written to {out_path}")

    return 0 if evaluation.is_usable_as_real_world_evidence else 2


def evaluation_label_geometry(corridor) -> str:
    return f"{corridor.geometry_source.value} — {corridor.geometry_note}"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _add_demo_snapshot_parser(subparsers: argparse._SubParsersAction, command: str) -> None:
    demo_parser = subparsers.add_parser(
        command,
        help="Generate a deterministic offline environmental scenario snapshot.",
    )
    demo_parser.add_argument(
        "--profile",
        choices=("tiny-ci", "regional-demo", "seasonal-training-smoke"),
        default="tiny-ci",
    )
    demo_parser.add_argument("--scenario", default=None)
    demo_parser.add_argument("--seed", type=int, default=None)
    demo_parser.add_argument("--anchor", default=None, help="UTC anchor timestamp (RFC 3339).")
    replay_group = demo_parser.add_mutually_exclusive_group()
    replay_group.add_argument("--replay-hour", type=int, default=0)
    replay_group.add_argument(
        "--at", default=None, help="UTC replay timestamp; mutually exclusive with replay-hour."
    )
    demo_parser.add_argument("--manifest", default=None)
    demo_parser.add_argument("--out", default="demo-snapshot.json")
    demo_parser.set_defaults(func=_run_demo_snapshot)


def _add_demo_features_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "demo-features", help="Build deterministic environmental features offline."
    )
    parser.add_argument(
        "--profile",
        choices=("tiny-ci", "regional-demo", "seasonal-training-smoke"),
        default="tiny-ci",
    )
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--anchor", default=None, help="UTC anchor timestamp (RFC 3339).")
    parser.add_argument("--replay-hour", type=int, default=0)
    parser.add_argument("--history-hours", type=int, default=24)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--out", default="feature-snapshot.json")
    parser.set_defaults(func=_run_demo_features)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Development commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Run one OpenAQ PM2.5 ingestion pass.")
    ingest_parser.add_argument("--min-lat", type=float, default=None)
    ingest_parser.add_argument("--min-lon", type=float, default=None)
    ingest_parser.add_argument("--max-lat", type=float, default=None)
    ingest_parser.add_argument("--max-lon", type=float, default=None)
    ingest_parser.set_defaults(func=_run_ingest)

    weather_parser = subparsers.add_parser(
        "ingest-weather", help="Run one Open-Meteo weather ingestion pass."
    )
    weather_parser.add_argument("--min-lat", type=float, default=None)
    weather_parser.add_argument("--min-lon", type=float, default=None)
    weather_parser.add_argument("--max-lat", type=float, default=None)
    weather_parser.add_argument("--max-lon", type=float, default=None)
    weather_parser.set_defaults(func=_run_ingest_weather)

    fire_parser = subparsers.add_parser(
        "ingest-fires", help="Fetch and retain NASA FIRMS VIIRS NRT detections."
    )
    fire_parser.add_argument("--days", type=int, choices=range(1, 6), default=2)
    fire_parser.add_argument("--region", default="delhi-ncr")
    fire_parser.add_argument("--min-lat", type=float, default=None)
    fire_parser.add_argument("--min-lon", type=float, default=None)
    fire_parser.add_argument("--max-lat", type=float, default=None)
    fire_parser.add_argument("--max-lon", type=float, default=None)
    fire_parser.set_defaults(func=_run_ingest_fires)

    traffic_parser = subparsers.add_parser(
        "ingest-traffic", help="Import a normalized, licensed sampled-traffic JSONL feed."
    )
    traffic_parser.add_argument("--input", required=True)
    traffic_parser.add_argument("--source", required=True)
    traffic_parser.add_argument("--product", required=True)
    traffic_parser.add_argument("--version", required=True)
    traffic_parser.add_argument("--region", required=True)
    traffic_parser.add_argument("--attribution", required=True)
    traffic_parser.add_argument("--license", required=True)
    traffic_parser.set_defaults(func=_run_ingest_traffic)

    grid_parser = subparsers.add_parser(
        "export-grid", help="Export the configured region's H3 grid as GeoJSON."
    )
    grid_parser.add_argument("--out", type=str, default="grid.geojson")
    grid_parser.add_argument("--min-lat", type=float, default=None)
    grid_parser.add_argument("--min-lon", type=float, default=None)
    grid_parser.add_argument("--max-lat", type=float, default=None)
    grid_parser.add_argument("--max-lon", type=float, default=None)
    grid_parser.set_defaults(func=_run_export_grid)

    forecast_parser = subparsers.add_parser(
        "forecast",
        help="Run one forecast pipeline pass (DeterministicH3DispersionModel) and persist results.",
    )
    forecast_parser.set_defaults(func=_run_forecast)

    _add_demo_snapshot_parser(subparsers, "demo-generate")
    _add_demo_snapshot_parser(subparsers, "demo-replay")
    _add_demo_features_parser(subparsers)

    publish_parser = subparsers.add_parser(
        "prediction-publish",
        help="Publish one immutable v2 prediction run from a feature-export-v1 file.",
    )
    publish_parser.add_argument("--input", required=True)
    publish_parser.add_argument("--feature-run-id", required=True)
    publish_parser.add_argument("--run-id", default=None)
    publish_parser.add_argument("--mode", choices=("live", "demo", "mixed"), required=True)
    publish_parser.add_argument("--region", default="india")
    publish_parser.add_argument("--scenario-id", default=None)
    publish_parser.add_argument("--generated-at", default=None)
    publish_parser.set_defaults(func=_run_prediction_publish)

    export_parser = subparsers.add_parser(
        "training-export",
        help="Normalize prejoined historical station labels and as-of features.",
    )
    export_parser.add_argument("--input", required=True, help="JSON or JSONL joined examples.")
    export_parser.add_argument("--mode", choices=("live", "demo"), required=True)
    export_parser.add_argument("--start", default=None, help="UTC date/timestamp, inclusive.")
    export_parser.add_argument("--end", default=None, help="UTC date/timestamp, exclusive.")
    export_parser.add_argument("--out", default="training-dataset.json")
    export_parser.set_defaults(func=_run_training_export)

    smoke_parser = subparsers.add_parser(
        "training-smoke-data",
        help="Generate fictional labeled examples for pipeline smoke tests only.",
    )
    smoke_parser.add_argument("--hours", type=int, default=24)
    smoke_parser.add_argument("--stations", type=int, default=6)
    smoke_parser.add_argument("--anchor", default="2025-01-01T00:00:00Z")
    smoke_parser.add_argument("--out", default="training-smoke-dataset.json")
    smoke_parser.set_defaults(func=_run_training_smoke_data)

    train_parser = subparsers.add_parser(
        "train", help="Train and register a reproducible residual-model candidate."
    )
    train_parser.add_argument("--dataset", required=True)
    train_parser.add_argument("--artifact-dir", default="models/candidates")
    train_parser.add_argument("--registry", default="models/registry.json")
    train_parser.add_argument("--ridge-alpha", type=float, default=1.0)
    train_parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Allow synthetic smoke-test training; it remains ineligible for live promotion.",
    )
    train_parser.add_argument(
        "--register-db", action="store_true", help="Also upsert candidate metadata into Postgres."
    )
    train_parser.set_defaults(func=_run_train)

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="Evaluate a pinned model artifact against a labeled split."
    )
    evaluate_parser.add_argument("--model", required=True)
    evaluate_parser.add_argument("--split", required=True)
    evaluate_parser.add_argument("--out", default="evaluation-report.json")
    evaluate_parser.set_defaults(func=_run_evaluate)

    feature_eval_parser = subparsers.add_parser(
        "evaluate-feature-group",
        help="Ablate fire/traffic predictors on identical live observed-label splits.",
    )
    feature_eval_parser.add_argument("--dataset", required=True)
    feature_eval_parser.add_argument("--group", choices=("fires", "traffic"), required=True)
    feature_eval_parser.add_argument(
        "--as-of-verified",
        action="store_true",
        help="Assert that feature availability was checked against each issue time.",
    )
    feature_eval_parser.add_argument("--ridge-alpha", type=float, default=1.0)
    feature_eval_parser.add_argument("--out", default="incremental-feature-report.json")
    feature_eval_parser.set_defaults(func=_run_evaluate_feature_group)

    validate_parser = subparsers.add_parser(
        "model-validate",
        help="Validate one live candidate against its pinned evaluation and M5 ablations.",
    )
    validate_parser.add_argument("--model-id", required=True)
    validate_parser.add_argument("--registry", default="models/registry.json")
    validate_parser.add_argument(
        "--incremental-report",
        action="append",
        default=[],
        help="M5 fire/traffic incremental report; repeat for each feature group used.",
    )
    validate_parser.add_argument(
        "--register-db", action="store_true", help="Operate on the Postgres model registry."
    )
    validate_parser.set_defaults(func=_run_model_validate)

    promote_parser = subparsers.add_parser(
        "model-promote", help="Promote a validated live artifact; retire the previous version."
    )
    promote_parser.add_argument("--model-id", required=True)
    promote_parser.add_argument("--registry", default="models/registry.json")
    promote_parser.add_argument(
        "--register-db", action="store_true", help="Operate on the Postgres model registry."
    )
    promote_parser.set_defaults(func=_run_model_promote)

    rollback_parser = subparsers.add_parser(
        "model-rollback", help="Restore a retired artifact for its region and horizon."
    )
    rollback_parser.add_argument("--model-id", required=True)
    rollback_parser.add_argument("--registry", default="models/registry.json")
    rollback_parser.add_argument(
        "--register-db", action="store_true", help="Operate on the Postgres model registry."
    )
    rollback_parser.set_defaults(func=_run_model_rollback)

    monitor_parser = subparsers.add_parser(
        "model-monitor", help="Summarize labelled prediction errors, coverage, and feature drift."
    )
    monitor_parser.add_argument(
        "--input", required=True, help="A model-monitor-v1 JSON report input."
    )
    monitor_parser.add_argument("--out", default="model-monitor-report.json")
    monitor_parser.set_defaults(func=_run_model_monitor)

    expire_parser = subparsers.add_parser(
        "expire-reports",
        help=(
            "Persist 'expired' for citizen reports whose window has closed (F1 "
            "lifecycle sweep; the read side already reports them as expired)."
        ),
    )
    expire_parser.set_defaults(func=_run_expire_reports)

    media_parser = subparsers.add_parser(
        "verify-media-storage",
        help=(
            "Check the citizen-photo evidence store is configured and writable "
            "(F2). Writes, reads back and deletes a probe object."
        ),
    )
    media_parser.add_argument(
        "--sweep-expired",
        action="store_true",
        help="Also delete evidence past its retention deadline, then report how "
        "many rows were swept.",
    )
    media_parser.set_defaults(func=_run_verify_media_storage)

    corridor_parser = subparsers.add_parser(
        "corridor-evaluate",
        help=(
            "Evaluate a named corridor's published forecast against withheld real "
            "station observations (exit 2 when there is not enough real data)."
        ),
    )
    corridor_parser.add_argument(
        "--corridor", default="delhi-kanpur", help="Corridor id (see /api/v1/corridors)."
    )
    corridor_parser.add_argument("--run-id", default=None, help="Published run to score.")
    corridor_parser.add_argument(
        "--horizons", default=None, help="Comma-separated horizon hours, e.g. 1,3,6."
    )
    corridor_parser.add_argument(
        "--min-labels", type=int, default=DEFAULT_MIN_LABELS, help="Minimum labels per slice."
    )
    corridor_parser.add_argument(
        "--threshold", type=float, default=None, help="High-pollution threshold (µg/m³)."
    )
    corridor_parser.add_argument(
        "--require-unused-stations",
        action="store_true",
        help=(
            "Require stations that did not contribute to the estimate. Not available "
            "yet: reported as a data gap rather than approximated."
        ),
    )
    corridor_parser.add_argument("--out", default=None, help="Write the JSON report here.")
    corridor_parser.set_defaults(func=_run_corridor_evaluate)

    federation_parser = subparsers.add_parser(
        "federation-demo",
        help=(
            "Train two synthetic regional partitions locally, exchange parameter "
            "updates, aggregate and evaluate."
        ),
    )
    federation_parser.add_argument("--out-dir", default="var/federation")
    federation_parser.add_argument("--hours", type=int, default=60)
    federation_parser.add_argument("--station-count", type=int, default=6)
    federation_parser.add_argument("--run-id", default=None)
    federation_parser.add_argument(
        "--no-db",
        action="store_true",
        help="Write local artifacts without persisting a run to PostgreSQL.",
    )
    federation_parser.set_defaults(func=_run_federation_demo)
    hotspot_parser = subparsers.add_parser(
        "hotspot-scan",
        help=(
            "Run the candidate-hotspot detector over a case fixture and record the scan "
            "(exit 2 when the inputs are insufficient, 0 when it ran)."
        ),
    )
    hotspot_parser.add_argument(
        "--fixture",
        action="append",
        default=None,
        help=(
            "Case fixture JSON (repeatable): georeferenced imagery plus optional FIRMS and "
            "station signals and authored labels."
        ),
    )
    hotspot_parser.add_argument(
        "--fixture-dir",
        default=None,
        help="Scan every *.json case fixture in this directory.",
    )
    hotspot_parser.add_argument(
        "--out-dir",
        default=None,
        help="Where to record scans (default: HOTSPOT_SCAN_DIR).",
    )
    hotspot_parser.add_argument("--out", default=None, help="Also write the full report JSON here.")
    hotspot_parser.add_argument(
        "--evaluated-at",
        default=None,
        help=(
            "Override the scan time (RFC 3339 UTC). Defaults to each fixture's own scan_time, "
            "so a case is reproducible on any day."
        ),
    )
    hotspot_parser.set_defaults(func=_run_hotspot_scan)

    args = parser.parse_args(argv)
    logging.basicConfig(level=get_settings().log_level)
    try:
        return asyncio.run(args.func(args))
    except Exception as exc:  # deliberately broad: the last line of defense so an
        # unreachable database (or any other failure no command's own error
        # handling already covers, e.g. a repository read outside a service's
        # try/except) prints one clear line instead of a raw traceback.
        logger.exception("Command failed with an unexpected error")
        print(f"Fatal: {exc!r}", file=sys.stderr)
        return 1


async def _run_verify_media_storage(args: argparse.Namespace) -> int:
    """Prove the F2 evidence store is configured, writable and self-consistent.

    The failure this exists to catch is the quiet one: `CITIZEN_MEDIA_STORAGE`
    left at the default `disabled`, or a configured filesystem/object store
    that cannot complete a write/read/delete round trip. Either way, it answers
    `media_unavailable` to every upload, which from a client's side reads as
    "this deployment does not take photos" rather than as a misconfiguration.

    So this writes a probe object, reads it back and deletes it rather than
    merely checking that a directory or bucket name exists.

    Exit 0 when the store verified, 1 when it is disabled or unreachable.
    """
    settings = get_settings()

    try:
        store = build_media_store(settings)
        if store is None:
            print(
                "media storage is DISABLED; uploads answer 503 media_unavailable. "
                "Set CITIZEN_MEDIA_STORAGE=filesystem or s3 to enable them.",
                file=sys.stderr,
            )
            return 1
        print(f"storage: {store.verify()}")
    except (MediaStoreUnavailable, MediaStoreError) as exc:
        print(f"Fatal: media store unusable: {exc}", file=sys.stderr)
        return 1

    print(
        f"limits: {settings.citizen_media_max_bytes} bytes/file, "
        f"{settings.citizen_media_max_per_report} per report, "
        f"derivative max edge {settings.citizen_media_derivative_max_edge}px, "
        f"retention {settings.citizen_media_retention_hours}h"
    )

    if args.sweep_expired:
        from datetime import UTC, datetime

        from app.db.repositories import SqlEvidenceRepository
        from app.db.session import get_session_factory
        from app.services.evidence import EvidenceService
        from app.services.reports import FireReportService

        session = get_session_factory()()
        try:
            evidence = EvidenceService(
                settings=settings,
                store=store,
                repository=SqlEvidenceRepository(session),
                reports=FireReportService(None),
            )
            swept = evidence.purge_expired(now=datetime.now(UTC))
            print(f"retention sweep: {swept} row(s) expired and removed")
        finally:
            session.close()

    return 0


async def _run_federation_demo(args: argparse.Namespace) -> int:
    """Run the deterministic two-partition federation demonstration."""
    payload = run_federation_demo(
        out_dir=Path(args.out_dir),
        hours=args.hours,
        station_count=args.station_count,
        run_id=args.run_id,
        persist=not args.no_db,
    )
    print(
        f"Federation demonstration: run_id={payload['run_id']} "
        f"status={payload['status']} participants={payload['participant_count']} "
        f"raw_rows_sent={payload['raw_rows_exchanged_to_aggregator']}"
    )
    for participant in payload["participants"]:
        print(
            f"  {participant['participant_id']} ({participant['region_label']}): "
            f"train={participant['train_count']} heldout={participant['test_count']} "
            f"update_sha256={participant['update_sha256'][:12]}…"
        )
    print(f"aggregate: {payload['aggregate']['artifact_path']}")
    evaluation = payload["evaluation"]
    for horizon in evaluation.get("horizons", []):
        print(
            f"  evaluation h={horizon['horizon_hours']}: "
            f"mae={horizon['mae_ugm3']:.3f} "
            f"baseline_mae={horizon['baseline_mae_ugm3']:.3f} "
            f"n={horizon['heldout_count']} (synthetic-only)"
        )
    if not evaluation.get("horizons"):
        print(f"  evaluation: {evaluation['status']} — {evaluation['reason']}")
    print("  limitations: no privacy guarantee; not a nationwide deployment")
    return 0 if payload["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
