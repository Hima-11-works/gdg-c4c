# M6 operations

M6 adds explicit operator controls around the M3 candidate workflow. Training
still creates an immutable candidate; it never replaces the model used for
inference automatically. Validation checks the pinned artifact's held-out
promotion gates. If a candidate includes M5 fire or traffic features, the
matching live incremental evaluation report is also required. Promotion and
rollback switch one region/horizon at a time, preserving the previous artifact
as a retired version.

## Keyless replay and visible live-data failures

Scenario replay and feature construction are deterministic and require no
OpenAQ, FIRMS, weather, or traffic API keys:

```text
python -m app.cli demo-replay --profile tiny-ci --replay-hour 12 --out work/replay.json
python -m app.cli demo-features --profile tiny-ci --replay-hour 12 --history-hours 24 --out work/features.json
```

Publishing the replay to the API also requires a configured database, but no
upstream API credentials. A live `prediction-publish` rejects synthetic
provenance before opening a database session. It also fails if there is no
current PM2.5 value supported by at least one observation station. Optional
fire and traffic feeds may be missing; their absence remains an explicit
missing feature rather than blocking a baseline-only live run.

```text
python -m app.cli prediction-publish --input work/features.json --feature-run-id replay-12 --mode demo
python -m app.cli prediction-publish --input work/features.json --feature-run-id should-fail --mode live
```

The second command must fail with a synthetic-input message. No demo data is
silently relabeled as live data.

## Candidate validation, promotion, and rollback

With the local JSON registry (offline operator workflow):

```text
python -m app.cli training-export --input joined-history.jsonl --mode live --out work/live-training.json
python -m app.cli train --dataset work/live-training.json
python -m app.cli model-validate --model-id MODEL_ID
python -m app.cli model-promote --model-id MODEL_ID
python -m app.cli model-rollback --model-id PREVIOUS_MODEL_ID
```

Use the exact model ID printed/listed in `models/registry.json`. `model-validate`
requires a non-synthetic live artifact with matching content hash and schema,
and requires all existing M3 per-horizon promotion gates to pass. It records
the reviewed state as `validated`; `model-promote` only activates validated
artifacts. Rollback selects a retired artifact for the same region and horizon
and rechecks its content address and evaluation evidence. Promotion retires
the previously active artifact; the prior immutable file is never overwritten.

For the Postgres registry used by live serving, pass `--register-db` to
`train`, `model-validate`, `model-promote`, and `model-rollback`. Training
always writes its local candidate artifact/registry and additionally registers
the candidate in Postgres when requested. Later validation/activation commands
in DB mode update Postgres; the local registry remains an offline audit copy
whose status may lag. Database activation is transactional and serialized per
region/horizon, and Postgres is the live-serving source of truth.

If the model uses fire or traffic predictors, prepare an incremental report
from the same live dataset and training settings as the candidate. The report's
with-group artifact digest must match the candidate digest and each supported
horizon must be eligible for manual review:

```text
python -m app.cli evaluate-feature-group --dataset work/live-training.json --group fires --as-of-verified --out work/fires-eval.json
python -m app.cli evaluate-feature-group --dataset work/live-training.json --group traffic --as-of-verified --out work/traffic-eval.json
python -m app.cli model-validate --model-id MODEL_ID --incremental-report work/fires-eval.json --incremental-report work/traffic-eval.json
```

Pass only the reports for feature groups present in the candidate. The as-of
flag is an operator assertion: it must be backed by checking each source's
availability timestamp against the prediction issue time. An ineligible M5
ablation blocks validation; it never triggers a model change.

## Error, coverage, and drift summaries

`model-monitor` consumes a `model-monitor-v1` JSON window with expected row
count, reference-window feature means/standard deviations, and prediction rows
joined to observed PM2.5 labels as they become available. Operational evidence
must declare `data_mode: live`, `target_kind: observed`, and
`synthetic_only: false`. Explicit demo fixtures may instead declare
`data_mode: demo`, `target_kind: synthetic`, and `synthetic_only: true`; these
reports are marked `demo_only` and are not operational evidence.
Missing predictions,
labels, and features remain missing and lower their corresponding coverage.
The report includes MAE, RMSE, bias, high-pollution MAE, interval coverage,
prediction/label coverage, feature missingness, and standardized feature-mean
shifts. An absolute shift of at least one reference standard deviation or
feature missingness of at least 20% marks the report `investigate`; these are
triage defaults, not scientific or regulatory thresholds.

```text
python -m app.cli model-monitor --input ../docs/dummy_data/model-monitor-tiny.json --out work/monitor.json
```

Monitoring rejects synthetic input mislabeled as live. Drift is an
investigation and candidate-training trigger only; `auto_promoted` is always
false. This M6
interchange format is an offline/operator report boundary, not a continuously
scheduled database-label joiner. Production scheduling and a trusted
prediction-to-observation reconciliation job remain deployment work.
