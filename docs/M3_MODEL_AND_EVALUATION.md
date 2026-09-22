# M3 model training and evaluation

M3 adds an offline, reproducible candidate-training path. It does not change the live forecast path or automatically replace the deterministic dispersion baseline. Candidate artifacts are residual corrections around the supplied baseline, and must be reviewed against observed labels before any later promotion.

## Training-row contract

`training-export` normalizes already joined historical station labels and their as-of feature vectors. Each JSON object has:

- `example_id`, `station_id`, `h3_cell`, and `region`;
- UTC `issued_at` and `target_at`, plus a matching `horizon_hours`;
- `baseline_pm25`, observed/synthetic `target_pm25` in `ug/m3`, and a numeric-or-null `features` object;
- `data_mode` (`live` or `demo`), `target_kind` (`observed` or `synthetic`), `feature_schema_version`, optional `dataset_ids`, and `spatial_exclusion_verified`.

Inputs can be a JSON array, a JSON object with an `examples` array, or JSONL. The export pins PM2.5 labels to `ug/m3`. Each row is checked for timestamps, nonnegative PM2.5 values, finite supported features, horizon/time agreement, and provenance consistency. Export selects one mode and an optional `[start, end)` target-time range; it refuses cross-mode, mixed-label, duplicate-ID, multi-region, and multi-schema exports.

The current M2 storage does not yet retain queryable station-level as-of feature/label joins across historical runs. Consequently, M3 deliberately accepts a prepared history export instead of implying that joining raw station readings to cell snapshots is already implemented. Future history-query work can feed the same interchange format without changing training or evaluation.

## Commands

From `backend/`:

```powershell
python -m app.cli training-export --input joined-history.jsonl --mode live --start 2024-01-01 --end 2026-01-01 --out live-training.json
python -m app.cli training-smoke-data --hours 24 --stations 6 --out smoke-training.json
python -m app.cli train --dataset smoke-training.json --allow-synthetic
python -m app.cli evaluate --model models/candidates/<artifact-sha256>.json --split smoke-training.json --out evaluation.json
```

`training-smoke-data` creates a deterministic, explicitly fictional dataset with weather, rainfall, calendar, population, land cover, roads, traffic, and pollution fields. Its `synthetic_only` marker and `scientific_validation: false` are intentional. Training refuses it unless `--allow-synthetic` is passed, and even then the candidate remains ineligible for live promotion. Use `--register-db` to also upsert candidate metadata into the migrated Postgres registry; by default, artifacts and a local JSON registry allow fully offline smoke tests.

## Model and evaluation

The first learner is standardized ridge regression on `observed PM2.5 - baseline PM2.5`. Missing feature values are imputed from training-only means; means, scales, coefficients, schema, data mode, provenance, and a canonical SHA-256 are pinned in the immutable JSON artifact. This dependency-light learner is an M3 evaluation baseline, not a production model-quality claim; replacing it with a pinned tree learner can follow once real-history validation justifies the extra training/runtime dependency.

For each horizon, target timestamps are split chronologically 60/20/20 into train, calibration/validation, and untouched test periods. Training labels end before the validation boundary. A separate deterministic station holdout excludes 20% of stations, then trains/calibrates chronologically on the remaining stations and scores only future timestamps for the held-out stations. A spatial score is marked unsupported, and blocks promotion, unless the prepared row explicitly verifies that its baseline and station-derived inputs were built without the held-out stations. Reports compare persistence (`current_pm25`), the supplied dispersion baseline, and the residual candidate using overall and station-balanced MAE, RMSE, bias, high-pollution MAE, alert precision/recall, and India-season subgroup metrics.

Empirical q10/q90 residual offsets are calibrated on validation labels only. They produce nonnegative ordered 80% interval bounds; unsupported calibration yields no interval and an explicit reason. Held-out interval coverage and width are reported. Candidate blockers include synthetic provenance, unsupported/weak baseline comparison, high-pollution performance, alert-recall regression, inadequate season coverage in the test window, and unsupported/out-of-band interval coverage. No command auto-promotes a model. Synthetic and unvalidated registry entries are rejected by the live-promotion guard.

The artifact digest is the SHA-256 of the canonical model payload with the digest field omitted. Registry metadata is stored separately to avoid a circular digest; model IDs are derived from the digest plus horizon, and repeated registration of the same candidate is idempotent.
