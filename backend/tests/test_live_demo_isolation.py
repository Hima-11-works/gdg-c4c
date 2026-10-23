from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from app.db.repositories.alert import _list_active_stmt as alert_query
from app.db.repositories.fire_report import _list_active_qualified_stmt as report_query
from app.db.repositories.forecast import _latest_for_horizon_stmt as forecast_query
from app.db.repositories.grid_state import _latest_stmt as grid_query
from app.domain.features import DataMode
from app.domain.prediction import PredictionRun
from app.services.prediction_queries import PredictionQueryService


class _PublicationRepository:
    def __init__(self, runs=()):
        self.runs = list(runs)

    def get_run(self, run_id):
        return next((run for run in self.runs if run.run_id == run_id), None)

    def latest_run(self, *, region=None, mode=None):
        candidates = [
            run for run in self.runs
            if (region is None or run.region == region)
            and (mode is None or run.mode is mode)
        ]
        return max(candidates, key=lambda run: run.generated_at) if candidates else None


def _run(run_id: str, mode: DataMode) -> PredictionRun:
    return PredictionRun(
        run_id=run_id,
        generated_at=datetime(2026, 10, 23, tzinfo=UTC),
        region="india",
        mode=mode,
        feature_run_id=f"{run_id}-features",
        feature_schema_version="environmental-v1",
    )


def test_live_empty_database_never_creates_a_synthetic_publication() -> None:
    service = PredictionQueryService(_PublicationRepository(), demo_mode=False)

    with pytest.raises(ValueError, match="no live prediction run"):
        service.run()

    demo_service = PredictionQueryService(_PublicationRepository(), demo_mode=True)
    assert demo_service.run().run_id.startswith("demo-fallback-")


def test_live_api_rejects_an_explicit_persisted_demo_run_id() -> None:
    service = PredictionQueryService(
        _PublicationRepository([_run("old-demo", DataMode.DEMO)]), demo_mode=False
    )

    with pytest.raises(ValueError, match="not available in live mode"):
        service.run("old-demo")


def test_legacy_environmental_reads_are_pinned_to_publication_mode() -> None:
    statements = (
        grid_query(DataMode.LIVE),
        forecast_query(1.0, DataMode.LIVE),
        alert_query(datetime(2026, 10, 22, tzinfo=UTC), DataMode.LIVE),
    )
    for statement in statements:
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled).lower()
        assert "prediction_run" in sql
        assert "mode" in sql
        assert "live" in compiled.params.values()


def test_live_model_input_query_excludes_seeded_demo_reports() -> None:
    compiled = report_query(
        datetime(2026, 10, 22, tzinfo=UTC), include_demo_reports=False
    ).compile(dialect=postgresql.dialect())

    assert "client_report_id" in str(compiled)
    assert "not like" in str(compiled).lower()
    assert "demo-fire-%" in compiled.params.values()
