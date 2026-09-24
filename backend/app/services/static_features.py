"""Import versioned static cell features (population, roads, land cover).

The synthetic scenario has always supplied static features, but the live path
had no way to: a real deployment needs a preprocessed, licensed artifact
(WorldPop-style population, OSM road lengths, a land-cover product) rather than
a raster download inside a request or a run.

This service imports such an artifact through the same typed contract the demo
scenario uses (``app.ingestion.static_features.load_static_features``) and
records it as a ``dataset_version`` + ``ingestion_run``, so a published run can
name exactly which version of which dataset its population came from.

Two rules it enforces:

* **Versioned.** Rows are stored per ``dataset_id``; re-importing refreshes that
  dataset, a new release never overwrites an older one, and the publication
  path picks the newest dataset that was *available* by the run's issue time.
* **No synthetic data in live mode.** In live mode an artifact whose dataset
  refs are ``SYNTHETIC`` is refused outright rather than quietly entering a
  published live run.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.domain.features import CellStaticFeatures, InputKind
from app.domain.repositories import (
    DatasetVersionRepository,
    IngestionRunRepository,
    StaticCellFeatureRepository,
)
from app.domain.scenario import DatasetVersion, IngestionRun, IngestionRunStatus
from app.ingestion.static_features import load_static_features

logger = logging.getLogger(__name__)

# The dataset source the publication path looks for. One source per deployment
# keeps "which static data did this run use?" a single question.
STATIC_SOURCE = "static-cells"


@dataclass(frozen=True, slots=True)
class StaticFeatureImportResult:
    run: IngestionRun
    dataset: DatasetVersion
    rows: int = 0
    cells: int = 0
    min_available_at: datetime | None = None
    max_available_at: datetime | None = None

    @property
    def succeeded(self) -> bool:
        return self.run.status is IngestionRunStatus.SUCCEEDED

    def summary(self) -> str:
        if not self.succeeded:
            errors = "; ".join(self.run.errors) or "unknown error"
            return f"static feature import failed: {errors}"
        return (
            f"static dataset {self.dataset.dataset_id} "
            f"({self.dataset.product} {self.dataset.version}): rows={self.rows} "
            f"available_at={self.min_available_at.isoformat() if self.min_available_at else '-'}"
        )


class StaticFeatureIngestionService:
    """Imports a versioned static artifact into the per-cell store."""

    def __init__(
        self,
        *,
        static_repository: StaticCellFeatureRepository,
        dataset_repository: DatasetVersionRepository,
        run_repository: IngestionRunRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._static = static_repository
        self._datasets = dataset_repository
        self._runs = run_repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def import_artifact(
        self,
        path: Path,
        *,
        region: str,
        live: bool,
        max_age_days: float = 400.0,
    ) -> StaticFeatureImportResult:
        """Load, validate, and store one artifact.

        `live=True` refuses an artifact whose dataset refs are synthetic, which
        is what keeps a demo scenario out of a published live run.
        """
        started_at = self._clock()
        try:
            features = load_static_features(path)
        except (OSError, ValueError) as exc:
            return self._failed(started_at, path, f"artifact could not be read: {exc}")

        if not features:
            return self._failed(started_at, path, "artifact contains no static feature rows")

        if live:
            synthetic = sorted(
                {
                    ref.dataset_id
                    for item in features
                    for ref in item.dataset_refs
                    if ref.kind is InputKind.SYNTHETIC
                }
            )
            if synthetic:
                return self._failed(
                    started_at,
                    path,
                    "live mode refuses synthetic static features: "
                    + ", ".join(synthetic),
                )

        refs = _dataset_refs(features)
        identity = "|".join(
            [STATIC_SOURCE, region]
            + [part for ref in refs for part in (ref.source, ref.product, ref.version)]
        )
        dataset_id = f"static:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"
        available_at = max(
            (item.available_at for item in features if item.available_at is not None),
            default=started_at,
        )
        # A population/land-cover release older than the freshness window is
        # stored, but reported as stale so the publication can decide rather
        # than silently trusting it.
        age_days = (self._clock() - available_at).total_seconds() / 86_400
        if age_days > max_age_days:
            return self._failed(
                started_at,
                path,
                f"static dataset is {age_days:.0f} days old, older than the "
                f"{max_age_days:.0f}-day freshness window",
            )

        run_id = uuid.uuid4().hex
        dataset = DatasetVersion(
            dataset_id=dataset_id,
            source=STATIC_SOURCE,
            product=refs[0].product if refs else "static cells",
            version=refs[0].version if refs else "unversioned",
            kind=InputKind.OBSERVED if not live else InputKind.OBSERVED,
            region=region,
            attribution=refs[0].attribution if refs else "unattributed",
            license=refs[0].license if refs else "unknown",
            available_at=available_at,
        )
        self._datasets.upsert(dataset)
        self._runs.upsert(
            IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                status=IngestionRunStatus.RUNNING,
            )
        )
        try:
            written, _ = self._static.save_many(
                dataset_id=dataset_id, ingestion_run_id=run_id, features=list(features)
            )
        except Exception as exc:  # noqa: BLE001 - reported as a failed run
            run = IngestionRun(
                run_id=run_id,
                dataset_id=dataset_id,
                started_at=started_at,
                finished_at=self._clock(),
                status=IngestionRunStatus.FAILED,
                errors=(f"static feature write failed: {exc}",),
                metrics={"rows_written": 0, "cells": 0},
            )
            self._runs.upsert(run)
            logger.error("Static feature import failed for %s: %s", path, exc)
            return StaticFeatureImportResult(run=run, dataset=dataset)

        run = IngestionRun(
            run_id=run_id,
            dataset_id=dataset_id,
            started_at=started_at,
            finished_at=max(self._clock(), available_at),
            fetched_at=available_at,
            status=IngestionRunStatus.SUCCEEDED,
            metrics={
                "rows_written": written,
                "cells": len(features),
                "population_cells": sum(
                    1 for item in features if item.population_count is not None
                ),
                "land_cover_cells": sum(
                    1
                    for item in features
                    if item.built_up_fraction is not None
                    or item.vegetation_fraction is not None
                ),
                "artifact_available_at": available_at.isoformat(),
                # The artifact may aggregate several upstream releases (a
                # population raster, a road extract, a land-cover product).
                # Their ids are retained here so a published run can name the
                # actual sources behind its one stored static dataset.
                "declared_datasets": [ref.dataset_id for ref in refs],
                "declared_sources": sorted({ref.source for ref in refs}),
                "licenses": sorted({ref.license for ref in refs}),
            },
        )
        self._runs.upsert(run)
        return StaticFeatureImportResult(
            run=run,
            dataset=dataset,
            rows=written,
            cells=len(features),
            min_available_at=min(
                (item.available_at for item in features if item.available_at is not None),
                default=None,
            ),
            max_available_at=available_at,
        )

    def _failed(
        self, started_at: datetime, path: Path, error: str
    ) -> StaticFeatureImportResult:
        run_id = uuid.uuid4().hex
        dataset_id = f"static-failed:{hashlib.sha256(str(path).encode()).hexdigest()[:24]}"
        run = IngestionRun(
            run_id=run_id,
            dataset_id=dataset_id,
            started_at=started_at,
            finished_at=self._clock(),
            status=IngestionRunStatus.FAILED,
            errors=(error,),
            metrics={"rows_written": 0, "artifact": str(path)},
        )
        self._runs.upsert(run)
        logger.warning("Static feature import skipped: %s", error)
        return StaticFeatureImportResult(
            run=run,
            dataset=DatasetVersion(
                dataset_id=dataset_id,
                source=STATIC_SOURCE,
                product="unavailable",
                version="unavailable",
                kind=InputKind.DERIVED,
                region="unknown",
                attribution="unattributed",
                license="unknown",
            ),
        )


def _dataset_refs(features: tuple[CellStaticFeatures, ...]):
    """The artifact's own refs, de-duplicated — the artifact declares its
    source, version, attribution, and license, so nothing is assumed here."""
    seen: dict[str, object] = {}
    for item in features:
        for ref in item.dataset_refs:
            seen.setdefault(ref.dataset_id, ref)
    return tuple(seen.values())


__all__ = ["STATIC_SOURCE", "StaticFeatureImportResult", "StaticFeatureIngestionService"]
