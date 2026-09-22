"""Orchestrate retained fire/traffic ingestion and source-quality reporting."""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.environmental_observations import TrafficObservation
from app.domain.features import InputKind
from app.domain.providers import ProviderError
from app.domain.repositories import (
    DatasetVersionRepository,
    FireHotspotRepository,
    IngestionRunRepository,
    TrafficObservationRepository,
)
from app.domain.scenario import DatasetVersion, IngestionRun, IngestionRunStatus
from app.domain.types import BoundingBox
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


class EnvironmentalIngestionService:
    """Fire NRT pulls and contract-normalized traffic imports share run history."""

    def __init__(
        self,
        *,
        fire_provider: FirmsProvider | None,
        fire_repository: FireHotspotRepository,
        traffic_repository: TrafficObservationRepository,
        dataset_repository: DatasetVersionRepository,
        run_repository: IngestionRunRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fire_provider = fire_provider
        self._fire_repository = fire_repository
        self._traffic_repository = traffic_repository
        self._dataset_repository = dataset_repository
        self._run_repository = run_repository
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
