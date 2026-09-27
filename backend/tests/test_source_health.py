"""F3: per-source health, explicit fallback/staleness, and non-fatal seeding.

Three behaviours are pinned here, and each corresponds to a specific way the
pipeline used to be dishonest by omission:

1. `classify` distinguishes a source that was never asked from one that was
   asked and had nothing, and from one that errored. All three used to look the
   same in a row count.
2. The v2 envelope says out loud that it is a fallback and why, rather than
   serving a plausible-looking synthesised response under a run id that rolls
   over hourly and looks like a healthy pipeline.
3. A rate-limited demo seed cannot fail the forecast run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.repositories.source_health import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_PRESENT,
    STATUS_STALE,
    SourceHealth,
    classify,
)
from app.domain.prediction import DataMode, PredictionRun
from app.services.prediction_queries import (
    STALE_RUN_SECONDS,
    PredictionQueryService,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _run(
    *,
    run_id: str = "run-20260101T0000Z",
    age_seconds: float = 0.0,
) -> PredictionRun:
    return PredictionRun(
        run_id=run_id,
        generated_at=NOW - timedelta(seconds=age_seconds),
        region="in-asia",
        mode=DataMode.DEMO,
        feature_run_id="feat-1",
        feature_schema_version="1",
    )


def _fallback_run(age_seconds: float = 0.0) -> PredictionRun:
    """What the read path actually mints when nothing is published."""
    return _run(run_id="demo-fallback-20260101T12Z", age_seconds=age_seconds)


class _StubPublicationRepo:
    def __init__(self, run: PredictionRun | None = None) -> None:
        self._run = run

    def get_latest_run(self, *, region: str | None = None) -> PredictionRun | None:
        return self._run


class _StubHealthRepo:
    """The two-line stub the repository protocol exists to allow."""

    def __init__(self, rows: list[SourceHealth]) -> None:
        self._rows = rows

    def list_latest_run(self) -> list[SourceHealth]:
        return self._rows


def _health(dataset_id: str, status: str, item_count: int) -> SourceHealth:
    return SourceHealth(
        dataset_id=dataset_id,
        status=status,
        item_count=item_count,
        latency_ms=None,
        fetched_at=NOW,
        error_summary=None,
    )


# --- classify -------------------------------------------------------------
# The ordering is the contract, so these are not "one test per branch" for its
# own sake: each pins a distinction that a row count cannot make.


def test_never_called_is_missing_not_empty() -> None:
    # The important one: "we did not ask" and "we asked and got nothing" are
    # different facts and must not collapse into the same word.
    assert classify(item_count=0, called=False) == STATUS_MISSING
    assert classify(item_count=0, called=True) == STATUS_EMPTY
    assert STATUS_MISSING != STATUS_EMPTY


def test_failure_wins_over_data() -> None:
    # Partial success that also errored is `failed`, not `present`. Reporting
    # it as present is the silence F3 exists to remove.
    assert classify(item_count=500, failed=True) == STATUS_FAILED
    assert classify(item_count=0, failed=True) == STATUS_FAILED


def test_stale_with_rows_is_stale() -> None:
    assert classify(item_count=10, stale=True) == STATUS_STALE
    assert classify(item_count=10) == STATUS_PRESENT


def test_every_status_is_a_real_word() -> None:
    # Guards against a status being added to classify() but not to the enum the
    # route and the DB constraint agree on.
    assert {STATUS_PRESENT, STATUS_EMPTY, STATUS_STALE, STATUS_MISSING, STATUS_FAILED} == {
        "present",
        "empty",
        "stale",
        "missing",
        "failed",
    }


# --- envelope: fallback disclosure ---------------------------------------


def test_synthesised_run_is_flagged_as_a_fallback() -> None:
    service = PredictionQueryService(_StubPublicationRepo())
    assert service.is_fallback(_fallback_run()) is True


def test_published_run_is_not_a_fallback() -> None:
    service = PredictionQueryService(_StubPublicationRepo())
    assert service.is_fallback(_run()) is False
    assert service.fallback_reason(_run()) is None


def test_fallback_reason_says_it_is_not_a_publication() -> None:
    # The reason has to state the fact in words a reader can act on, not merely
    # be a non-null string.
    service = PredictionQueryService(_StubPublicationRepo())
    reason = service.fallback_reason(_fallback_run())
    assert reason is not None
    assert "not a publication" in reason


# --- envelope: freshness -------------------------------------------------


def test_staleness_boundary_is_the_shared_24h_budget() -> None:
    service = PredictionQueryService(_StubPublicationRepo())

    just_inside = service.age_seconds(
        _run(age_seconds=STALE_RUN_SECONDS - 60), now=NOW
    )
    assert service.is_stale(_run(age_seconds=STALE_RUN_SECONDS - 60), now=NOW) is False

    just_outside = _run(age_seconds=STALE_RUN_SECONDS + 60)
    assert service.is_stale(just_outside, now=NOW) is True
    assert just_inside < STALE_RUN_SECONDS


def test_age_is_clamped_at_zero_on_clock_skew() -> None:
    # A run generated in the future is a clock problem, not a negative age that
    # would render as a staleness bug.
    service = PredictionQueryService(_StubPublicationRepo())
    future = _run(age_seconds=-500)
    assert service.age_seconds(future, now=NOW) == 0.0
    assert service.is_stale(future, now=NOW) is False


def test_stale_fallback_is_still_a_fallback() -> None:
    # Orthogonal facts: a run can be both synthesised and old, and both must be
    # reported. Collapsing them would lose one.
    service = PredictionQueryService(_StubPublicationRepo())
    old = _fallback_run(age_seconds=STALE_RUN_SECONDS + 3600)
    assert service.is_fallback(old) is True
    assert service.is_stale(old, now=NOW) is True


# --- envelope: source health ---------------------------------------------


def test_source_health_degrades_to_empty_when_unwired() -> None:
    # Health is a diagnostic; a service built without the repo must return an
    # empty list rather than crash the envelope.
    service = PredictionQueryService(_StubPublicationRepo())
    assert service.source_health() == []


def test_source_health_is_passed_through_when_present() -> None:
    rows = [_health("openaq", STATUS_PRESENT, 5), _health("open-meteo", STATUS_EMPTY, 0)]
    service = PredictionQueryService(
        _StubPublicationRepo(), source_health=_StubHealthRepo(rows)
    )
    assert service.source_health() == rows


def test_a_failed_source_is_visible_rather_than_absent() -> None:
    # The regression this whole table exists for: an outage that produced no
    # rows used to be indistinguishable from a source that was never asked.
    rows = [_health("openaq", STATUS_FAILED, 0)]
    service = PredictionQueryService(
        _StubPublicationRepo(), source_health=_StubHealthRepo(rows)
    )
    [reported] = service.source_health()
    assert reported.status == STATUS_FAILED
    assert reported.status != STATUS_MISSING


# --- pipeline resilience --------------------------------------------------


class _UnusedSession:
    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"seeding should not touch the session, tried {name!r}")


@pytest.mark.asyncio
async def test_rate_limited_demo_seed_does_not_abort_the_run(monkeypatch) -> None:
    """F1's submission cap must not be able to kill the forecast run.

    The cap is per /24 per egress address, so on a shared address (a CI runner,
    or a developer who has just been submitting reports) two rows of demo data
    used to abort the entire hourly pipeline with "Fatal: pipeline aborted" -
    after the grid, the forecast and the alerts had all already succeeded.
    """
    from app.core.config import Settings
    from app.pipeline.run import _seed_fire_reports
    from app.services.reports import ReportRateLimitedError

    class _RateLimited:
        def submit(self, **_: object) -> None:
            raise ReportRateLimitedError(
                "at most 5 report(s) per hour from one network; the report was not stored",
                retry_after_seconds=3600,
                scope="/24",
            )

    monkeypatch.setattr("app.pipeline.run.FireReportService", lambda *a, **k: _RateLimited())

    outcome = _seed_fire_reports(_UnusedSession(), Settings(demo_mode=True), NOW)

    # Succeeded, because the run itself is fine - and the skip is visible in the
    # summary, because a silent skip is the thing being fixed here.
    assert outcome.succeeded is True
    assert "skipped" in outcome.summary
    assert "at most 5" in outcome.summary


@pytest.mark.asyncio
async def test_live_mode_seeds_nothing_and_reports_it(monkeypatch) -> None:
    from app.core.config import Settings
    from app.pipeline.run import _seed_fire_reports

    def _explode(*_a: object, **_k: object) -> None:
        raise AssertionError("live mode must not construct a report service at all")

    monkeypatch.setattr("app.pipeline.run.FireReportService", _explode)

    outcome = _seed_fire_reports(_UnusedSession(), Settings(demo_mode=False), NOW)
    assert outcome.succeeded is True
    assert "nothing seeded" in outcome.summary


# --- publication ----------------------------------------------------------


class _ExplodingGridRepo:
    """Fails the way a real one does when the database is unreachable."""

    def __init__(self, session: object = None) -> None:
        pass

    def latest(self) -> list[object]:
        raise RuntimeError("connection reset by peer")


def test_publication_failure_is_a_failed_stage_not_a_crash(monkeypatch) -> None:
    """A publication failure must not throw away the rest of the run.

    The grid, the forecast and the alerts above this stage are already
    persisted and still useful. Letting publication raise would abort the
    process, which both breaks the module's own contract (no stage failure
    aborts the run) and discards a successful run's work over the one step
    that only affects the v2 read path.
    """
    from app.core.config import Settings
    from app.pipeline.run import _publish

    monkeypatch.setattr("app.pipeline.run.SqlGridStateRepository", _ExplodingGridRepo)

    outcome = _publish(
        _UnusedSession(), Settings(demo_mode=True), "run-x", NOW, forecasts=[]
    )

    assert outcome.name == "publication"
    assert outcome.succeeded is False
    # The reason has to reach the operator, not just flip a boolean.
    assert "connection reset by peer" in outcome.summary
    assert "run-x" in outcome.summary


def test_two_executions_get_distinct_run_ids() -> None:
    """Run identity is per execution, because a published run is immutable.

    A published run cannot be overwritten: re-publishing an id with different
    content raises. So an id derived from the hour collides on the first retry,
    because the retry recomputes from fresher readings. Distinct ids per
    execution is what keeps a retry from aborting the pipeline.
    """
    from app.pipeline.run import _new_run_id

    first = _new_run_id(NOW)
    second = _new_run_id(NOW)

    assert first != second
    # Still sortable and recognisable in a log.
    assert first.startswith("run-20260101T120000Z-")
    assert first.rsplit("-", 1)[1]


def test_run_ids_sort_chronologically() -> None:
    from app.pipeline.run import _new_run_id

    earlier = _new_run_id(NOW)
    later = _new_run_id(NOW + timedelta(minutes=1))

    assert earlier < later


# --- alert pinning --------------------------------------------------------


def test_alerts_are_pinned_to_the_run_that_raised_them() -> None:
    """An alert must name the run that produced it, or it cannot be traced."""
    from app.core.config import Settings
    from app.services.alert_generation import AlertGenerationService
    from app.domain.types import AlertSeverity, GridState, Forecast

    class _RecordingAlerts:
        def __init__(self) -> None:
            self.saved: list[object] = []

        def list_active(self, *, since: datetime) -> list[object]:
            return []

        def add(self, alert: object) -> object:
            self.saved.append(alert)
            return alert

    settings = Settings()
    repo = _RecordingAlerts()
    service = AlertGenerationService(
        repo,
        warning_threshold_ugm3=settings.alert_warning_threshold_ugm3,
        critical_threshold_ugm3=settings.alert_critical_threshold_ugm3,
        sharp_increase_threshold_ugm3=settings.alert_sharp_increase_threshold_ugm3,
        pdi_high_threshold=settings.alert_pdi_high_threshold,
        pdi_worsening_min_increase_ugm3=settings.alert_pdi_worsening_min_increase_ugm3,
        active_lookback=timedelta(hours=settings.alert_active_lookback_hours),
    )

    # A cell well over the critical threshold raises on current state alone, so
    # this does not depend on the forecast path.
    service.run(
        [
            GridState(
                h3_cell="8928308280fffff",
                timestamp=NOW,
                confidence=0.9,
                pm25=settings.alert_critical_threshold_ugm3 + 40.0,
            )
        ],
        [],
        generated_at=NOW,
        run_id="run-pinned-0001",
    )

    assert repo.saved, "expected the over-threshold cell to raise an alert"
    for alert in repo.saved:
        assert alert.severity is AlertSeverity.CRITICAL
        assert alert.run_id == "run-pinned-0001"


def test_an_alert_raised_outside_a_run_is_unpinned_not_invented() -> None:
    """A null run_id is honest; a made-up one is a lie about provenance."""
    from app.core.config import Settings
    from app.services.alert_generation import AlertGenerationService
    from app.domain.types import GridState

    class _RecordingAlerts:
        def __init__(self) -> None:
            self.saved: list[object] = []

        def list_active(self, *, since: datetime) -> list[object]:
            return []

        def add(self, alert: object) -> object:
            self.saved.append(alert)
            return alert

    settings = Settings()
    repo = _RecordingAlerts()
    service = AlertGenerationService(
        repo,
        warning_threshold_ugm3=settings.alert_warning_threshold_ugm3,
        critical_threshold_ugm3=settings.alert_critical_threshold_ugm3,
        sharp_increase_threshold_ugm3=settings.alert_sharp_increase_threshold_ugm3,
        pdi_high_threshold=settings.alert_pdi_high_threshold,
        pdi_worsening_min_increase_ugm3=settings.alert_pdi_worsening_min_increase_ugm3,
        active_lookback=timedelta(hours=settings.alert_active_lookback_hours),
    )

    service.run(
        [
            GridState(
                h3_cell="8928308280fffff",
                timestamp=NOW,
                confidence=0.9,
                pm25=settings.alert_critical_threshold_ugm3 + 40.0,
            )
        ],
        [],
        generated_at=NOW,
    )

    assert repo.saved
    for alert in repo.saved:
        assert alert.run_id is None

