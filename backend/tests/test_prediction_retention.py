from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.features import DataMode
from app.domain.prediction import PredictionInputQuality, PredictionRun
from app.domain.types import Alert, AlertSeverity
from app.services.prediction_retention import PredictionRetentionService
from tests.fakes import FakeAlertRepository


class FakePublicationRepository:
    def __init__(self, runs: list[PredictionRun] | None = None) -> None:
        self.runs = {r.run_id: r for r in (runs or [])}
        self.compacted: dict[str, list[float]] = {}
        self.deleted: list[str] = []

    def latest_run(
        self, *, region: str | None = None, mode: DataMode | None = None
    ) -> PredictionRun | None:
        matches = [
            r for r in self.runs.values()
            if (region is None or r.region == region) and (mode is None or r.mode is mode)
        ]
        matches.sort(key=lambda r: r.generated_at, reverse=True)
        return matches[0] if matches else None

    def list_runs_before(
        self, before: datetime, *, region: str | None = None, mode: DataMode | None = None
    ) -> list[PredictionRun]:
        matches = [
            r for r in self.runs.values()
            if r.generated_at < before
            and (region is None or r.region == region)
            and (mode is None or r.mode is mode)
        ]
        matches.sort(key=lambda r: r.generated_at)
        return matches

    def delete_run(self, run_id: str) -> bool:
        if run_id in self.runs:
            del self.runs[run_id]
            self.deleted.append(run_id)
            return True
        return False

    def compact_run_results(self, run_id: str, keep_horizons: list[float]) -> int:
        self.compacted[run_id] = list(keep_horizons)
        return 10


def _run(
    run_id: str,
    generated_at: datetime,
    *,
    mode: DataMode = DataMode.LIVE,
    scenario_id: str | None = None,
    holdout: bool = False,
) -> PredictionRun:
    reasons = ("strict_holdout_evidence",) if holdout else ()
    return PredictionRun(
        run_id=run_id,
        generated_at=generated_at,
        region="india",
        mode=mode,
        feature_run_id=f"{run_id}-feat",
        feature_schema_version="environmental-v1",
        scenario_id=scenario_id,
        input_quality=PredictionInputQuality(
            supported_cells=5,
            expected_cells=5,
            unavailable_reasons=reasons,
        ),
    )


def test_retention_preserves_latest_run_active_pin() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=10)
    # Even if generated 10 days ago, if it is the only/latest run, it must be preserved!
    run = _run("latest-run", old)
    repo = FakePublicationRepository([run])
    service = PredictionRetentionService(repo)

    report = service.sweep(now=now, retention_hours=72, compact_after_hours=24)

    assert report.succeeded is True
    assert "latest-run" in report.preserved_runs
    assert "latest-run" not in report.pruned_runs
    assert "latest-run" in repo.runs


def test_retention_prunes_expired_unpinned_runs() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    active_latest = _run("run-latest", now - timedelta(hours=1))
    old_unpinned = _run("run-old", now - timedelta(hours=80))

    repo = FakePublicationRepository([active_latest, old_unpinned])
    service = PredictionRetentionService(repo)

    report = service.sweep(now=now, retention_hours=72, compact_after_hours=24)

    assert report.succeeded is True
    assert "run-old" in report.pruned_runs
    assert "run-old" not in repo.runs
    assert "run-latest" in repo.runs


def test_retention_preserves_runs_pinned_by_active_alerts() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    active_latest = _run("run-latest", now - timedelta(hours=1))
    alert_pinned = _run("run-alert-pinned", now - timedelta(hours=80))

    repo = FakePublicationRepository([active_latest, alert_pinned])
    alert_repo = FakeAlertRepository()
    alert_repo.add(
        Alert(
            h3_cell="8861892e0dfffff",
            severity=AlertSeverity.WARNING,
            message="Active warning",
            created_at=now - timedelta(hours=2),
            run_id="run-alert-pinned",
        )
    )

    service = PredictionRetentionService(repo, alert_repo)
    report = service.sweep(now=now, retention_hours=72, compact_after_hours=24)

    assert report.succeeded is True
    assert "run-alert-pinned" in report.preserved_runs
    assert "run-alert-pinned" not in report.pruned_runs
    assert "run-alert-pinned" in repo.runs


def test_retention_preserves_runs_with_audit_or_scenario_evidence() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    active_latest = _run("run-latest", now - timedelta(hours=1))
    scenario_run = _run("run-scenario", now - timedelta(hours=100), scenario_id="delhi-smog-event")
    holdout_run = _run("run-holdout", now - timedelta(hours=100), holdout=True)

    repo = FakePublicationRepository([active_latest, scenario_run, holdout_run])
    service = PredictionRetentionService(repo)

    report = service.sweep(now=now, retention_hours=72, compact_after_hours=24)

    assert "run-scenario" in report.preserved_runs
    assert "run-holdout" in report.preserved_runs
    assert repo.runs["run-scenario"] is not None
    assert repo.runs["run-holdout"] is not None


def test_retention_compacts_intermediate_runs_older_than_compact_cutoff() -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    active_latest = _run("run-latest", now - timedelta(hours=1))
    intermediate = _run("run-mid", now - timedelta(hours=36))

    repo = FakePublicationRepository([active_latest, intermediate])
    service = PredictionRetentionService(repo)

    report = service.sweep(now=now, retention_hours=72, compact_after_hours=24)

    assert report.succeeded is True
    assert "run-mid" in report.compacted_runs
    assert "run-mid" in repo.compacted
    assert repo.compacted["run-mid"] == [0.0, 1.0, 3.0, 6.0]
    assert "run-mid" in repo.runs
