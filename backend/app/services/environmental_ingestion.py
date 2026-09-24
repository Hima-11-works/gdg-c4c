"""Orchestrate retained fire/traffic/forecast-weather ingestion and
source-quality reporting."""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.environmental_observations import TrafficObservation, WeatherForecast
from app.domain.features import InputKind
from app.domain.providers import ProviderError, WeatherForecastSample
from app.domain.repositories import (
    DatasetVersionRepository,
    FireHotspotRepository,
    IngestionRunRepository,
    TrafficObservationRepository,
    WeatherForecastRepository,
)
from app.domain.scenario import DatasetVersion, IngestionRun, IngestionRunStatus
from app.domain.types import BoundingBox, Coordinate
from app.ingestion.firms import FirmsProvider
from app.ingestion.traffic_samples import parse_traffic_jsonl

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnvironmentalIngestionResult:
    run: IngestionRun
    saved: int = 0
    skipped_duplicates: int = 0

    @property
    def succeeded(self) -> bool:
        return self.run.status is IngestionRunStatus.SUCCEEDED


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return timezone-aware timestamps")
    return value.astimezone(UTC)


def _to_forecast(
    sample: WeatherForecastSample,
    *,
    dataset_id: str,
    ingestion_run_id: str,
    source: str,
) -> WeatherForecast:
    """Map a provider sample onto the retained row, with a deterministic id so
    re-fetching the same hour is idempotent."""
    identity = "|".join(
        (dataset_id, sample.h3_cell, sample.valid_at.isoformat(), source)
    )
    return WeatherForecast(
        forecast_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        dataset_id=dataset_id,
        ingestion_run_id=ingestion_run_id,
        source=source,
        h3_cell=sample.h3_cell,
        issued_at=sample.issued_at,
        valid_at=sample.valid_at,
        horizon_hours=sample.horizon_hours,
        wind_speed_ms=sample.wind_speed,
        wind_direction_deg=sample.wind_direction,
        precipitation_mm=sample.precipitation,
        boundary_layer_height_m=sample.boundary_layer_height,
        temperature_c=sample.temperature,
        relative_humidity_pct=sample.humidity,
    )


class EnvironmentalIngestionService:
    """Fire NRT pulls, contract-normalized traffic imports, and forecast-weather
    pulls share run history."""

    def __init__(
        self,
        *,
        fire_provider: FirmsProvider | None,
        fire_repository: FireHotspotRepository,
        traffic_repository: TrafficObservationRepository,
        dataset_repository: DatasetVersionRepository,
        run_repository: IngestionRunRepository,
        forecast_repository: WeatherForecastRepository | None = None,
        weather_forecast_provider: object | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fire_provider = fire_provider
        self._fire_repository = fire_repository
        self._traffic_repository = traffic_repository
        self._dataset_repository = dataset_repository
        self._run_repository = run_repository
        self._forecast_repository = forecast_repository
        self._weather_forecast_provider = weather_forecast_provider
        self._clock = clock or (lambda: datetime.now(UTC))

    async def ingest_firms(
        self,
        bbox: BoundingBox,
        *,
        day_range: int,
        region: str,
        source: str,
        stale_after_hours: float,
    ) -> EnvironmentalIngestionResult:
        if self._fire_provider is None:
            raise ValueError("a NASA FIRMS provider is required for fire ingestion")
        started_at = _utc(self._clock())
        run_id = uuid.uuid4().hex
        dataset_id = f"firms:{run_id}"
        dataset = DatasetVersion(
            dataset_id=dataset_id,
            source="nasa-firms",
            product=source,
            version="NRT (row-level processing versions retained)",
            kind=InputKind.OBSERVED,
            region=region,
            attribution="NASA LANCE FIRMS",
            license="NASA FIRMS data; source attribution retained",
            available_at=started_at,
        )
        self._dataset_repository.upsert(dataset)
        self._run_repository.upsert(
            IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                status=IngestionRunStatus.RUNNING,
            )
        )
        try:
            feed = await self._fire_provider.fetch(
                bbox,
                day_range=day_range,
                dataset_id=dataset_id,
                ingestion_run_id=run_id,
            )
            saved, duplicates = self._fire_repository.save_many(list(feed.detections))
            ages = [
                (feed.fetched_at - item.acquired_at).total_seconds() / 3600
                for item in feed.detections
            ]
            stale = sum(age > stale_after_hours for age in ages)
            metrics = {
                "query_complete": feed.complete,
                "successful_empty_feed": feed.complete and not feed.detections,
                "day_range": day_range,
                "fetched_records": len(feed.detections),
                "saved_records": saved,
                "duplicate_records": duplicates,
                "invalid_rows": feed.invalid_rows,
                "unknown_confidence_rows": feed.unknown_confidence_rows,
                "stale_records": stale,
                "fresh_records": len(ages) - stale,
                "max_detection_age_hours": max(ages, default=None),
                "newest_detection_at": max(
                    (item.acquired_at.isoformat() for item in feed.detections), default=None
                ),
                "model_features_enabled": False,
            }
            finished_at = max(started_at, _utc(self._clock()), feed.fetched_at)
            errors = () if feed.complete else (
                f"{feed.invalid_rows} invalid row(s); feed marked incomplete",
            )
            run = IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                finished_at=finished_at,
                fetched_at=feed.fetched_at,
                status=(
                    IngestionRunStatus.SUCCEEDED
                    if feed.complete
                    else IngestionRunStatus.FAILED
                ),
                errors=errors,
                metrics=metrics,
            )
        except ProviderError as exc:
            finished_at = max(started_at, _utc(self._clock()))
            run = IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                finished_at=finished_at,
                fetched_at=None,
                status=IngestionRunStatus.FAILED,
                errors=(str(exc),),
                metrics={"query_complete": False, "successful_empty_feed": False},
            )
            self._run_repository.upsert(run)
            logger.error("NASA FIRMS ingestion failed for run %s: %s", run_id, exc)
            return EnvironmentalIngestionResult(run)
        except Exception as exc:
            finished_at = max(started_at, _utc(self._clock()))
            run = IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                finished_at=finished_at,
                status=IngestionRunStatus.FAILED,
                errors=(f"unexpected {type(exc).__name__} during FIRMS ingestion",),
                metrics={"query_complete": False, "successful_empty_feed": False},
            )
            self._run_repository.upsert(run)
            logger.error(
                "NASA FIRMS ingestion stopped unexpectedly for run %s (%s)",
                run_id,
                type(exc).__name__,
            )
            return EnvironmentalIngestionResult(run)
        self._dataset_repository.upsert(
            DatasetVersion(
                dataset_id=dataset_id,
                source=dataset.source,
                product=dataset.product,
                version=dataset.version,
                kind=dataset.kind,
                region=dataset.region,
                attribution=dataset.attribution,
                license=dataset.license,
                coverage_start=min((item.acquired_at for item in feed.detections), default=None),
                coverage_end=max((item.acquired_at for item in feed.detections), default=None),
                available_at=feed.fetched_at,
            )
        )
        self._run_repository.upsert(run)
        return EnvironmentalIngestionResult(run, saved, duplicates)

    async def ingest_weather_forecast(
        self,
        points: list[Coordinate],
        *,
        hours: int,
        region: str,
        source: str,
        h3_resolution: int,
    ) -> EnvironmentalIngestionResult:
        """Pull modeled forecast weather and retain it as an auditable dataset.

        Future horizons need a forecast whose *issue* time is known, so each row
        records both ``issued_at`` and ``valid_at``. An empty successful pull is
        a valid zero-hour forecast (recorded as such), and a provider failure is
        recorded as a failed run — the two are never confused, and neither is
        padded with observation values.
        """
        provider = self._weather_forecast_provider
        if provider is None or self._forecast_repository is None:
            raise ValueError("forecast weather ingestion requires a provider and repository")
        started_at = _utc(self._clock())
        run_id = uuid.uuid4().hex
        dataset_id = f"weather-forecast:{source}:{run_id}"
        self._dataset_repository.upsert(
            DatasetVersion(
                dataset_id=dataset_id,
                source=source,
                product="hourly forecast",
                version=f"+{hours}h",
                kind=InputKind.MODELED,
                region=region,
                attribution="Open-Meteo",
                license="Open-Meteo free tier (CC BY 4.0)",
                available_at=started_at,
            )
        )
        self._run_repository.upsert(
            IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                status=IngestionRunStatus.RUNNING,
            )
        )
        try:
            samples: list[WeatherForecastSample] = await provider.fetch_forecast(
                points, hours=hours, h3_resolution=h3_resolution
            )
        except ProviderError as exc:
            run = IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                finished_at=_utc(self._clock()),
                status=IngestionRunStatus.FAILED,
                errors=(str(exc),),
                metrics={"forecast_complete": False, "forecast_rows": 0},
            )
            self._run_repository.upsert(run)
            logger.error("Forecast weather ingestion failed for run %s: %s", run_id, exc)
            return EnvironmentalIngestionResult(run)
        except Exception as exc:  # noqa: BLE001 - one bad pull must not abort the run
            run = IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                finished_at=_utc(self._clock()),
                status=IngestionRunStatus.FAILED,
                errors=(f"unexpected {type(exc).__name__} during forecast ingestion",),
                metrics={"forecast_complete": False, "forecast_rows": 0},
            )
            self._run_repository.upsert(run)
            logger.error("Forecast weather ingestion stopped unexpectedly (%s)", type(exc).__name__)
            return EnvironmentalIngestionResult(run)

        forecasts = [
            _to_forecast(sample, dataset_id=dataset_id, ingestion_run_id=run_id, source=source)
            for sample in samples
        ]
        inserted, duplicates = self._forecast_repository.save_many(forecasts)
        issued = max((item.issued_at for item in forecasts), default=started_at)
        run = IngestionRun(
            run_id=run_id,
            dataset_id=dataset_id,
            started_at=started_at,
            finished_at=max(_utc(self._clock()), issued),
            fetched_at=issued,
            # An empty but complete forecast pull is a success with zero rows,
            # not a failure: "the provider says there is nothing" is data.
            status=IngestionRunStatus.SUCCEEDED,
            metrics={
                "forecast_complete": True,
                "requested_locations": len(points),
                "forecast_rows": len(forecasts),
                "inserted_records": inserted,
                "duplicate_records": duplicates,
                "covered_cells": len({item.h3_cell for item in forecasts}),
                "issued_at": issued.isoformat(),
                "max_horizon_hours": max(
                    (item.horizon_hours for item in forecasts), default=None
                ),
            },
        )
        self._run_repository.upsert(run)
        return EnvironmentalIngestionResult(run, inserted, duplicates)

    def import_traffic_jsonl(
        self,
        payload: str,
        *,
        source: str,
        product: str,
        version: str,
        region: str,
        attribution: str,
        license: str,
        stale_after_hours: float,
        h3_resolution: int,
    ) -> EnvironmentalIngestionResult:
        started_at = _utc(self._clock())
        run_id = uuid.uuid4().hex
        identity = "|".join((source, product, version, region))
        dataset_id = f"traffic:{hashlib.sha256(identity.encode()).hexdigest()}"
        dataset = DatasetVersion(
            dataset_id=dataset_id,
            source=source,
            product=product,
            version=version,
            kind=InputKind.OBSERVED,
            region=region,
            attribution=attribution,
            license=license,
            available_at=started_at,
        )
        self._dataset_repository.upsert(dataset)
        self._run_repository.upsert(
            IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                status=IngestionRunStatus.RUNNING,
            )
        )
        fetched_at = _utc(self._clock())
        try:
            observations = parse_traffic_jsonl(
                payload,
                source=source,
                dataset_id=dataset_id,
                ingestion_run_id=run_id,
                fetched_at=fetched_at,
                stale_after_hours=stale_after_hours,
                h3_resolution=h3_resolution,
            )
            saved, duplicates = self._traffic_repository.save_many(list(observations))
            ages = [(fetched_at - item.observed_at).total_seconds() / 3600 for item in observations]
            stale = sum(age > stale_after_hours for age in ages)
            mean_coverage = sum(item.sampled_road_coverage_fraction for item in observations) / len(observations)
            confidence_count = sum(item.confidence is not None for item in observations)
            metrics = {
                "feed_complete": True,
                "sampled_only": True,
                "sample_count": len(observations),
                "saved_records": saved,
                "duplicate_records": duplicates,
                "stale_records": stale,
                "fresh_records": len(ages) - stale,
                "max_observation_age_hours": max(ages, default=None),
                "mean_sampled_road_coverage_fraction": mean_coverage,
                "confidence_coverage_fraction": confidence_count / len(observations),
                "model_features_enabled": False,
            }
            run = IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                finished_at=max(_utc(self._clock()), fetched_at),
                fetched_at=fetched_at,
                status=IngestionRunStatus.SUCCEEDED,
                metrics=metrics,
            )
        except ValueError as exc:
            finished_at = max(started_at, _utc(self._clock()))
            run = IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                finished_at=finished_at,
                fetched_at=fetched_at,
                status=IngestionRunStatus.FAILED,
                errors=(str(exc),),
                metrics={"feed_complete": False, "sampled_only": True},
            )
            self._run_repository.upsert(run)
            return EnvironmentalIngestionResult(run)
        self._dataset_repository.upsert(
            DatasetVersion(
                dataset_id=dataset_id,
                source=dataset.source,
                product=dataset.product,
                version=dataset.version,
                kind=dataset.kind,
                region=dataset.region,
                attribution=dataset.attribution,
                license=dataset.license,
                coverage_start=min(item.observed_at for item in observations),
                coverage_end=max(item.observed_at for item in observations),
                available_at=fetched_at,
            )
        )
        self._run_repository.upsert(run)
        return EnvironmentalIngestionResult(run, saved, duplicates)
