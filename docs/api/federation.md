# Federation demonstration API

This document is the contract for the two-region federated-training
demonstration (`app.cli federation-demo` and `GET /api/v1/federation/status`).
It defines the **status response** and the **model-metadata (aggregate
artifact) contract** before the implementation.

## 1. What this is — and explicitly is not

| This is | This is not |
| --- | --- |
| A labeled **federation demonstration**: two deterministic regional partitions of one synthetic demo dataset, each trained locally, exchanging **model updates only**. | **Not** evidence of privacy: participants exchange fitted parameters and counts, but this establishes **no differential-privacy or other privacy guarantee**. |
| A workflow that records participants, model version, run status and provenance, and evaluates the aggregate on **held-out rows**. | **Not** nationwide (or even two-real-region) deployment: the "regions" are disjoint partitions of one synthetic Delhi-NCR-labelled dataset. |
| A pipeline where synthetic-only results are structurally ineligible for promotion (the registered model version is `synthetic_only: true` and `status: candidate`). | **Not** evidence of real-world accuracy: no observed labels participate, so the evaluation block is marked as such. |

`region_scope` is always pinned to
`two-partition-synthetic-demonstration`. Nothing in this feature asserts
nationwide coverage, real jurisdictional behaviour, or privacy properties.

## 2. What is exchanged (never raw rows)

Each participating client computes locally and sends to the aggregator exactly
one **model update payload**:

```json
{
  "participant_id": "region-a",
  "region_label": "federation-partition-a",
  "feature_schema_version": "environmental-v2",
  "data_mode": "demo",
  "algorithm": "standardized-ridge-residual",
  "ridge_alpha": 1.0,
  "horizons": {
    "1.0": {
      "feature_names": ["built_up_fraction", "..."],
      "means": {"built_up_fraction": 0.71},
      "scales": {"built_up_fraction": 0.12},
      "intercept": 3.41,
      "coefficients": {"built_up_fraction": -0.7},
      "interval_80_lower_offset": -6.2,
      "interval_80_upper_offset": 5.8,
      "interval_80_reason": null,
      "train_count": 42,
      "station_count": 3
    }
  },
  "example_count": 126,
  "train_count": 2730,
  "validation_count": 780,
  "test_count": 780,
  "station_count": 3,
  "horizon_count": 1,
  "heldout_examples": 780,
  "update_sha256": "<64 hex chars>"
}
```

The aggregator's input contract is **parameters and counts only**. Update
payloads must not contain:

- `target_pm25` / `baseline_pm25` / any per-row measurement,
- `issued_at` / `target_at` / any timestamp,
- `latitude` / `longitude` / `h3_cell` / any coordinate or cell id,
- station ids.

`update_sha256` is the SHA-256 of the canonical JSON of the payload (sorted
keys, compact separators), so what arrived at the aggregator can be
re-verified.

## 3. Aggregate artifact (model metadata)

The aggregator combines the participant updates with **federated weighted
averaging** (weights = each participant's training-row count), and writes one
immutable artifact per run:

```json
{
  "artifact_schema_version": "federated-ridge-v1",
  "algorithm": "weighted-fedavg-of-standardized-ridge-residual",
  "feature_schema_version": "environmental-v2",
  "region": "federation-demo",
  "data_mode": "demo",
  "synthetic_only": true,
  "horizons_hours": [1.0],
  "participants": [
    {
      "participant_id": "region-a",
      "region_label": "federation-partition-a",
      "train_count": 2730,
      "station_count": 3,
      "weight_fraction": 0.6,
      "update_sha256": "..."
    }
  ],
  "models": {
    "1.0": {
      "feature_names": ["..."],
      "means": {"...": 0.0},
      "scales": {"...": 1.0},
      "intercept": 0.0,
      "coefficients": {"...": 0.0},
      "ridge_alpha": 1.0,
      "interval_80_lower_offset": -6.1,
      "interval_80_upper_offset": 5.9,
      "participant_count": 2,
      "contributing_train_rows_total": 5500
    }
  },
  "exchange": {
    "raw_observation_rows_sent": 0,
    "raw_coordinates_sent": 0,
    "model_updates": 2
  },
  "evaluation": { "...": "see §5" },
  "limitations": { "...": "see §6" },
  "artifact_sha256": "<64 hex chars>"
}
```

## 4. Status endpoint

`GET /api/v1/federation/status` — public, no key.

**Before any run** the endpoint still answers `200` with:

```json
{
  "generated_at": "...",
  "is_demo": true,
  "data": {"status": "no_federation_run"}
}
```

**After a run**:

```json
{
  "generated_at": "...",
  "is_demo": true,
  "data": {
    "run_id": "federation-20250101T0000Z-h60-s6-v1",
    "status": "succeeded",
    "participant_count": 2,
    "region_scope": "two-partition-synthetic-demonstration",
    "participants": [
      {
        "participant_id": "region-a",
        "region_label": "federation-partition-a",
        "example_count": 1260,
        "train_count": 840,
        "validation_count": 240,
        "test_count": 180,
        "station_count": 3,
        "update_path": "var/federation/<run_id>/updates/region-a.json",
        "update_sha256": "...",
        "weight_fraction": 0.6,
        "joined_at": "..."
      }
    ],
    "aggregate": {
      "artifact_path": "var/federation/<run_id>/aggregate.json",
      "artifact_sha256": "...",
      "algorithm": "weighted-fedavg-of-standardized-ridge-residual",
      "feature_schema_version": "environmental-v2",
      "horizons_hours": [1.0],
      "synthetic_only": true
    },
    "model_versions": [
      {
        "model_id": "federation-...-h1.0",
        "region": "federation-demo",
        "horizon_hours": 1.0,
        "status": "candidate",
        "synthetic_only": true
      }
    ],
    "evaluation": {
      "status": "synthetic_evaluation_only",
      "heldout_examples": 360,
      "usable_as_real_world_evidence": false,
      "reason": "synthetic-only labels; no observed station observations exist for this run",
      "horizons": [
        {
          "horizon_hours": 1.0,
          "mae_ugm3": 7.94,
          "baseline_mae_ugm3": 9.6,
          "rmse_ugm3": 9.1,
          "bias_ugm3": 0.4,
          "heldout_count": 360
        }
      ]
    },
    "raw_rows_exchanged_to_aggregator": 0,
    "limitations": {
      "privacy": "not established: exchanging model parameters is not a privacy guarantee",
      "geography": "two disjoint partitions of one synthetic regional demo dataset; not a nationwide deployment",
      "accuracy": "synthetic-only evaluation; not evidence of real-world accuracy"
    },
    "started_at": "...",
    "finished_at": "..."
  }
}
```

Numbers are illustrative; the contract is the shape.

## 5. Evaluation rules

- Each participant trains on its own train/validation split only. The
  aggregate is fitted on those updates (never on raw rows), then evaluated on
  each participant's **held-out test rows**, which the aggregate never saw.
- Metrics: `mae_ugm3`, `baseline_mae_ugm3` (persistence/dispersion baseline on
  the same rows), `rmse_ugm3`, `bias_ugm3`, `heldout_count`.
- **Eligibility**: every evaluation produced by this demonstration carries
  `usable_as_real_world_evidence: false` and `status:
  "synthetic_evaluation_only"` because its labels are synthetic. If a run has
  no held-out labels, the evaluation block reports
  `{"status": "unavailable", "reason": "..."}` instead of inventing metrics.

## 6. Limitations (as recorded in every run)

- `privacy`: exchanging model parameters is **not** a privacy guarantee;
  no differential privacy, secure aggregation, or membership inference
  analysis exists.
- `geography`: demonstrated on two disjoint partitions of one synthetic
  regional dataset.
- `accuracy`: synthetic-only evaluation cannot establish real-world skill.
- Registered model versions are `candidate` + `synthetic_only`, so the
  promotion path (`assert_live_promotion_allowed`) structurally refuses them.

## 7. Command sequence (reproducible)

```bash
cd backend

# Full demonstration, persisted to the configured database:
python -m app.cli federation-demo

# Same demonstration without a database (artifacts only):
python -m app.cli federation-demo --no-db --out-dir var/federation
```

The run id is deterministic (`federation-<anchor>-h<hours>-s<stations>-v<version>`),
partition assignment is a stable station hash, and no random seed exists: the
same inputs always produce the same aggregate sha256.

## 8. Error shape

Standard platform errors
`{"error": {"code": "...", "message": "...", "details": [...]?}}`.
The status endpoint has no error paths beyond `500` (unhandled) and a
`503` when the database itself is unreachable, per platform conventions.
