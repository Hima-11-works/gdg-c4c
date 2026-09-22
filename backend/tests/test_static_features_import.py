"""Tests for the offline static-feature artifact adapter."""

import json

from app.ingestion.static_features import load_static_features


def test_static_feature_artifact_reader_preserves_zero_population(tmp_path) -> None:
    path = tmp_path / "static.json"
    path.write_text(
        json.dumps(
            {
                "dataset_refs": [
                    {
                        "dataset_id": "worldpop-test",
                        "source": "WorldPop",
                        "product": "population",
                        "version": "2025",
                        "kind": "modeled",
                        "region": "delhi-ncr",
                        "attribution": "fixture",
                        "license": "test",
                    }
                ],
                "static_features": [
                    {
                        "h3_cell": "882f1d4887fffff",
                        "population_count": 0.0,
                        "population_density_per_km2": 0.0,
                        "road_length_km_by_class": {"primary": 1.0},
                        "built_up_fraction": 0.0,
                        "vegetation_fraction": 1.0,
                        "bare_soil_fraction": 0.0,
                        "industrial_fraction": 0.0,
                        "coverage_fraction": 0.8,
                        "valid_from": "2025-01-01T00:00:00Z",
                        "available_at": "2025-01-02T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    [features] = load_static_features(path)

    assert features.population_count == 0.0
    assert features.coverage_fraction == 0.8
    assert features.dataset_refs[0].dataset_id == "worldpop-test"
