"""Tests for the pipeline-level logic in app.pipeline.run that doesn't
need a database or network: aggregating per-stage outcomes into an
overall result, and the sensor-ingestion stage's early exit when
OPENAQ_API_KEY isn't configured. Each stage's actual orchestration logic
is tested in isolation elsewhere (test_ingestion_service.py,
test_grid_computation_service.py, test_forecasting_service.py,
test_alert_generation_service.py) — this file covers only what's unique
to app.pipeline.run itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.domain.types import BoundingBox
from app.pipeline.run import PipelineReport, StageOutcome, _ingest_sensors

GENERATED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BBOX = BoundingBox(min_lat=37.6, min_lon=-122.6, max_lat=37.9, max_lon=-122.1)


def _settings(**overrides) -> Settings:
    defaults = dict(postgres_user="u", postgres_password="p", postgres_db="d")
    defaults.update(overrides)
    return Settings(**defaults)


# --- PipelineReport aggregation ---


def test_report_succeeds_only_when_every_stage_succeeds() -> None:
    all_ok = PipelineReport(
        stages=[
            StageOutcome("a", True, "fine"),
            StageOutcome("b", True, "fine"),
        ]
    )
    assert all_ok.succeeded is True


def test_report_fails_if_any_single_stage_fails() -> None:
    one_bad = PipelineReport(
        stages=[
            StageOutcome("a", True, "fine"),
            StageOutcome("b", False, "boom"),
            StageOutcome("c", True, "fine"),
        ]
    )
    assert one_bad.succeeded is False


def test_report_with_no_stages_is_vacuously_successful() -> None:
    assert PipelineReport(stages=[]).succeeded is True


# --- sensor ingestion stage: missing API key ---


async def test_ingest_sensors_without_an_api_key_is_a_clear_failure_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # demo_mode is pinned explicitly (not just left at Settings' False
    # default): this test reads the real repo-root .env via Settings'
    # env_file, and DEMO_MODE=true there (a valid thing for a developer to
    # have set locally) would otherwise silently change which branch of
    # _ingest_sensors this test actually exercises.
    settings = _settings(openaq_api_key=None, demo_mode=False)

    # F3: the unconfigured path is the clearest case of "a source that was
    # never asked" - the API returned nothing *because nobody called it*. It
    # records `missing` rather than `empty`, and the distinction is only
    # meaningful if it is asserted somewhere, so it is asserted here.
    recorded: list[dict[str, object]] = []

    class _RecordingHealthRepo:
        def __init__(self, session: object) -> None:
            pass

        def record(self, **kwargs: object) -> None:
            recorded.append(kwargs)

    monkeypatch.setattr(
        "app.db.repositories.source_health.SqlSourceHealthRepository", _RecordingHealthRepo
    )

    outcome = await _ingest_sensors(
        None,  # type: ignore[arg-type]
        settings,
        BBOX,
        GENERATED_AT,
        "run-test",
    )

    assert outcome.name == "sensor_ingestion"
    assert outcome.succeeded is False
    assert "OPENAQ_API_KEY" in outcome.summary

    assert len(recorded) == 1
    [call] = recorded
    assert call["dataset_id"] == "openaq"
    assert call["pipeline_run_id"] == "run-test"
    # `called` is folded into `status` by classify() rather than stored, so the
    # distinction survives as the word itself.
    assert call["status"] == "missing"
    assert call["error_summary"] == "not configured"
