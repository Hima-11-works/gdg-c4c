"""Resolve a published alert into a bounded, run-pinned advisory context."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime

from app.domain.advisories import (
    MAX_ADVISORY_MISSING_ITEMS,
    MAX_ADVISORY_SOURCES,
    AdvisoryContext,
    AdvisoryFact,
    AdvisorySourceReference,
)
from app.domain.features import DataMode, DatasetRef, InputKind
from app.services.prediction_queries import PredictionQueryService
from app.services.published_alerts import PublishedAlertService


class AdvisorySourceUnavailable(LookupError):
    """The requested source cannot be resolved to a published alert."""


class AdvisoryContextService:
    """Build context only from server-resolved alert and same-run evidence."""

    def __init__(
        self,
        *,
        published_alerts: PublishedAlertService,
        queries: PredictionQueryService,
    ) -> None:
        self._published_alerts = published_alerts
        self._queries = queries

    def for_published_alert(
        self, alert_id: str, *, now: datetime | None = None
    ) -> AdvisoryContext:
        """Resolve an ID; measurements or labels supplied by a caller are unused."""
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        alert = self._published_alerts.resolve(alert_id)
        run = self._queries.run(alert.run_id)
        if run.run_id != alert.run_id or run.region != alert.region or run.mode != alert.mode:
            raise AdvisorySourceUnavailable("alert and prediction run provenance did not match")

        # Read the exact cell/horizon again for its source references. Keep the
        # resolver's values authoritative and reject any inconsistent second read.
        rows = self._queries.results(
            run,
            cells=[alert.h3_cell],
            horizons={float(alert.forecast_hours)},
        )
        prediction = next(
            (
                row
                for row in rows
                if row.run_id == run.run_id
                and row.h3_cell == alert.h3_cell
                and math.isclose(row.horizon_hours, alert.forecast_hours)
            ),
            None,
        )
        if (
            prediction is None
            or prediction.predicted_pm25 is None
            or not math.isclose(prediction.predicted_pm25, alert.forecast_pm25)
        ):
            raise AdvisorySourceUnavailable("published alert evidence changed during resolution")

        run_evidence_id = f"run:{run.run_id}:metadata"
        alert_evidence_id = f"alert:{alert.alert_id}:forecast"
        facts = [
            AdvisoryFact(
                evidence_id=alert_evidence_id,
                label="Published forecast PM2.5",
                value=f"{alert.forecast_pm25:.1f} µg/m³ at +{alert.forecast_hours:g} h",
            ),
            AdvisoryFact(
                evidence_id=alert_evidence_id,
                label="Forecast valid at",
                value=_format_time(alert.forecast_time),
            ),
            AdvisoryFact(
                evidence_id=run_evidence_id,
                label="Prediction run generated at",
                value=_format_time(alert.run_generated_at),
            ),
        ]
        if alert.current_pm25 is not None:
            facts.append(
                AdvisoryFact(
                    evidence_id=run_evidence_id,
                    label="Published horizon-0 PM2.5 value",
                    value=(
                        f"{alert.current_pm25:.1f} µg/m³ "
                        "(run value, not independently verified here)"
                    ),
                )
            )

        quality = run.input_quality
        input_age_seconds = self._queries.input_age_seconds(run, now=moment)
        input_age_hours = (
            input_age_seconds / 3600 if input_age_seconds is not None else None
        )
        missing: list[str] = [
            "Independent on-site measurement and pollution-source verification "
            "are not part of this published forecast alert."
        ]
        if quality is None:
            missing.append("Prediction-run input-quality metadata was not recorded.")
        else:
            facts.append(
                AdvisoryFact(
                    evidence_id=run_evidence_id,
                    label="Prediction input cell support",
                    value=f"{quality.supported_cells} of {quality.expected_cells} expected cells",
                )
            )
            if quality.max_observation_age_hours is None:
                missing.append("Age of the oldest source observation is not recorded.")
            if quality.unavailable_reasons:
                missing.append(
                    "One or more prediction inputs were marked unavailable in run metadata."
                )
        if input_age_hours is None:
            missing.append("Effective age of the source observations cannot be calculated.")
        elif self._queries.is_stale(run, now=moment):
            missing.append(
                "The effective source-observation age exceeds the 24-hour freshness policy."
            )

        run_health = self._queries.source_health(run)
        if not run_health:
            missing.append("No per-source health checks are pinned to this prediction run.")

        dataset_refs = _unique_dataset_refs((*run.dataset_refs, *prediction.dataset_refs))
        sources: list[AdvisorySourceReference] = [
            AdvisorySourceReference(
                evidence_id=_dataset_evidence_id(run.run_id, ref),
                source=ref.source[:160],
                description=_truncate(
                    f"{ref.product} v{ref.version} "
                    f"({ref.kind.value}; dataset {ref.dataset_id})",
                    240,
                ),
            )
            for ref in dataset_refs
        ]
        for health in run_health:
            checked_at = (
                _format_time(health.fetched_at)
                if health.fetched_at
                else "time unavailable"
            )
            sources.append(
                AdvisorySourceReference(
                    evidence_id=_health_evidence_id(run.run_id, health.dataset_id),
                    source=health.dataset_id[:160],
                    description=(
                        f"run-pinned collection status {health.status}; "
                        f"{health.item_count} returned item(s); "
                        f"checked {checked_at}"
                    ),
                )
            )
        if not dataset_refs:
            missing.append("The prediction run has no versioned source-dataset references.")
        if len(sources) > MAX_ADVISORY_SOURCES:
            sources = sources[:MAX_ADVISORY_SOURCES]
            missing.append(
                f"Source references are limited to the first {MAX_ADVISORY_SOURCES} records."
            )

        unique_missing = tuple(dict.fromkeys(missing))[:MAX_ADVISORY_MISSING_ITEMS]
        is_fallback = self._queries.is_fallback(run)
        contains_synthetic = (
            alert.synthetic
            or any(ref.kind is InputKind.SYNTHETIC for ref in dataset_refs)
            or run.mode is DataMode.DEMO
        )
        return AdvisoryContext(
            source_type="published_alert",
            source_id=alert.alert_id,
            run_id=run.run_id,
            h3_cell=alert.h3_cell,
            region=run.region,
            mode=run.mode,
            severity=alert.severity,
            generated_at=run.generated_at,
            forecast_at=alert.forecast_time,
            forecast_hours=alert.forecast_hours,
            forecast_pm25=alert.forecast_pm25,
            current_pm25=alert.current_pm25,
            confidence=alert.confidence,
            fallback=is_fallback,
            synthetic=contains_synthetic,
            stale=self._queries.is_stale(run, now=moment),
            input_age_hours=input_age_hours,
            facts=tuple(facts),
            sources=tuple(sources),
            missing_evidence=unique_missing,
        )


def _format_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _unique_dataset_refs(refs: tuple[DatasetRef, ...]) -> tuple[DatasetRef, ...]:
    unique: dict[tuple[str, str], DatasetRef] = {}
    for ref in refs:
        unique.setdefault((ref.dataset_id, ref.version), ref)
    return tuple(unique.values())


def _dataset_evidence_id(run_id: str, ref: DatasetRef) -> str:
    digest = hashlib.sha256(f"{ref.dataset_id}:{ref.version}".encode()).hexdigest()[:16]
    return f"dataset:{run_id}:{digest}"


def _health_evidence_id(run_id: str, dataset_id: str) -> str:
    digest = hashlib.sha256(dataset_id.encode()).hexdigest()[:16]
    return f"source-health:{run_id}:{digest}"


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}…"
