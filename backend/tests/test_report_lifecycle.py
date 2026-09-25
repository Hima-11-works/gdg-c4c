"""F1: a citizen report is a claim, and only a corroborated one acts.

The brief's verification list, one section each:

* invalid and out-of-country coordinates;
* malformed notes;
* duplicate retries (and a retry that must not cost rate-limit budget twice);
* abusive bursts;
* moderation transitions, legal and illegal;
* expiry;
* report-to-model gating — the acceptance criterion that an uncorroborated
  report does not alter the plume;
* list permissions — what the public reads expose and what they must not.

Everything here runs against in-memory fakes, like the rest of the suite: there
is no local PostGIS, so the repository's SQL is exercised by
`tests/test_migrations_offline.py` and the statements test, not here. What *is*
real in these tests is the lifecycle, the geofence, the caps and the gate.

The geofence is the one input that is not faked: it loads a committed 317 KB
ADM1 asset, so "Delhi is in, Kathmandu is out" is a fact about shipped data.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import h3
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain import india
from app.domain.report_lifecycle import (
    MODEL_QUALIFIED_STATUSES,
    STATUS_TRANSITIONS,
    AuditEventKind,
    IllegalTransitionError,
    ReportStatus,
    effective_status,
    expiry_for,
    is_model_qualified,
)
from app.domain.types import FireKind, FireReport
from app.domain.types import ReportStatus as TypesReportStatus
from app.main import create_app
from app.services.fire_gradient import PlumeFireGradientModel
from app.services.reports import (
    FireReportService,
    ReportOutsideIndiaError,
    ReportRateLimitedError,
    ReviewNotConfiguredError,
    submitter_prefix,
)
from tests.fakes import FakeFireReportRepository

# Inside India (Delhi NCR) and outside it (Kathmandu), plus a sea point.
DELHI = (28.6139, 77.2090)
KATHMANDU = (27.7172, 85.3240)
AT_SEA = (18.0, 70.0)  # Arabian Sea, off the Gujarat coast

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
REVIEWER_KEY = "reviewer-secret"


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        h3_resolution=8,
        fire_report_max_age_hours=12.0,
        reports_reviewer_key=REVIEWER_KEY,
        reports_rate_limit_per_hour=5,
        reports_global_limit_per_hour=500,
        reports_cluster_window_hours=6.0,
        reports_require_india_geofence=True,
    )
    base.update(overrides)
    return Settings(**base)


def _service(repository: FakeFireReportRepository | None = None, **overrides):
    repo = repository or FakeFireReportRepository()
    return FireReportService(repo, _settings(**overrides)), repo


def _submit(
    service, *, at=NOW, client_report_id=None, where=DELHI, kind=FireKind.CROP_BURNING, host=None
):
    return service.submit(
        latitude=where[0],
        longitude=where[1],
        kind=kind,
        smoke_intensity=4,
        duration_hours=1.0,
        notes="stubble smoke",
        client_report_id=client_report_id,
        reported_at=at,
        client_host=host,
    )


# --- the geofence ----------------------------------------------------------


def test_the_geofence_asset_ships_and_covers_india() -> None:
    provenance = india.provenance()
    assert "ADM1" in provenance["basis"]
    assert provenance["license"] == "ODC-ODbL"
    assert india.is_inside_india(*DELHI) is True
    assert india.is_inside_india(*KATHMANDU) is False


def test_a_report_outside_india_is_refused() -> None:
    service, repo = _service()
    with pytest.raises(ReportOutsideIndiaError) as caught:
        _submit(service, where=KATHMANDU)
    assert caught.value.detail["code"] == "outside_india"
    # Nothing was stored: the cheapest rejection is the strictest one.
    assert repo.reports == []


def test_a_report_at_sea_is_refused() -> None:
    service, repo = _service()
    with pytest.raises(ReportOutsideIndiaError):
        _submit(service, where=AT_SEA)
    assert repo.reports == []


def test_out_of_country_coordinates_are_422_over_http() -> None:
    service, _repo = _service()
    app = create_app()
    app.dependency_overrides[
        __import__("app.api.deps", fromlist=["get_fire_report_service"]).get_fire_report_service
    ] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/v1/reports",
        json={
            "latitude": KATHMANDU[0],
            "longitude": KATHMANDU[1],
            "kind": "crop_burning",
            "smoke_intensity": 3,
            "duration_hours": 1.0,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "outside_india"


def test_the_geofence_can_be_disabled_for_fence_testing_only() -> None:
    """The switch exists, and with it off an out-of-country report is stored —
    recorded as *not* geofence-verified rather than silently claimed as checked."""
    service, repo = _service(reports_require_india_geofence=False)
    report = _submit(service, where=KATHMANDU)
    assert report.india_geofence_verified is False
    assert repo.reports[0].status is ReportStatus.SUBMITTED


# --- malformed input -------------------------------------------------------


def test_malformed_notes_are_refused_by_the_schema() -> None:
    service, repo = _service()
    app = create_app()
    app.dependency_overrides[
        __import__("app.api.deps", fromlist=["get_fire_report_service"]).get_fire_report_service
    ] = lambda: service
    client = TestClient(app)
    base = {
        "latitude": DELHI[0],
        "longitude": DELHI[1],
        "kind": "crop_burning",
        "smoke_intensity": 3,
        "duration_hours": 1.0,
    }

    too_long = client.post("/api/v1/reports", json={**base, "notes": "x" * 281})
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "validation_error"

    # Out-of-range values are refused too, rather than coerced.
    assert client.post("/api/v1/reports", json={**base, "smoke_intensity": 9}).status_code == 422
    assert client.post("/api/v1/reports", json={**base, "duration_hours": -1}).status_code == 422
    assert client.post("/api/v1/reports", json={**base, "kind": "volcano"}).status_code == 422
    assert repo.reports == []


def test_notes_are_length_checked_in_the_domain_too() -> None:
    with pytest.raises(ValueError, match="notes"):
        FireReport(
            h3_cell="x",
            latitude=DELHI[0],
            longitude=DELHI[1],
            kind=FireKind.OTHER,
            smoke_intensity=3,
            duration_hours=0.0,
            reported_at=NOW,
            notes="y" * 281,
        )


# --- idempotency and stable retry ids --------------------------------------


def test_a_duplicate_retry_returns_the_original_and_adds_no_row() -> None:
    service, repo = _service()
    first = _submit(service, client_report_id="retry-1")
    second = _submit(service, client_report_id="retry-1", at=NOW + timedelta(minutes=1))

    assert first.id == second.id
    assert len(repo.reports) == 1


def test_a_retry_does_not_consume_rate_limit_budget_twice() -> None:
    """A client on a flaky network must not be throttled for retrying.

    The idempotency check runs *before* the caps, so repeated retries of one
    report count once. Without this, a legitimate retry storm would look like
    abuse and a citizen would lose their report to a timeout.
    """
    service, _repo = _service(reports_rate_limit_per_hour=3)
    host = "203.0.113.9"
    _submit(service, client_report_id="retry-1", host=host)
    for _ in range(5):
        _submit(service, client_report_id="retry-1", at=NOW + timedelta(seconds=30), host=host)
    # The whole remaining budget is still available for distinct reports.
    _submit(service, client_report_id="retry-2", host=host)
    _submit(service, client_report_id="retry-3", host=host)
    with pytest.raises(ReportRateLimitedError):
        _submit(service, client_report_id="retry-4", host=host)


def test_without_a_client_id_every_call_adds_a_row() -> None:
    """The documented pre-existing behaviour, kept: idempotency is opt-in via
    client_report_id, so a client that wants retries must send a stable one."""
    service, repo = _service()
    _submit(service)
    _submit(service, at=NOW + timedelta(minutes=1))
    assert len(repo.reports) == 2


# --- rate and abuse control ------------------------------------------------


def test_an_abusive_burst_from_one_source_is_throttled() -> None:
    service, repo = _service(reports_rate_limit_per_hour=3)
    for index in range(3):
        # Distinct locations so clustering is not what stops it: this is the
        # per-source cap doing the work on its own.
        _submit(
            service,
            client_report_id=f"burst-{index}",
            where=(DELHI[0] + index * 0.01, DELHI[1]),
            host="198.51.100.7",
        )

    with pytest.raises(ReportRateLimitedError) as caught:
        _submit(service, client_report_id="burst-4", host="198.51.100.7")
    assert caught.value.scope == "source"
    assert caught.value.retry_after_seconds >= 1
    assert len(repo.reports) == 3


def test_the_platform_wide_cap_stops_a_many_source_flood() -> None:
    """One abusive source is capped per-source; many sources are capped together.

    Three genuinely different /24s — 198.51.100.x, 203.0.113.x and 192.0.2.x —
    because adjacent addresses in one /24 are the *same* source by design, and
    using them here would test the per-source cap a second time.
    """
    # Per-source 2 and platform-wide 2: two accepted reports fill the platform
    # budget, so the third is refused as platform-wide even though its own /24
    # has sent nothing. (Settings forbids a platform cap below the per-source
    # cap, since that could never be reached.)
    service, _repo = _service(reports_rate_limit_per_hour=2, reports_global_limit_per_hour=2)
    _submit(service, client_report_id="a", host="198.51.100.1")
    _submit(service, client_report_id="b", host="203.0.113.1")

    with pytest.raises(ReportRateLimitedError) as caught:
        _submit(service, client_report_id="c", host="192.0.2.1")
    assert caught.value.scope == "platform"


def test_rate_limit_throttles_with_429_and_retry_after_over_http() -> None:
    service, _repo = _service(reports_rate_limit_per_hour=1)
    app = create_app()
    app.dependency_overrides[
        __import__("app.api.deps", fromlist=["get_fire_report_service"]).get_fire_report_service
    ] = lambda: service
    client = TestClient(app)
    body = {
        "latitude": DELHI[0],
        "longitude": DELHI[1],
        "kind": "crop_burning",
        "smoke_intensity": 3,
        "duration_hours": 1.0,
    }
    assert client.post("/api/v1/reports", json=body).status_code == 201
    second = client.post("/api/v1/reports", json=body)
    assert second.status_code == 429
    assert second.headers["Retry-After"]
    assert second.json()["error"]["code"] == "rate_limited_source"


def test_the_submitter_prefix_is_coarse_and_never_a_full_address() -> None:
    assert submitter_prefix("198.51.100.7") == "198.51.100.0/24"
    assert submitter_prefix("2001:db8:85a3:0:0:8a2e:370:7334") == "2001:db8:85a3:0::/64"
    assert submitter_prefix(None) is None
    assert submitter_prefix("") is None
    # And the stored value is the prefix, never the address.
    service, repo = _service()
    report = _submit(service, host="198.51.100.7")
    assert report.submitter_prefix == "198.51.100.0/24"
    assert "198.51.100.7" not in json.dumps(asdict(repo.reports[0]), default=str)


# --- duplicate clustering --------------------------------------------------


def test_a_second_report_of_the_same_event_becomes_corroboration() -> None:
    service, repo = _service()
    first = _submit(service, client_report_id="c1")
    second = _submit(service, client_report_id="c2", at=NOW + timedelta(minutes=10))

    assert first.cluster_id == second.cluster_id
    assert first.corroborating_report_count == 0
    assert second.corroborating_report_count == 1
    # The sibling's own record is updated, not just the new one.
    assert repo.get(first.id).corroborating_report_count == 1


def test_clustering_is_scoped_to_cell_kind_and_window() -> None:
    service, _repo = _service()
    base = _submit(service, client_report_id="k1")
    same_event = _submit(service, client_report_id="k2", at=NOW + timedelta(minutes=5))
    assert same_event.cluster_id == base.cluster_id

    # A different kind in the same cell is a different claim, not corroboration.
    other_kind = _submit(
        service, client_report_id="k3", at=NOW + timedelta(minutes=6), kind=FireKind.BUILDING_FIRE
    )
    assert other_kind.cluster_id != base.cluster_id
    assert other_kind.corroborating_report_count == 0

    # Outside the cluster window it starts a fresh cluster.
    later = _submit(service, client_report_id="k4", at=NOW + timedelta(hours=7))
    assert later.cluster_id != base.cluster_id


def test_clustering_records_an_audit_event_on_both_reports() -> None:
    service, _repo = _service()
    first = _submit(service, client_report_id="a1")
    second = _submit(service, client_report_id="a2", at=NOW + timedelta(minutes=2))
    kinds = [event.kind for event in service._repository.list_events(first.id)]
    assert AuditEventKind.CLUSTERED in kinds
    assert AuditEventKind.SUBMITTED in [e.kind for e in service._repository.list_events(second.id)]


# --- the lifecycle ---------------------------------------------------------


def test_a_submitted_report_is_a_claim_that_cannot_act() -> None:
    report = _submit(_service()[0])
    assert report.status is ReportStatus.SUBMITTED
    assert is_model_qualified(report.status, expires_at=report.expires_at, now=NOW) is False


def test_only_corroborated_reports_are_model_qualified() -> None:
    assert MODEL_QUALIFIED_STATUSES == frozenset({ReportStatus.CORROBORATED})
    for status in ReportStatus:
        expected = status is ReportStatus.CORROBORATED
        assert is_model_qualified(status, now=NOW) is expected


def test_moderation_transitions_are_legal_and_audited() -> None:
    service, _repo = _service()
    report = _submit(service, client_report_id="m1")

    updated = service.moderate(
        report.id,
        target_status=ReportStatus.UNDER_REVIEW,
        actor=" reviewer-7 ",
        note="picked up for review",
        now=NOW + timedelta(minutes=5),
    )
    assert updated.status is ReportStatus.UNDER_REVIEW
    assert updated.reviewed_by == "reviewer-7"
    assert updated.reviewed_at == NOW + timedelta(minutes=5)

    final = service.moderate(
        report.id,
        target_status=ReportStatus.CORROBORATED,
        actor="reviewer-7",
        note="matched a FIRMS detection in the same cell",
        now=NOW + timedelta(minutes=9),
    )
    assert final.status is ReportStatus.CORROBORATED

    events = service.list_audit(report.id)
    assert [event["kind"] for event in events] == [
        "submitted",
        "status_changed",
        "status_changed",
    ]
    assert events[-1]["from_status"] == "under_review"
    assert events[-1]["to_status"] == "corroborated"
    assert events[-1]["actor"] == "reviewer-7"
    assert "FIRMS" in events[-1]["note"]


def test_an_illegal_transition_is_refused() -> None:
    service, _repo = _service()
    report = _submit(service, client_report_id="i1")
    service.moderate(
        report.id,
        target_status=ReportStatus.EXPIRED,
        actor="system",
        note="window closed",
        now=NOW,
    )
    with pytest.raises(IllegalTransitionError):
        # expired is terminal: nothing revives it.
        service.moderate(
            report.id,
            target_status=ReportStatus.CORROBORATED,
            actor="reviewer-1",
            note="too late",
            now=NOW,
        )


def test_every_status_has_a_meaning_and_a_transition_rule() -> None:
    from app.domain.report_lifecycle import STATUS_MEANING

    for status in ReportStatus:
        assert STATUS_MEANING[status].strip()
        assert status in STATUS_TRANSITIONS


def test_a_status_change_without_a_note_is_refused_at_the_audit_layer() -> None:
    from app.domain.report_lifecycle import ReportAuditEvent

    with pytest.raises(ValueError, match="note or a detail"):
        ReportAuditEvent(
            report_id=1,
            kind=AuditEventKind.STATUS_CHANGED,
            at=NOW,
            from_status=ReportStatus.SUBMITTED,
            to_status=ReportStatus.CORROBORATED,
            actor="reviewer-1",
        )


def test_a_non_submitted_report_must_name_its_reviewer() -> None:
    with pytest.raises(ValueError, match="reviewed_by"):
        FireReport(
            h3_cell="x",
            latitude=DELHI[0],
            longitude=DELHI[1],
            kind=FireKind.OTHER,
            smoke_intensity=3,
            duration_hours=0.0,
            reported_at=NOW,
            status=TypesReportStatus.CORROBORATED,
        )


# --- expiry ----------------------------------------------------------------


def test_expiry_is_fixed_at_submission_from_the_model_window() -> None:
    service, _repo = _service(fire_report_max_age_hours=12.0)
    report = _submit(service)
    assert report.expires_at == expiry_for(NOW, 12.0)
    assert report.expires_at == NOW + timedelta(hours=12)


def test_an_aged_out_claim_is_reported_expired_even_before_the_sweep() -> None:
    """The read side must never imply a stale claim is still actionable."""
    service, _repo = _service()
    report = _submit(service)
    later = NOW + timedelta(hours=13)
    view = service.report_detail(report.id, now=later)
    assert view["status"] == "expired"
    assert view["affects_air_quality_model"] is False
    assert effective_status(report.status, report.expires_at, now=later) is ReportStatus.EXPIRED


def test_the_sweep_persists_expired_and_audits_it() -> None:
    service, repo = _service(fire_report_max_age_hours=1.0)
    report = _submit(service)

    assert service.expire_due(now=NOW + timedelta(minutes=30)) == []  # still open
    expired = service.expire_due(now=NOW + timedelta(hours=2))
    assert [row.id for row in expired] == [report.id]
    assert repo.get(report.id).status is ReportStatus.EXPIRED
    assert repo.get(report.id).reviewed_by == "system"
    events = service.list_audit(report.id)
    assert events[-1]["kind"] == "expired"
    assert events[-1]["actor"] == "system"
    # Idempotent: a second sweep has nothing left to do.
    assert service.expire_due(now=NOW + timedelta(hours=3)) == []


# --- report-to-model gating (the acceptance criterion) ---------------------


def _model() -> PlumeFireGradientModel:
    return PlumeFireGradientModel(
        source_pm25_ugm3=180.0,
        plume_radius_km=1.5,
        decay_half_life_hours=4.0,
        max_age_hours=12.0,
    )


def test_an_uncorroborated_report_does_not_alter_the_model() -> None:
    """The criterion, end to end: submit a report, run the plume model on what
    came out of the repository, and the modeled field must not move."""
    service, repo = _service()
    report = _submit(service, client_report_id="g1")
    cell = h3.latlng_to_cell(DELHI[0], DELHI[1], 8)

    qualified = repo.list_active_qualified(since=NOW - timedelta(hours=12))
    assert qualified == []
    assert _model().contributions([cell], qualified, timestamp=NOW) == [0.0]

    # And even if a caller ignores the filter, the model refuses it.
    assert _model().contributions([cell], [report], timestamp=NOW) == [0.0]


def test_a_corroborated_report_does_alter_the_model() -> None:
    service, repo = _service()
    report = _submit(service, client_report_id="g2")
    service.moderate(
        report.id,
        target_status=ReportStatus.CORROBORATED,
        actor="reviewer-1",
        note="corroborated by a satellite detection",
        now=NOW + timedelta(minutes=1),
    )
    cell = h3.latlng_to_cell(DELHI[0], DELHI[1], 8)

    qualified = repo.list_active_qualified(since=NOW - timedelta(hours=12))
    assert [row.id for row in qualified] == [report.id]
    contributions = _model().contributions([cell], qualified, timestamp=NOW)
    assert contributions[0] > 0.0


def test_a_rejected_report_never_alters_the_model() -> None:
    service, repo = _service()
    report = _submit(service, client_report_id="g3")
    service.moderate(
        report.id,
        target_status=ReportStatus.REJECTED,
        actor="reviewer-1",
        note="no supporting evidence; refused",
        now=NOW + timedelta(minutes=1),
    )
    cell = h3.latlng_to_cell(DELHI[0], DELHI[1], 8)
    assert repo.list_active_qualified(since=NOW - timedelta(hours=12)) == []
    assert _model().contributions([cell], [report], timestamp=NOW + timedelta(minutes=2)) == [0.0]


# --- the same criterion through the pipeline's own code path --------------


class _FixedPDI:
    """A PDIModel that returns a constant, so the only thing that can move the
    modeled PM2.5 in this test is the fire-report contribution."""

    def calculate(self, cell_context):  # noqa: ANN001, ANN201 - test double
        from app.domain.pdi import PDIResult

        return PDIResult(h3_cell=cell_context.h3_cell, pdi=50.0, factors={"pm25": 0.5})


class _FixedEstimator:
    """A PollutionEstimator that returns a flat 50 ug/m3 for every cell.

    The IDW maths has its own tests; here it would only obscure the thing under
    test, which is whether a report may move the field at all.
    """

    def estimate(self, grid, sensor_readings, *, timestamp):  # noqa: ANN001, ANN201
        from app.domain.types import GridState

        return [
            GridState(h3_cell=cell, pm25=50.0, timestamp=timestamp, confidence=0.9) for cell in grid
        ]


def test_the_pipeline_grid_step_reflects_a_review_decision() -> None:
    """The acceptance criterion as the pipeline itself sees it.

    The tests above call the plume model directly. This one goes through
    `GridComputationService` - the object `app/pipeline/run.py` actually calls -
    so the "before and after the next pipeline run" claim is exercised over the
    real code path: submit, compute the grid, review, compute again.

    The only substitute is the storage driver (there is no local PostGIS in this
    environment), and it filters by status exactly as
    `SqlFireReportRepository.list_active_qualified` does.
    """
    from app.domain.types import PM25, BoundingBox, SensorReading
    from app.services.geospatial import GeospatialService
    from app.services.grid_computation import GridComputationService
    from tests.fakes import FakeGridStateRepository, FakeSensorReadingRepository

    service, repo = _service()
    cell = h3.latlng_to_cell(DELHI[0], DELHI[1], 8)
    report = _submit(service, client_report_id="pipe-1")

    sensors = FakeSensorReadingRepository()
    sensors.add(
        SensorReading(
            source="openaq",
            external_sensor_id="s-1",
            latitude=DELHI[0],
            longitude=DELHI[1],
            pollutant=PM25,
            value=50.0,
            unit="ug/m3",
            measured_at=NOW - timedelta(hours=1),
        )
    )
    grid_repo = FakeGridStateRepository()

    def compute():
        service_under_test = GridComputationService(
            _FixedEstimator(),
            _FixedPDI(),
            GeospatialService(resolution=8),
            sensors,
            grid_repo,
            fire_gradient=_model(),
            fire_repository=repo,
        )
        result = service_under_test.run(
            BoundingBox(
                min_lat=DELHI[0] - 0.01,
                min_lon=DELHI[1] - 0.01,
                max_lat=DELHI[0] + 0.01,
                max_lon=DELHI[1] + 0.01,
            ),
            timestamp=NOW,
            sensor_max_age=timedelta(hours=3),
        )
        assert result.succeeded is True
        return result

    # 1. The pipeline run with the report as an unreviewed claim.
    before = compute()
    baseline_pm25 = {state.h3_cell: state.pm25 for state in before.states}[cell]
    assert baseline_pm25 == pytest.approx(50.0), "sensor-only estimate to start from"
    assert {state.h3_cell: state.pm25 for state in before.states}[cell] == pytest.approx(50.0)

    # 2. A reviewer corroborates it.
    service.moderate(
        report.id,
        target_status=ReportStatus.CORROBORATED,
        actor="reviewer-1",
        note="matched a FIRMS detection in the same cell",
        now=NOW + timedelta(minutes=1),
    )

    # 3. The next pipeline run reflects the decision.
    after = compute()
    assert {state.h3_cell: state.pm25 for state in after.states}[cell] > baseline_pm25
    assert before.fires_used == 0
    assert after.fires_used == 1


# --- list permissions and what the reads expose ---------------------------


def test_the_public_detail_hides_reviewer_identity_and_notes() -> None:
    service, _repo = _service()
    report = _submit(service, client_report_id="p1")
    service.moderate(
        report.id,
        target_status=ReportStatus.CORROBORATED,
        actor="reviewer-7",
        note="matched a FIRMS detection",
        now=NOW,
    )

    detail = service.report_detail(report.id, now=NOW)
    serialised = json.dumps(detail)
    assert "reviewer-7" not in serialised
    assert "matched a FIRMS" not in serialised
    assert "reviewed_by" not in detail
    assert detail["status"] == "corroborated"
    assert detail["is_verified"] is True
    assert detail["affects_air_quality_model"] is True


def test_the_audit_read_is_gated_and_names_the_reviewer() -> None:
    service, _repo = _service()
    report = _submit(service, client_report_id="p2")
    service.moderate(
        report.id,
        target_status=ReportStatus.REJECTED,
        actor="reviewer-9",
        note="duplicate of an existing report",
        now=NOW,
    )
    # A configured key means a missing or wrong one is 401, not 503.
    with pytest.raises(PermissionError):
        service.require_reviewer(None)
    with pytest.raises(PermissionError):
        service.require_reviewer("wrong")
    assert service.require_reviewer(REVIEWER_KEY) == REVIEWER_KEY
    events = service.list_audit(report.id)
    assert any(event["actor"] == "reviewer-9" for event in events)


def test_review_is_off_when_no_key_is_configured_not_open() -> None:
    service, _repo = _service(reports_reviewer_key=None)
    with pytest.raises(ReviewNotConfiguredError, match="not configured"):
        service.require_reviewer("anything")


def test_review_endpoints_are_503_when_unconfigured_and_401_when_wrong() -> None:
    service, _repo = _service(reports_reviewer_key=None)
    app = create_app()
    app.dependency_overrides[
        __import__("app.api.deps", fromlist=["get_fire_report_service"]).get_fire_report_service
    ] = lambda: service
    client = TestClient(app)
    report = _submit(service, client_report_id="p3")
    body = {"status": "corroborated", "actor": "reviewer-1", "note": "checked"}

    unconfigured = client.post(f"/api/v1/reports/{report.id}/moderation", json=body)
    assert unconfigured.status_code == 503
    assert unconfigured.json()["error"]["code"] == "review_not_configured"

    service._settings = _settings()
    wrong = client.post(
        f"/api/v1/reports/{report.id}/moderation", json=body, headers={"X-Reviewer-Key": "nope"}
    )
    assert wrong.status_code == 401


def test_the_status_vocabulary_is_served_so_clients_need_not_hardcode_it() -> None:
    service, _repo = _service()
    result = service.list_statuses()
    rows = {row["status"]: row for row in result.data}
    assert set(rows) == {status.value for status in ReportStatus}
    assert rows["submitted"]["affects_air_quality_model"] is False
    assert rows["corroborated"]["affects_air_quality_model"] is True
    assert "unverified" in rows["submitted"]["meaning"]


# --- F2 linkage without making images mandatory ---------------------------


def test_a_report_with_no_evidence_is_a_first_class_claim() -> None:
    service, _repo = _service()
    report = _submit(service, client_report_id="f1")
    assert report.evidence_count == 0
    detail = service.report_detail(report.id, now=NOW)
    assert detail["evidence_count"] == 0
    # Explicitly not required: a photo is never a precondition for validity.
    assert detail["evidence_expected"] is False
    # And evidence is not an input to qualification.
    assert is_model_qualified(report.status, expires_at=report.expires_at, now=NOW) is False
    # A reviewer may still corroborate it on other grounds.
    updated = service.moderate(
        report.id,
        target_status=ReportStatus.CORROBORATED,
        actor="reviewer-1",
        note="corroborated by two nearby reports; no photo supplied",
        now=NOW,
    )
    assert updated.evidence_count == 0
    assert is_model_qualified(updated.status, expires_at=updated.expires_at, now=NOW) is True


def test_the_submission_contract_is_unchanged() -> None:
    """The v1 POST response keys are a locked contract (tests/test_api_reports.py
    asserts them verbatim). F1 adds validation and a lifecycle; it must not
    reshape the response an existing client parses."""
    service, _repo = _service()
    app = create_app()
    app.dependency_overrides[
        __import__("app.api.deps", fromlist=["get_fire_report_service"]).get_fire_report_service
    ] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/v1/reports",
        json={
            "latitude": DELHI[0],
            "longitude": DELHI[1],
            "kind": "crop_burning",
            "smoke_intensity": 4,
            "duration_hours": 1.5,
            "client_report_id": "shape-1",
        },
    )
    assert response.status_code == 201
    assert set(response.json()["data"].keys()) == {
        "id",
        "h3_cell",
        "latitude",
        "longitude",
        "kind",
        "smoke_intensity",
        "duration_hours",
        "notes",
        "client_report_id",
        "reported_at",
    }


def test_the_v2_list_carries_status_per_row() -> None:
    """A client rendering a list needs status on every row, not N detail calls."""
    service, _repo = _service()
    app = create_app()
    app.dependency_overrides[
        __import__("app.api.deps", fromlist=["get_fire_report_service"]).get_fire_report_service
    ] = lambda: service
    client = TestClient(app)
    client.post(
        "/api/v1/reports",
        json={
            "latitude": DELHI[0],
            "longitude": DELHI[1],
            "kind": "crop_burning",
            "smoke_intensity": 4,
            "duration_hours": 1.0,
            "client_report_id": "v2-1",
        },
    )

    response = client.get("/api/v2/reports")
    assert response.status_code == 200
    [row] = response.json()["data"]
    assert row["status"] == "submitted"
    assert row["affects_air_quality_model"] is False
    assert row["is_verified"] is False
    assert "reported_at" in row and "expires_at" in row
