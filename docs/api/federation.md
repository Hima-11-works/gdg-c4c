# Federation demonstrations

This document is the contract for **both** federated demonstrations in this
repository. They are independent features; neither is a rewrite of the other.

| Part | What it is | Entry points | Status API |
| --- | --- | --- | --- |
| **A — two-partition demo** (§1–§8) | one process splits one synthetic dataset into two regions, trains locally, aggregates, evaluates, and can persist to the database | `python -m app.cli federation-demo` | `GET /api/v1/federation/status` (public, database-backed) |
| **B — client/aggregator workflow** (§9–§20) | **two client processes with distinct data stores** and **one aggregator process**, exchanging authenticated, versioned model updates over HTTP and evaluating the aggregate on held-out data; clients can use synthetic demo rows or a validated, locally held observed-label export | `python -m app.federation_client`, `python -m app.federation_aggregator` | the aggregator process's own `GET /status` |

Part A is unchanged by Part B. Read §1 first: the same honesty rules govern both,
and in particular neither workflow claims independent agencies, real-world
accuracy, or a privacy guarantee.

## 1. What this is — and explicitly is not

| This is | This is not |
| --- | --- |
| A labeled **federation demonstration**: two deterministic regional partitions of one synthetic demo dataset, each trained locally, exchanging **model updates only**. | **Not** evidence of privacy: participants exchange fitted parameters and counts, but this establishes **no differential-privacy or other privacy guarantee**. |
| A workflow that records participants, model version, run status and declared provenance, and evaluates the aggregate on **held-out rows**. | **Not** nationwide deployment or verified inter-agency operation: participant identities and declared data regions are not independently verified. |
| Synthetic demo clients, plus observed-label clients that keep their rows local and share only signed fitted parameters and aggregate metrics. | **Not** evidence of real-world accuracy: observed labels are participant-provided and metrics remain ineligible for promotion until provenance and accuracy are independently validated. |

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

---

# Part B — the separately runnable client/aggregator workflow

## 9. What Part B is, and what it is not

Part B is a **process-separated** demonstration: two client processes, each
holding its **own data store**, train locally and submit **versioned model
updates** to a third process, the aggregator, which authenticates each update,
refuses anything carrying observation rows, records each participant's identity
and source scope, and records the aggregate's held-out evaluation.

| This is | This is not |
| --- | --- |
| Two independently executed client processes, each with its **own data store** (`var/federation/clients/<participant>/dataset.json`, `heldout.json`), training on synthetic demo data or a locally supplied observed-label export. Training rows never cross the process boundary. | **Not** two partitions of one dataset presented as two authorities. The clients are not verified agencies; every update, aggregate, and status response carries `independent_agencies: false`. |
| An **authenticated, versioned** update protocol: per-participant HMAC-SHA256 signatures, `update_sha256` digests, and a source-scope record. | **Not** a real multi-party deployment. The two clients are **not** independent Indian agencies, states, ministries, or data-sharing authorities, and no real agency participated. |
| An **update-only exchange** plus a client-side evaluation round trip: the aggregator sends the aggregate back, each client scores it on its own held-out rows, and reports metrics and counts. | **Not** a privacy guarantee. Exchanging fitted parameters is **not differential privacy**; no noise, clipping, secure aggregation, or membership-inference analysis exists here (§20). |
| A recorded run on disk (`status.json`, `aggregate.json`) readable from the aggregator's HTTP status API. | **Not** independently verified accuracy evidence. Synthetic metrics are labeled synthetic; observed-label metrics are labeled participant-reported and both carry `usable_as_real_world_evidence: false`. |

With no `--dataset`, the CLI uses the two deterministic synthetic scopes below.
With `--dataset`, the client instead validates a participant-local
`training-dataset-v1` JSON manifest with `data_mode: "live"`,
`target_kind: "observed"`, and `synthetic_only: false`. The participant must
keep its dataset file and generated `dataset.json`/`heldout.json` private. The
aggregator still receives parameters, counts, participant-reported scope, and
metrics only. HMAC authentication proves possession of a configured shared
secret; it does not prove agency identity or the truth of a source declaration.

## 10. The three processes

```
 client process #1                       client process #2
 ┌──────────────────────────┐            ┌──────────────────────────┐
 │ region-a                 │            │ region-b                 │
 │ store: .../region-a      │            │ store: .../region-b      │
 │  dataset.json            │            │  dataset.json            │
 │  heldout.json (stays)    │            │  heldout.json (stays)    │
 └───────────┬──────────────┘            └────────────┬─────────────┘
             │ signed update (parameters + counts)     │
             │                                          │
             │            ┌─────────────────────────────▼──┐
             └───────────►│ aggregator process               │
                         │  authenticates · refuses rows /  │
                         │  incompatible updates · records  │
                         │  run · GET /status               │
                         └──────────────┬───────────────────┘
                                        │ aggregate artifact
                          ┌─────────────┴─────────────┐
                          ▼                           ▼
                 client #1 scores on its        client #2 scores on its
                 own held-out rows, reports     own held-out rows, reports
                 metrics + counts only          metrics + counts only
```

- **Clients** — `python -m app.federation_client --participant region-a …`
  Build their own data store, train on their own train/validation split, keep
  their held-out split local, submit a signed update, wait for the aggregate,
  score it on their own held-out rows, and report metrics and counts.
- **Aggregator** — `python -m app.federation_aggregator --run-id … --client-keys …`
  Authenticates each update, refuses raw rows and incompatible updates,
  aggregates once every expected update has arrived, records the run to disk
  after every accepted change, and serves the status API. It **never** receives
  or reads an observation row, and it sends no notifications of any kind.

The two clients may run on different machines: nothing is shared except the
aggregator URL, the participant id, and that participant's secret.

## 11. Aggregator HTTP contract

`python -m app.federation_aggregator` serves four endpoints on
`--listen`/`--port` (default `127.0.0.1:8099`). Each process aggregates exactly
one run, fixed by its `--run-id`.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/updates` | Submit one client's signed, versioned model update. |
| `GET` | `/aggregates/{run_id}` | Fetch the aggregate artifact (once every expected update has arrived) so a client can score it on its own held-out rows. |
| `POST` | `/evaluations` | Submit a client's evaluation report — metrics and counts, never rows. |
| `GET` | `/status` | The recorded run (§16). |

Request headers on `POST /updates`:

| Header | Required | Meaning |
| --- | --- | --- |
| `X-Participant-Id` | yes | The submitting participant; must be registered in the aggregator's key registry. |
| `X-Participant-Signature` | yes | HMAC-SHA256 (hex) over the canonical request body, keyed with that participant's registered secret (§13). |
| `X-Run-Id` | no | Informational. The process is already bound to one `--run-id`, so this header is **not** used for authorization; a mismatch is not an error. |

`POST /evaluations` takes `X-Participant-Id` (and optional `X-Run-Id`). The
evaluation report is authenticated by *content*, not by signature: it must name
the submitting participant and the exact aggregate digest it scored (§15, §19).

## 12. The versioned update payload

Each client submits one JSON object. It is the Part A update payload (§2) plus
three metadata blocks this workflow needs, under its own schema version
`federation-workflow-update-v1`:

```json
{
  "update_schema_version": "federation-workflow-update-v1",
  "participant_id": "region-a",
  "region_label": "federation-partition-a",
  "feature_schema_version": "environmental-v2",
  "data_mode": "demo",
  "algorithm": "standardized-ridge-residual",
  "ridge_alpha": 1.0,
  "horizons": {
    "1.0": {
      "feature_names": ["..."],
      "means": {"...": 0.0},
      "scales": {"...": 1.0},
      "intercept": 0.0,
      "coefficients": {"...": 0.0},
      "interval_80_lower_offset": 0.0,
      "interval_80_upper_offset": 0.0,
      "interval_80_reason": null,
      "train_count": 84,
      "station_count": 4
    }
  },
  "example_count": 144,
  "train_count": 112,
  "validation_count": 32,
  "test_count": 32,
  "station_count": 4,
  "horizon_count": 1,
  "heldout_examples": 32,
  "scope": {
    "scope_id": "synthetic-basin-a",
    "scope_label": "Synthetic basin A (unlabelled geography)",
    "scope_kind": "synthetic",
    "synthetic_only": true,
    "independent_agency": false,
    "region_label": "federation-partition-a",
    "station_count": 4,
    "hours": 36,
    "anchor_utc": "2025-01-01T00:00:00+00:00",
    "dataset_sha256": "<64 hex chars>",
    "example_count": 144,
    "heldout_count": 32,
    "generated_at": "2025-01-01T00:00:00+00:00",
    "generated_at_kind": "deterministic-dataset-anchor"
  },
  "client_identity": {
    "participant_id": "region-a",
    "process_role": "federated-client",
    "holds_raw_rows": true
  },
  "limitations": { "privacy": "...", "participants": "...", "accuracy": "...", "geography": "..." },
  "update_sha256": "<64 hex chars>"
}
```

Two rules govern this payload:

1. **No raw rows.** The top-level key set is an allow-list: the Part A keys plus
   `scope`, `client_identity`, and `limitations`. Anything else is refused. A
   block-list names the row-shaped keys outright (`observations`, `labels`,
   `rows`, `station_ids`, `measured_at`, `issued_at`, `target_at`, `h3_cell`,
   `latitude`, `longitude`, …) so the rejection says *why*. Each `horizons`
   block has its own allow-list, so a row cannot hide inside a model.
2. **The metadata is not model content.** The three added blocks are projected
   out before averaging, so Part A's aggregator and its strict contract stay
   exactly as they are; the full updates are kept on record.

`update_sha256` is the SHA-256 of the canonical JSON of the payload (sorted keys,
compact separators) **excluding the `update_sha256` field itself**. The
signature in `X-Participant-Signature` is computed over the full body, digest
included, so a verifier recomputes both from the bytes that arrived.

**The update is deterministic.** There is no random seed and no wall clock in the
payload: `scope.generated_at` is the dataset's generation anchor
(`generated_at_kind: "deterministic-dataset-anchor"`), not the time the process
happened to run. A client re-run with the same arguments therefore signs
byte-identical bytes, so a retry after the aggregator was briefly unreachable
is idempotent rather than a second, conflicting update. As in Part A, the same
inputs always produce the same update and aggregate digests.

## 13. Authentication, identity, and source scope

**Authentication is per participant, not a shared API key.** The aggregator is
started with a registry:

```bash
--client-keys 'region-a:<secret-a>,region-b:<secret-b>'   # or FEDERATION_CLIENT_KEYS
```

A client signs its own update with **its own** secret and states its identity
in the `X-Participant-Id` header. The aggregator checks, in order:

1. the participant is registered (`unknown_participant`),
2. the body's `participant_id` matches the header (`participant_mismatch`),
3. the HMAC-SHA256 signature over the canonical body matches that participant's
   secret (`signature_mismatch`),
4. the participant has not already submitted a *different* update for this run
   (`duplicate_participant_update`; an identical, correctly signed resubmission
   is idempotent and returns `duplicate: true` with the same response shape),
5. the payload carries no raw rows (§12),
6. the update is compatible with the run's reference (§14).

Authentication precedes the duplicate check deliberately: an unauthenticated
caller must not be able to probe which participants have already submitted, and
a resubmission is only idempotent if it is genuinely signed. Because updates are
deterministic (§12), a client's own retry is the identical payload and is
accepted as a duplicate rather than refused as a conflict.

A generic API key would let any holder submit as anyone; the registry plus a
per-participant signature does not. One participant's secret cannot sign for
another: the body names the participant too, and the signature is verified
against the registered secret for that specific id.

**Identity and source scope are recorded, not inferred.** The aggregator stores,
per participant, the `scope` block above — scope id and label, `synthetic_only:
true`, `independent_agency: false`, station count, hours, the dataset digest
`dataset_sha256`, row counts, and the deterministic generation anchor — together
with the update digest and `signature_verified: true`. The status API (§16)
reports all of it. The scope digest lets a reviewer confirm later that two runs'
participants really did hold different data, without the aggregator ever holding
the rows.

## 14. Refusals

### Raw observation rows

Any payload carrying row-shaped data is refused with `422` and code
`raw_rows_rejected` — at the top level, and inside a `horizons` block.

```
POST /updates  (a valid, correctly signed region-b update + one smuggled row)
HTTP 422
{"error":{"code":"raw_rows_rejected",
          "message":"update carries raw observation-row keys: ['observations']; only fitted parameters and counts may be submitted"}}

POST /updates  (the same row hidden inside horizons["1.0"])
HTTP 422
{"error":{"code":"raw_rows_rejected",
          "message":"update payload horizon 1.0 carries unexpected keys: ['labels']"}}
```

### Incompatible model updates

An update whose `update_schema_version`, `feature_schema_version`, `algorithm`,
`ridge_alpha`, or per-horizon `feature_names` disagree with the run's reference
update is refused with `422` and code `incompatible_model_update`, rather than
being averaged into a model that means nothing. The first accepted update of a
run is the reference; later updates must match it.

```
POST /updates  (region-b trained locally with ridge_alpha 0.25; the run's
                reference is 1.0)
HTTP 422
{"error":{"code":"incompatible_model_update",
          "message":"update ridge_alpha=0.25 does not match the run's reference 1.0"}}
```

## 15. Held-out evaluation: the client scores, the aggregator records

The aggregate is fitted from updates only. Each client then:

1. receives the aggregate (`GET /aggregates/{run_id}`),
2. scores it on **its own held-out rows**, which never left its store,
3. reports metrics and counts — never rows.

The evaluation report is
`federation-workflow-evaluation-v1`: the participant id, the exact
`aggregate_sha256` scored, per-horizon `mae_ugm3`, `baseline_mae_ugm3`,
`rmse_ugm3`, `bias_ugm3`, and `heldout_count`, plus
`usable_as_real_world_evidence: false`, a label-provenance declaration, and
`raw_rows_sent: 0`. In the default demo mode this says
`synthetic_only: true`, `label_provenance: "synthetic"`; with `--dataset`, it
says `synthetic_only: false`, `label_provenance:
"participant-reported-observed"`.

The aggregator verifies the report is about **this** run's aggregate and
**this** participant, and structurally refuses any report that claims real-world
usability from synthetic labels (`synthetic_evidence_rejected`, §19). The
following is the default synthetic demo response. Observed-data runs use
`observed_evaluation_unverified` and retain the same real-world-evidence
ineligibility:

```json
"evaluation": {
  "status": "synthetic_evaluation_only",
  "usable_as_real_world_evidence": false,
  "label_provenance": "synthetic",
  "client_reports": 2,
  "reason": "each client scored the aggregate on its own synthetic held-out rows; these metrics are not evidence of real-world accuracy",
  "horizons": [
    {
      "horizon_hours": 1.0,
      "client_mae_ugm3": [0.0328, 0.0315],
      "mean_client_mae_ugm3": 0.0322,
      "heldout_count": 64
    }
  ]
}
```

## 16. The workflow status API

`GET /status` on the aggregator process. Public read, like Part A's endpoint.
It answers before any update has arrived, and after each accepted change; the
same payload is written to `<out-dir>/status.json` (§18).

```json
{
  "status_schema_version": "federation-workflow-status-v1",
  "run_id": "fedrun3-20260925T0200Z",
  "code_version": "v1",
  "status": "aggregated",
  "workflow": "separately runnable federated clients with distinct data stores",
  "independent_agencies": false,
  "synthetic_only": true,
  "participants_expected": 2,
  "participants_received": 2,
  "participants": [
    {
      "participant_id": "region-a",
      "update_sha256": "9f59c04a…",
      "signature_verified": true,
      "evaluation_reported": true,
      "scope": { "scope_id": "synthetic-basin-a", "dataset_sha256": "d8fb214d…", "synthetic_only": true, "independent_agency": false, "...": "see §12" }
    },
    { "participant_id": "region-b", "...": "same shape" }
  ],
  "raw_rows_exchanged_to_aggregator": 0,
  "aggregate": {
    "artifact_sha256": "537f17e0…",
    "algorithm": "weighted-fedavg-of-standardized-ridge-residual",
    "model_schema_version": "federated-ridge-v1",
    "synthetic_only": true
  },
  "evaluation": { "...": "see §15" },
  "limitations": {
    "privacy": "not established: exchanging fitted parameters is not a privacy guarantee, and no differential privacy mechanism is applied",
    "participants": "two locally executed simulation processes over independently generated synthetic scopes; they are NOT independent Indian agencies, states, or data-sharing authorities",
    "accuracy": "synthetic-only held-out labels; these metrics are not evidence of real-world accuracy",
    "geography": "the scopes are synthetic areas with no real-world boundaries and no real stations"
  }
}
```

`status` is `awaiting_updates` → `collecting` → `aggregated` as updates arrive;
`evaluation.status` is `awaiting_client_evaluations` until the clients report,
then `synthetic_evaluation_only`. The numbers above are from a real recorded
run; the contract is the shape.

**`GET /api/v1/federation/status` (Part A) does not read Part B runs.** Part B
is a standalone demonstration process with its own status API; it is not wired
into the platform's database-backed status endpoint, and it should not be
mistaken for one.

## 17. Launching the three processes

All commands run from `backend/` on Windows PowerShell. Pick a run id, a
registry, and a port; the clients are started concurrently so the second update
arrives while the first client is still waiting for the aggregate.

```powershell
$RUN_ID = "fedrun3-20260925T0200Z"
$KEYS   = "region-a:secret-a,region-b:secret-b"
$PORT   = 8126
$OUT    = "C:\Users\KIIT\AppData\Local\Temp\opencode\fedrun3"

# 1. the aggregator, in its own process:
.\.venv\Scripts\python.exe -m app.federation_aggregator `
    --run-id $RUN_ID --client-keys $KEYS --port $PORT --out-dir "$OUT\aggregator"

# 2. client region-a, in its own process, with its own data store:
.\.venv\Scripts\python.exe -m app.federation_client `
    --participant region-a --client-keys $KEYS --run-id $RUN_ID `
    --aggregator "http://127.0.0.1:$PORT" `
    --data-store "$OUT\clients\region-a" --hours 36 --wait-seconds 30

# 3. client region-b, likewise with its own store:
.\.venv\Scripts\python.exe -m app.federation_client `
    --participant region-b --client-keys $KEYS --run-id $RUN_ID `
    --aggregator "http://127.0.0.1:$PORT" `
    --data-store "$OUT\clients\region-b" --hours 36 --wait-seconds 30

# 4. read the recorded run's status API:
Invoke-RestMethod "http://127.0.0.1:$PORT/status" | ConvertTo-Json -Depth 8
```

Equivalent in bash (three terminals, or `&` for the clients):

```bash
cd backend
RUN_ID=fedrun3-20260925T0200Z
KEYS='region-a:secret-a,region-b:secret-b'
OUT=/tmp/fedrun3

python -m app.federation_aggregator \
    --run-id "$RUN_ID" --client-keys "$KEYS" --port 8126 --out-dir "$OUT/aggregator" &

python -m app.federation_client --participant region-a --client-keys "$KEYS" \
    --run-id "$RUN_ID" --aggregator http://127.0.0.1:8126 \
    --data-store "$OUT/clients/region-a" --hours 36 --wait-seconds 30 &

python -m app.federation_client --participant region-b --client-keys "$KEYS" \
    --run-id "$RUN_ID" --aggregator http://127.0.0.1:8126 \
    --data-store "$OUT/clients/region-b" --hours 36 --wait-seconds 30 &

wait
curl -s "http://127.0.0.1:8126/status" | python -m json.tool
```

Each client writes to its **own** store — `dataset.json` (its local rows),
`heldout.json` (its local held-out split), `update.json` (the signed payload it
sent), and `evaluation.json` (the metrics it reported). The two stores are
separate directories with disjoint station ids and cells; nothing merges them.
If `--data-store` is omitted, each client defaults to
`var/federation/clients/<participant>`, so two clients started with the same
command still never share a store.

#### Use observed data kept by the client

Prepare one `training-dataset-v1` JSON export per participant on that
participant's own machine. Each manifest must declare live/observed PM2.5
labels in `ug/m3`; the client validates its manifest and time splits before
training. Start both clients with `--dataset`:

```powershell
python -m app.federation_client --participant region-a --client-keys $KEYS `
  --run-id $RUN_ID --aggregator "http://127.0.0.1:$PORT" `
  --data-store "$OUT\clients\region-a" --dataset "D:\agency-private\region-a-training.json"

python -m app.federation_client --participant region-b --client-keys $KEYS `
  --run-id $RUN_ID --aggregator "http://127.0.0.1:$PORT" `
  --data-store "$OUT\clients\region-b" --dataset "D:\agency-private\region-b-training.json"
```

The client sends no training or held-out rows. The aggregator rejects a run if
participants mix synthetic and observed data or use incompatible feature
schemas. The status marks observed evaluation as
`observed_evaluation_unverified`; scores are not independently verified and
cannot be used for live model promotion. Each H3 cell center is checked against
the platform's coarse India state/UT geofence; participant IDs and region labels
still require a trusted agency enrollment process.

### Client output (real run, region-a)

```
client=region-a scope=synthetic-basin-a (data_mode=demo, synthetic_only=True, independent_agency=False)
  own data store: C:\...\fedrun3\clients\region-a
  local rows=144 held_out=32
  update_sha256=9f59c04a019ed330ee2b710ae08154e2661e541275cd17e5f2ae46e2a0c3c5c1 signature=ff4fffe8d45aad26...
  update accepted: {'accepted': True, 'aggregate_ready': False, 'duplicate': False, 'update_sha256': '9f59c04a…', 'updates_expected': 2, 'updates_received': 1}
  aggregate artifact_sha256=537f17e0217836c807c64dd530b2f04764de398e954c353e6435c8fc1749871b
  held-out +1h mae=0.03 baseline_mae=9.34 n=32
  evaluation provenance=synthetic; not independently verified or eligible for live model promotion
  evaluation accepted: {'accepted': True, 'evaluations_received': 1}
```

Both clients exited `0`. (Which client submits first varies with process
scheduling; the one that submits first sees `aggregate_ready: false` and waits
for the other.) Client exit codes: `0` success; `1` no usable client secret;
`2` the aggregator refused the update/evaluation, or bad CLI arguments; `3` the
aggregate was not ready within `--wait-seconds`.

### Running the clients one after another instead

If concurrent processes are inconvenient, the same run works sequentially: the
first client submits and exits `3` because the aggregate cannot exist yet, the
second completes the run, and re-running the first now finds the aggregate.
Because the update is deterministic (§12), that re-run resubmits the identical
signed payload, which the aggregator accepts as an idempotent duplicate rather
than a conflicting update.

```powershell
# client A: submits, finds no aggregate yet, exits 3
.\.venv\Scripts\python.exe -m app.federation_client --participant region-a `
    --client-keys $KEYS --run-id $RUN_ID --aggregator "http://127.0.0.1:$PORT" `
    --hours 24 --wait-seconds 4
$LASTEXITCODE   # 3

# client B: completes the run, evaluates, exits 0
.\.venv\Scripts\python.exe -m app.federation_client --participant region-b `
    --client-keys $KEYS --run-id $RUN_ID --aggregator "http://127.0.0.1:$PORT" `
    --hours 24 --wait-seconds 15
$LASTEXITCODE   # 0

# client A again: duplicate accepted, evaluates, exits 0
.\.venv\Scripts\python.exe -m app.federation_client --participant region-a `
    --client-keys $KEYS --run-id $RUN_ID --aggregator "http://127.0.0.1:$PORT" `
    --hours 24 --wait-seconds 15
$LASTEXITCODE   # 0
```

The third command is the case that exposed the two defects this contract now
rules out: it originally produced a *different* payload on each run (so the
retry was refused as `duplicate_participant_update`) and the duplicate check ran
before signature verification (so an unsigned replay of a stored update was not
authenticated). Both are covered by tests.

## 18. The recorded run on disk

| Path | Written by | Contents |
| --- | --- | --- |
| `<out-dir>/status.json` | aggregator, after every accepted update and evaluation | exactly the `GET /status` payload |
| `<out-dir>/aggregate.json` | aggregator, once the aggregate exists | the full artifact, including `source_scopes` (per-participant identity and scope) |
| `<client-store>/dataset.json`, `heldout.json` | client | that client's own local rows and held-out split — they never leave the client |
| `<client-store>/update.json`, `evaluation.json` | client | the signed payload it sent and the metrics it reported |

The aggregator can also be exercised without a server to check its recorded
starting state:

```bash
python -m app.federation_aggregator --run-id smoke-check \
    --client-keys 'region-a:s1,region-b:s2' --once --out-dir /tmp/fed-smoke
```

## 19. Error codes (Part B)

| Code | HTTP | Meaning |
| --- | --- | --- |
| `unknown_participant` | 422 | the submitting participant is not in the registry |
| `participant_mismatch` | 422 | the body's `participant_id` differs from the header |
| `signature_mismatch` | 422 | the HMAC does not match that participant's secret |
| `duplicate_participant_update` | 422 | the participant already submitted a *different* update for this run (an identical resubmission is idempotent) |
| `raw_rows_rejected` | 422 | a row-shaped key, an unknown top-level key, or an unknown key inside a `horizons` block |
| `incompatible_model_update` | 422 | the update disagrees with the run's reference update |
| `aggregate_not_ready` | 409 (`GET`), 422 (`POST /evaluations`) | not every expected update has arrived yet |
| `unknown_run` | 404 | `GET /aggregates/{id}` names a run this process does not serve |
| `incompatible_evaluation_report` | 422 | unsupported evaluation schema version |
| `aggregate_mismatch` | 422 | the report scored a different aggregate |
| `synthetic_evidence_rejected` | 422 | a synthetic-label report claimed real-world usability |
| `empty_body` / `invalid_json` | 422 | absent body, or not a JSON object |
| `payload_too_large` | 422 | body larger than 1 MiB; refused unread, since an update is a few kilobytes |
| `not_found` | 404 | unknown path |
| `internal_error` | 500 | an unexpected failure; one bad request does not kill the process |

## 20. What Part B does not establish

- **Privacy.** Exchanging fitted parameters is not differential privacy. There
  is no noise, clipping, secure aggregation, or membership-inference
  analysis. The HMAC authenticates the submitter; it does not hide what is
  submitted. The scope digest identifies a dataset; it does not protect it. A
  client's own store is plain, unencrypted JSON on local disk, and the
  aggregator's key registry is a command-line argument — this demonstration
  makes no production claim about either. The status payload states the limit on
  every read.
- **Real participants.** The two clients are simulation processes, not
  independent Indian agencies, states, or data-sharing authorities. Two
  partitions of one dataset would not be two authorities, and these are not
  even that: they are two independently generated synthetic scopes.
- **Real accuracy.** Every metric is computed on synthetic held-out labels and
  is marked unusable as real-world evidence. The registered model versions in
  Part A remain `candidate` + `synthetic_only`, and `assert_live_promotion_allowed`
  still refuses them.
- **Real geography.** The scopes are synthetic areas with no real boundaries,
  no real stations, and no real data sources.
