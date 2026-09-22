"""Fast, offline acceptance tests for the M1 scenario generator."""

from datetime import UTC, datetime

from app.domain.scenario import IngestionRunStatus
from app.ingestion.demo_scenarios import ScenarioClock, ScenarioGenerator


def test_same_clock_and_seed_produce_identical_snapshot_and_idempotent_export(tmp_path) -> None:
    generator = ScenarioGenerator.from_manifest("tiny-ci")
    first = generator.generate(7)
    second = generator.generate(clock=ScenarioClock(generator.anchor_utc, 7))

    assert first.checksum == second.checksum
    assert first.to_json() == second.to_json()
    output = tmp_path / "snapshot.json"
    assert first.write_json(output) is True
    assert first.write_json(output) is False
    assert output.read_text(encoding="utf-8") == first.to_json()


def test_profiles_generate_manifest_counts_and_real_h3_cells() -> None:
    tiny = ScenarioGenerator.from_manifest("tiny-ci").generate()
    regional = ScenarioGenerator.from_manifest("regional-demo").generate()

    assert (len(tiny.cells), len(tiny.sensor_readings), len(tiny.weather), len(tiny.roads)) == (
        12,
        6,
        4,
        8,
    )
    assert (
        len(regional.cells),
        len(regional.sensor_readings),
        len(regional.weather),
        len(regional.roads),
    ) == (
        256,
        32,
        16,
        64,
    )
    assert all(len(cell) == 15 for cell in tiny.cells)
    assert len(set(regional.cells)) == 256


def test_authored_scenarios_change_weather_and_pollution_directionally() -> None:
    clean = ScenarioGenerator.from_manifest("tiny-ci", scenario_id="clean_breezy").generate()
    winter = ScenarioGenerator.from_manifest(
        "tiny-ci", scenario_id="winter_stagnation"
    ).generate()
    washout = ScenarioGenerator.from_manifest(
        "tiny-ci", scenario_id="monsoon_washout"
    ).generate(12)

    assert sum(reading.value for reading in winter.sensor_readings) > sum(
        reading.value for reading in clean.sensor_readings
    )
    assert washout.weather[0].precipitation_mm == 8.0
    assert washout.sensor_readings[0].value < winter.sensor_readings[0].value


def test_snapshot_provenance_is_synthetic_and_replay_time_is_injected() -> None:
    anchor = datetime(2025, 6, 1, tzinfo=UTC)
    generator = ScenarioGenerator(
        "tiny-ci", scenario_id="upwind_fire", seed=9, anchor_utc=anchor
    )
    snapshot = generator.generate(clock=ScenarioClock(anchor, 4))

    assert snapshot.replay_at == datetime(2025, 6, 1, 4, tzinfo=UTC)
    assert snapshot.dataset_refs[0].kind.value == "synthetic"
    assert snapshot.fires
    assert snapshot.dataset_version().kind.value == "synthetic"
    assert snapshot.ingestion_run().status is IngestionRunStatus.SUCCEEDED
