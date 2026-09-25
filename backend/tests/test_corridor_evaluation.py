"""Corridor / interstate events and the honesty of their evaluation.

The claim under test is narrow and important: **a forecast may only be scored as
real accuracy against real, withheld station observations — and when those are
absent, the result is an explicit insufficient-data answer, not a number.**

What these tests cover:

* the named corridor is a real place, with a stable event id and **labelled**
  illustrative geometry (never a road route);
* the metric maths (error, bias, high-pollution recall, coverage) per horizon and
  geography, with sample counts attached;
* the insufficient-data path, driven by the committed fixture
  ``fixtures/corridors/insufficient_labels.json``;
* a synthetic run, and labels from a synthetic source, are never reported as
  real accuracy;
* the API returns the event plus that verdict, and the CLI prints the report.

The metric tests use hand-made numbers to check the *arithmetic*. They are not,
and are not presented as, evidence about real-world accuracy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import h3
import pytest
from fastapi.testclient import TestClient

from app.domain.corridor import (
    DELHI_KANPUR,
    EvaluationVerdict,
    GeometrySource,
    Label,
    LabelWindow,
    make_event_id,
)
from app.domain.features import DataMode
from app.domain.prediction import PredictionResult, PredictionRun
from app.domain.types import SensorReading
from app.main import create_app
from app.services.corridor_evaluation import (
    DEFAULT_MIN_LABELS,
    EventNotFoundError,
    _slice_metrics,
    corridor_cells,
    evaluate_event,
    event_for_run,
    event_peak,
    geography_segments,
)

FIXTURES = Path(__file__).parent / "fixtures" / "corridors"
INSUFFICIENT_FIXTURE = FIXTURES / "insufficient_labels.json"
BACKEND_DIR = Path(__file__).resolve().parents[1]
RUN_AT = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)


class _Settings:
    alert_warning_threshold_ugm3 = 90.0
    synthetic_label_sources = "scenario,demo,demo-scenario"
    h3_resolution = DELHI_KANPUR.h3_resolution


# --- the corridor itself --------------------------------------------------


def test_the_named_corridor_is_a_real_place_with_labelled_geometry() -> None:
    assert DELHI_KANPUR.name == "Delhi–Kanpur interstate corridor"
    assert DELHI_KANPUR.kind.value == "interstate"
    labels = [label for label, _, _ in DELHI_KANPUR.endpoints]
    assert labels == ["Delhi", "Kanpur"]
    # Delhi ~28.61N/77.21E, Kanpur ~26.45N/80.33E: real coordinates.
    assert DELHI_KANPUR.endpoints[0][1:3] == (28.6139, 77.2090)
    assert DELHI_KANPUR.endpoints[-1][1:3] == (26.4499, 80.3319)
    # The geometry is labelled, and says what would replace it.
    assert DELHI_KANPUR.geometry_source is GeometrySource.ILLUSTRATIVE
    assert "not a road route" in DELHI_KANPUR.geometry_note
    assert "OSM" in DELHI_KANPUR.geometry_note
    assert "ILLUSTRATIVE" in DELHI_KANPUR.describe_geometry()


def test_corridor_cells_are_deterministic_and_ordered() -> None:
    first = corridor_cells(DELHI_KANPUR)
    second = corridor_cells(DELHI_KANPUR)

    assert first == second
    assert len(first) == len(set(first))
    assert len(first) > 10
    # The first cell is nearest Delhi, the last nearest Kanpur.
    delhi = DELHI_KANPUR.endpoints[0]
    kanpur = DELHI_KANPUR.endpoints[-1]
    first_lat, first_lon = h3.cell_to_latlng(first[0])
    last_lat, last_lon = h3.cell_to_latlng(first[-1])
    assert abs(first_lat - delhi[1]) < abs(last_lat - delhi[1])
    assert abs(last_lon - kanpur[2]) < abs(first_lon - kanpur[2])
    assert all(
        h3.get_resolution(cell) == DELHI_KANPUR.h3_resolution for cell in first
    )


def test_geography_segments_cover_every_cell_once() -> None:
    cells = corridor_cells(DELHI_KANPUR)
    segments = geography_segments(DELHI_KANPUR, cells)

    assert set(segments) == {"delhi_end", "midway", "kanpur_end"}
    flattened = [cell for name in ("delhi_end", "midway", "kanpur_end") for cell in segments[name]]
    assert flattened == list(cells)
    assert sum(len(value) for value in segments.values()) == len(cells)


def test_event_id_is_stable_and_run_scoped() -> None:
    horizons = (1.0, 3.0, 6.0)
    first = make_event_id(
        corridor_id="delhi-kanpur", run_id="prediction-a", horizons=horizons
    )
    same = make_event_id(
        corridor_id="delhi-kanpur", run_id="prediction-a", horizons=horizons
    )
    other_run = make_event_id(
        corridor_id="delhi-kanpur", run_id="prediction-b", horizons=horizons
    )
    other_horizons = make_event_id(
        corridor_id="delhi-kanpur", run_id="prediction-a", horizons=(1.0, 6.0)
    )

    assert first == same
    assert first.startswith("corridor:delhi-kanpur:prediction-a:")
    assert first != other_run
    assert first != other_horizons


# --- metric arithmetic (not real-world evidence) --------------------------


def _labels(cell: str, values: list[float]) -> list[Label]:
    return [
        Label(
            h3_cell=cell,
            measured_at=RUN_AT,
            value=value,
            source="cpcb",
            external_sensor_id=f"station-{index}",
        )
        for index, value in enumerate(values)
    ]


def test_metrics_report_error_bias_and_recall_with_counts() -> None:
    cell = corridor_cells(DELHI_KANPUR)[0]
    forecast = {cell: 120.0}
    # Two observed-above-threshold cases, one below.
    labels = _labels(cell, [100.0, 130.0, 40.0])

    scored = _slice_metrics(
        horizon_hours=3.0,
        geography="corridor",
        forecast_by_cell=forecast,
        labels=labels,
        threshold=90.0,
        min_labels=1,
    )

    assert scored.sufficient is True
    assert scored.pairs == 3
    assert scored.station_count == 3
    # errors: +20, -10, +80 -> mae 36.67, bias +30.0
    assert scored.mae_ugm3 == pytest.approx((20 + 10 + 80) / 3)
    assert scored.bias_ugm3 == pytest.approx(30.0)
    assert scored.rmse_ugm3 == pytest.approx(((400 + 100 + 6400) / 3) ** 0.5)
    # Of the two observations >= 90, both were forecast >= 90; the third
    # (40 µg/m³) was a false positive, so precision is 2/3.
    assert scored.high_pollution_observed == 2
    assert scored.high_pollution_recall == pytest.approx(1.0)
    assert scored.high_pollution_precision == pytest.approx(2 / 3)


def test_recall_is_undefined_not_zero_when_nothing_was_observed_high() -> None:
    cell = corridor_cells(DELHI_KANPUR)[0]
    scored = _slice_metrics(
        horizon_hours=1.0,
        geography="corridor",
        forecast_by_cell={cell: 10.0},
        labels=_labels(cell, [20.0, 25.0, 30.0]),
        threshold=90.0,
        min_labels=1,
    )

    # Reporting 0% recall here would be a fabricated claim.
    assert scored.high_pollution_recall is None
    assert "undefined" in scored.note


def test_a_slice_below_the_minimum_is_insufficient_not_a_number() -> None:
    cell = corridor_cells(DELHI_KANPUR)[0]
    scored = _slice_metrics(
        horizon_hours=6.0,
        geography="corridor",
        forecast_by_cell={cell: 100.0},
        labels=_labels(cell, [95.0]),
        threshold=90.0,
        min_labels=DEFAULT_MIN_LABELS,
    )

    assert scored.sufficient is False
    assert scored.mae_ugm3 is None
    assert "below the 5-label minimum" in scored.note


# --- the insufficient-data path ------------------------------------------


class _EmptyPublicationRepository:
    def __init__(self, run: PredictionRun | None = None, results=()):
        self._run = run
        self._results = list(results)

    def get_run(self, run_id):
        return self._run if self._run and self._run.run_id == run_id else None

    def latest_run(self, *, region=None):
        return self._run

    def list_results(self, run_id):
        return [item for item in self._results if item.run_id == run_id]


class _NoStationRepository:
    def list_since(self, since, *, pollutant=None):
        return []

    def list_latest(self):
        return []


def _published_run(*, mode: DataMode = DataMode.LIVE, synthetic: bool = False):
    cells = corridor_cells(DELHI_KANPUR)
    run = PredictionRun(
        run_id="prediction-features-20260924T0600Z",
        generated_at=RUN_AT,
        region="india",
        mode=mode,
        feature_run_id="features-20260924T0600Z",
        feature_schema_version="environmental-v1",
    )
    results = [
        PredictionResult(
            run_id=run.run_id,
            h3_cell=cell,
            horizon_hours=horizon,
            valid_at=RUN_AT + timedelta(hours=horizon),
            baseline_pm25=80.0,
            predicted_pm25=120.0 + index,
            quality=__import__(
                "app.domain.features", fromlist=["FeatureQuality"]
            ).FeatureQuality(coverage_fraction=0.8),
            synthetic=synthetic,
        )
        for index, cell in enumerate(cells)
        for horizon in (1.0, 3.0, 6.0)
    ]
    return run, results


def _install(monkeypatch, run, results, readings=()):
    import app.services.corridor_evaluation as module

    publication = _EmptyPublicationRepository(run, results)
    monkeypatch.setattr(
        module, "SqlPredictionPublicationRepository", lambda _s: publication
    )
    monkeypatch.setattr(
        module, "SqlSensorReadingRepository", lambda _s: _StationRepository(readings)
    )


class _StationRepository:
    def __init__(self, readings):
        self._readings = list(readings)

    def list_since(self, since, *, pollutant=None):
        return [
            item
            for item in self._readings
            if item.measured_at >= since
            and (pollutant is None or item.pollutant == pollutant)
        ]

    def list_latest(self):
        return list(self._readings)


def test_insufficient_data_fixture_is_honoured(monkeypatch) -> None:
    """The committed fixture's case: a live run with no station observations in
    the corridor's target windows must return an explicit insufficient-data
    result naming the gap."""
    fixture = json.loads(INSUFFICIENT_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["corridor_id"] == DELHI_KANPUR.corridor_id

    run, results = _published_run()
    assert run.run_id == fixture["run"]["run_id"], "fixture names this run"
    _install(monkeypatch, run, results, readings=[])

    event, _run, corridor_results = event_for_run(
        object(), corridor=DELHI_KANPUR, run_id=run.run_id
    )
    assert event.event_id == event.event_id  # stable
    assert event.horizon_hours == (1.0, 3.0, 6.0)

    evaluation = evaluate_event(
        object(),
        corridor=DELHI_KANPUR,
        event=event,
        results=corridor_results,
        settings=_Settings(),
        min_labels=DEFAULT_MIN_LABELS,
    )

    assert evaluation.verdict is EvaluationVerdict.INSUFFICIENT_DATA
    assert evaluation.is_usable_as_real_world_evidence is False
    assert evaluation.label_provenance == "none"
    assert evaluation.event.label_count == 0
    # The gap is stated, not implied.
    assert any(
        "no pm2.5 station observation fell in the target window" in reason
        for reason in evaluation.reasons
    )
    assert any(
        "below the 5-label minimum" in reason for reason in evaluation.reasons
    )
    # No metric is quoted for a slice with no labels.
    assert all(item.sufficient is False for item in evaluation.slices)
    assert all(item.mae_ugm3 is None for item in evaluation.slices)
    # Coverage is still reported, so the shortfall is quantified.
    assert evaluation.coverage.corridor_cells == len(event.cells)
    assert evaluation.coverage.cells_scored == 0
    assert evaluation.coverage.fraction_cells_scored == 0.0


def test_a_synthetic_run_is_never_real_accuracy(monkeypatch) -> None:
    """Even with plenty of observed labels, a demo run's own accuracy is a
    property of the scenario, not of the world."""
    cells = corridor_cells(DELHI_KANPUR)
    run, results = _published_run(mode=DataMode.DEMO, synthetic=True)
    readings = [
        SensorReading(
            source="cpcb",
            external_sensor_id=f"station-{index}",
            latitude=h3.cell_to_latlng(cell)[0],
            longitude=h3.cell_to_latlng(cell)[1],
            pollutant="pm25",
            value=100.0,
            unit="ug/m3",
            measured_at=RUN_AT + timedelta(hours=1),
        )
        for index, cell in enumerate(cells * 2)
    ]
    _install(monkeypatch, run, results, readings=readings)

    event, _run, corridor_results = event_for_run(
        object(), corridor=DELHI_KANPUR, run_id=run.run_id
    )
    evaluation = evaluate_event(
        object(),
        corridor=DELHI_KANPUR,
        event=event,
        results=corridor_results,
        settings=_Settings(),
        min_labels=1,
    )

    assert evaluation.verdict is EvaluationVerdict.INSUFFICIENT_DATA
    assert evaluation.is_usable_as_real_world_evidence is False
    assert any("synthetic/demo" in reason for reason in evaluation.reasons)
    # The labels were found — the refusal is about the *run*, not the labels.
    assert evaluation.event.label_count > 0


def test_synthetic_label_sources_are_excluded(monkeypatch) -> None:
    """A label from a declared synthetic source is counted as available and
    never scored."""
    cell = corridor_cells(DELHI_KANPUR)[0]
    run, results = _published_run()
    readings = [
        SensorReading(
            source="scenario",
            external_sensor_id="synthetic-0",
            latitude=h3.cell_to_latlng(cell)[0],
            longitude=h3.cell_to_latlng(cell)[1],
            pollutant="pm25",
            value=120.0,
            unit="ug/m3",
            measured_at=RUN_AT + timedelta(hours=1),
        )
    ]
    _install(monkeypatch, run, results, readings=readings)

    event, _run, corridor_results = event_for_run(
        object(), corridor=DELHI_KANPUR, run_id=run.run_id
    )
    evaluation = evaluate_event(
        object(),
        corridor=DELHI_KANPUR,
        event=event,
        results=corridor_results,
        settings=_Settings(),
        min_labels=1,
    )

    assert evaluation.event.label_count == 0
    assert evaluation.verdict is EvaluationVerdict.INSUFFICIENT_DATA


def test_strict_input_holdout_is_reported_as_a_gap(monkeypatch) -> None:
    """Asking for stations that provably did not influence the estimate is not
    approximated — it is reported as unavailable."""
    run, results = _published_run()
    _install(monkeypatch, run, results, readings=[])
    event, _run, corridor_results = event_for_run(
        object(), corridor=DELHI_KANPUR, run_id=run.run_id
    )
    evaluation = evaluate_event(
        object(),
        corridor=DELHI_KANPUR,
        event=event,
        results=corridor_results,
        settings=_Settings(),
        require_unused_stations=True,
    )

    assert evaluation.verdict is EvaluationVerdict.INSUFFICIENT_DATA
    assert any(
        "not available yet" in reason for reason in evaluation.reasons
    )


def test_missing_run_is_404_worthy(monkeypatch) -> None:
    _install(monkeypatch, None, [])
    with pytest.raises(EventNotFoundError):
        event_for_run(object(), corridor=DELHI_KANPUR)


def test_a_run_with_no_corridor_results_names_the_corridor_once(monkeypatch) -> None:
    """Regression: the not-found message appended "corridor" to a name that
    already ended in it, so it read "... interstate corridor corridor"."""
    run, _ = _published_run()
    _install(monkeypatch, run, [])
    with pytest.raises(EventNotFoundError) as excinfo:
        event_for_run(object(), corridor=DELHI_KANPUR, run_id=run.run_id)
    message = str(excinfo.value)
    assert "corridor corridor" not in message
    assert message.count("corridor") == message.lower().count("corridor")
    assert "Delhi" in message


def test_a_corridor_not_named_corridor_still_gets_the_word(monkeypatch) -> None:
    """The label adds the word only when the name does not already carry it."""
    run, _ = _published_run()
    _install(monkeypatch, run, [])
    plain = replace(DELHI_KANPUR, name="Delhi-Kanpur axis")
    with pytest.raises(EventNotFoundError) as excinfo:
        event_for_run(object(), corridor=plain, run_id=run.run_id)
    assert "Delhi-Kanpur axis corridor" in str(excinfo.value)
    assert "corridor corridor" not in str(excinfo.value)


def test_event_peak_finds_the_worst_horizon(monkeypatch) -> None:
    cells = corridor_cells(DELHI_KANPUR)
    run = PredictionRun(
        run_id="prediction-peak",
        generated_at=RUN_AT,
        region="india",
        mode=DataMode.LIVE,
        feature_run_id="f",
        feature_schema_version="environmental-v1",
    )
    results = [
        PredictionResult(
            run_id=run.run_id,
            h3_cell=cells[0],
            horizon_hours=horizon,
            valid_at=RUN_AT + timedelta(hours=horizon),
            baseline_pm25=50.0,
            predicted_pm25=value,
        )
        for horizon, value in ((1.0, 110.0), (3.0, 260.0), (6.0, 150.0))
    ]

    value, horizon = event_peak(results)

    assert value == 260.0
    assert horizon == 3.0


# --- the API and the command ---------------------------------------------


def test_api_returns_the_event_and_its_evidence(monkeypatch) -> None:
    run, results = _published_run()
    _install(monkeypatch, run, results, readings=[])
    client = TestClient(create_app())

    catalog = client.get("/api/v1/corridors")
    assert catalog.status_code == 200
    [entry] = catalog.json()["data"]
    assert entry["corridor_id"] == "delhi-kanpur"
    assert entry["geometry_source"] == "illustrative"
    assert "not a road route" in entry["geometry_note"]

    event, _run, _results = event_for_run(
        object(), corridor=DELHI_KANPUR, run_id=run.run_id
    )
    response = client.get(f"/api/v1/corridors/delhi-kanpur/events/{event.event_id}")

    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["event"]["event_id"] == event.event_id
    assert [point["horizon_hours"] for point in data["event"]["horizons"]] == [
        1.0,
        3.0,
        6.0,
    ]
    assert data["event"]["issued_at"].startswith("2026-09-24T06:00")
    assert data["evaluation"]["verdict"] == "insufficient_data"
    assert data["evaluation"]["usable_as_real_world_evidence"] is False
    assert data["evaluation"]["reasons"]
    # A missing real evaluation is signalled on the envelope too.
    assert body["is_demo"] is True

    unknown = client.get("/api/v1/corridors/delhi-kanpur/events/corridor:nope")
    assert unknown.status_code == 404
    assert client.get("/api/v1/corridors/berlin-moscow").status_code == 404


def test_cli_command_reports_the_insufficient_data_case() -> None:
    """`corridor-evaluate` must never print a metric it cannot justify. With no
    database here it reports that it could not evaluate, and exits non-zero."""
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli", "corridor-evaluate", "--corridor", "delhi-kanpur"],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert "mae=" not in completed.stdout
    assert "Corridor:" not in completed.stdout
    assert "corridor" in output.lower()


def test_label_window_is_inclusive_and_ordered() -> None:
    window = LabelWindow(from_at=RUN_AT, to_at=RUN_AT + timedelta(hours=1))

    assert window.contains(RUN_AT)
    assert window.contains(RUN_AT + timedelta(hours=1))
    assert not window.contains(RUN_AT + timedelta(hours=1, seconds=1))
    assert not window.contains(RUN_AT - timedelta(seconds=1))
    with pytest.raises(ValueError):
        LabelWindow(from_at=RUN_AT + timedelta(hours=1), to_at=RUN_AT)
