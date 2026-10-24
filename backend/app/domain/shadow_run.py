"""Domain models and contracts for RT11 live shadow collection, measurements,
failure scenario verification, and live cutover readiness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class LagMetrics:
    """Lag metrics between physical observation, provider publication, and application ingestion."""

    sample_count: int
    observation_to_provider_seconds: float | None = None
    provider_to_app_seconds: float | None = None
    max_observation_to_provider_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "observation_to_provider_seconds": (
                round(self.observation_to_provider_seconds, 2)
                if self.observation_to_provider_seconds is not None
                else None
            ),
            "provider_to_app_seconds": (
                round(self.provider_to_app_seconds, 2)
                if self.provider_to_app_seconds is not None
                else None
            ),
            "max_observation_to_provider_seconds": (
                round(self.max_observation_to_provider_seconds, 2)
                if self.max_observation_to_provider_seconds is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class StationCoverageMetrics:
    """Active ground station coverage and freshness in the pilot region."""

    total_stations: int
    active_fresh_stations: int
    stale_stations: int

    @property
    def freshness_fraction(self) -> float:
        if self.total_stations == 0:
            return 0.0
        return self.active_fresh_stations / self.total_stations

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_stations": self.total_stations,
            "active_fresh_stations": self.active_fresh_stations,
            "stale_stations": self.stale_stations,
            "freshness_fraction": round(self.freshness_fraction, 4),
        }


@dataclass(frozen=True, slots=True)
class StorageGrowthMetrics:
    """Database growth projection and retention headroom."""

    estimated_daily_rows: int
    estimated_daily_bytes: int
    retention_prune_active: bool
    headroom_days: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_daily_rows": self.estimated_daily_rows,
            "estimated_daily_bytes": self.estimated_daily_bytes,
            "retention_prune_active": self.retention_prune_active,
            "headroom_days": (
                round(self.headroom_days, 1) if self.headroom_days is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class JobExecutionSummary:
    """Summary of scheduled collection job executions over the shadow period."""

    job_key: str
    total_runs: int
    successful_runs: int
    failed_runs: int
    consecutive_failures: int
    average_interval_seconds: float | None = None

    @property
    def success_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.successful_runs / self.total_runs

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_key": self.job_key,
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "consecutive_failures": self.consecutive_failures,
            "average_interval_seconds": (
                round(self.average_interval_seconds, 1)
                if self.average_interval_seconds is not None
                else None
            ),
            "success_rate": round(self.success_rate, 4),
        }


@dataclass(frozen=True, slots=True)
class FailureScenarioResult:
    """Result of exercising a controlled operational failure mode."""

    scenario_name: str
    exercised: bool
    graceful_handling: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "exercised": self.exercised,
            "graceful_handling": self.graceful_handling,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class CutoverGateVerdict:
    """One individual readiness gate before live cutover."""

    gate_name: str
    passed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CutoverReadinessReport:
    """Comprehensive readiness assessment for live cutover."""

    evaluated_at: datetime
    mode: str
    ready_for_cutover: bool
    lag: LagMetrics
    station_coverage: StationCoverageMetrics
    storage_growth: StorageGrowthMetrics
    jobs: list[JobExecutionSummary]
    failure_scenarios: list[FailureScenarioResult]
    gates: list[CutoverGateVerdict]
    last_good_live_run_retained: bool
    rollback_path_verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated_at": self.evaluated_at.isoformat(),
            "mode": self.mode,
            "ready_for_cutover": self.ready_for_cutover,
            "lag": self.lag.to_dict(),
            "station_coverage": self.station_coverage.to_dict(),
            "storage_growth": self.storage_growth.to_dict(),
            "jobs": [j.to_dict() for j in self.jobs],
            "failure_scenarios": [s.to_dict() for s in self.failure_scenarios],
            "gates": [g.to_dict() for g in self.gates],
            "last_good_live_run_retained": self.last_good_live_run_retained,
            "rollback_path_verified": self.rollback_path_verified,
        }
