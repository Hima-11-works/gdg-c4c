"""Retention and compaction sweep for historical prediction runs.

Keeps active pins (latest run per region/mode, runs with active alerts, runs with
holdout/audit evidence) while compacting older intermediate granular horizons and
pruning runs past their retention window.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.features import DataMode
from app.domain.prediction import DEFAULT_REGION
from app.domain.repositories import AlertRepository, PredictionPublicationRepository

logger = logging.getLogger(__name__)

DEFAULT_COMPACT_HORIZONS: tuple[float, ...] = (0.0, 1.0, 3.0, 6.0)


@dataclass(frozen=True, slots=True)
class RetentionReport:
    pruned_runs: list[str] = field(default_factory=list)
    compacted_runs: list[str] = field(default_factory=list)
    preserved_runs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.errors


class PredictionRetentionService:
    def __init__(
        self,
        publication_repository: PredictionPublicationRepository,
        alert_repository: AlertRepository | None = None,
    ) -> None:
        self._pub_repo = publication_repository
        self._alert_repo = alert_repository

    def sweep(
        self,
        *,
        now: datetime,
        retention_hours: int = 72,
        compact_after_hours: int = 24,
        compact_keep_horizons: Sequence[float] = DEFAULT_COMPACT_HORIZONS,
    ) -> RetentionReport:
        if retention_hours < 1:
            raise ValueError("retention_hours must be at least 1")
        if compact_after_hours < 1:
            raise ValueError("compact_after_hours must be at least 1")
        if compact_after_hours > retention_hours:
            raise ValueError("compact_after_hours must not exceed retention_hours")

        pruned: list[str] = []
        compacted: list[str] = []
        preserved: list[str] = []
        errors: list[str] = []

        # 1. Collect protected run IDs (active pins)
        pinned_runs: set[str] = set()

        # The latest published run for each mode and default region is always an active pin
        for mode in (DataMode.LIVE, DataMode.DEMO):
            latest = self._pub_repo.latest_run(region=DEFAULT_REGION, mode=mode)
            if latest is not None:
                pinned_runs.add(latest.run_id)

        # Runs pinned by active alerts
        if self._alert_repo is not None:
            try:
                active_alerts = self._alert_repo.list_active(
                    since=now - timedelta(hours=retention_hours)
                )
                for alert in active_alerts:
                    if alert.run_id:
                        pinned_runs.add(alert.run_id)
            except Exception as exc:
                logger.warning("Could not list active alerts for retention sweep: %s", exc)
                errors.append(f"active_alerts_query_failed: {exc}")

        # 2. Inspect runs older than compact_after_hours
        compact_cutoff = now - timedelta(hours=compact_after_hours)
        prune_cutoff = now - timedelta(hours=retention_hours)

        try:
            candidates = self._pub_repo.list_runs_before(compact_cutoff)
        except Exception as exc:
            logger.exception("Failed to query prediction runs before %s", compact_cutoff)
            return RetentionReport(errors=[f"list_runs_failed: {exc}"])

        for run in candidates:
            run_id = run.run_id

            # Keep audit evidence: runs with scenario_id or holdout evaluation flags
            if run.scenario_id or (
                run.input_quality
                and any("holdout" in r for r in run.input_quality.unavailable_reasons)
            ):
                pinned_runs.add(run_id)

            if run_id in pinned_runs:
                preserved.append(run_id)
                continue

            if run.generated_at < prune_cutoff:
                # Prune completely
                try:
                    self._pub_repo.delete_run(run_id)
                    pruned.append(run_id)
                except Exception as exc:
                    logger.exception("Failed to prune run %s", run_id)
                    errors.append(f"prune_failed:{run_id}:{exc}")
            else:
                # Compact intermediate horizons
                try:
                    deleted_rows = self._pub_repo.compact_run_results(
                        run_id, compact_keep_horizons
                    )
                    if deleted_rows > 0:
                        compacted.append(run_id)
                    else:
                        preserved.append(run_id)
                except Exception as exc:
                    logger.exception("Failed to compact run %s", run_id)
                    errors.append(f"compact_failed:{run_id}:{exc}")

        logger.info(
            "Prediction retention sweep: pruned=%d, compacted=%d, preserved=%d, errors=%d",
            len(pruned),
            len(compacted),
            len(preserved),
            len(errors),
        )
        return RetentionReport(
            pruned_runs=pruned,
            compacted_runs=compacted,
            preserved_runs=preserved,
            errors=errors,
        )
