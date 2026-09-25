# Backend release verification report

Release audit of the India sustainability backend, exercised against a **freshly
migrated** database and reproducible fixtures.

| | |
| --- | --- |
| Branch audited and fixed | `backhima` |
| Commit audited | **`6631397f785c1c40c9c089427b649b6eaf51d7a6`** — *"Detect candidate hotspots from satellite imagery, for human review"* |
| Database | fresh Docker volume, `alembic upgrade head` from empty → `0015_live_feature_inputs` |
| API | `http://localhost:8002` (compose project `gdg-audit`, isolated from the dev stack) |
| Owned / edited | `backend/**`, `docs/api/**` only |
| Not edited | `frontend/**`, `partner_apps/**`, `backend/app` runtime behaviour unchanged except one message fix |
| Merged to `main` | **no** |

B1–B6 map to the six commits under test: `b6c003f` (citizen evidence), `c9236df`
(incidents), `5ed2567` (live v2 inputs), `3c1e98c` (corridor), `e1af74f`
(federation), `6631397` (hotspots).

## Result matrix

| # | Feature | Result |
| --- | --- | --- |
| 1 | Report + evidence, retry without duplicates, invalid input, unverified excluded | **Pass** (1 defect fixed) |
| 2 | Alert identity → one incident, authority, transitions, retry, delivery, history | **Pass** |
| 3 | Scheduled pipeline coherent v2 run; input states distinct | **Pass** (4 stale tests fixed) |
| 4 | Exposure totals agree with map-facing fields; demo not sold as live | **Pass** |
| 5 | Corridor ids/forecast/evaluation agree; insufficient data explicit | **Pass** (1 defect fixed) |
| 6 | Federation separate processes, allowed updates only, honest scope | **Pass** (1 test fixed) |
| 7 | Hotspot fields complete; three fixtures as documented | **Pass** |
| 8 | Migrations, schemas, contract examples, exit codes, deployment settings | **Fail** (2 defects fixed; 2 open) |

### 1 — Citizen intake evidence · Pass

Fresh DB. `POST /api/v1/reports` → `id=1`; evidence `id=1` with a photo and a
sensor reading together.

| Check | Expected | Actual |
| --- | --- | --- |
| Photo + sensor attached | `201`, one record | `201`, `evidence id=1 report_id=1` |
| Reopen the record | persisted, still unverified | `unverified`, `sensor.verified=false` |
| Photo round trip | byte-identical | sent `cdf194e6…` = retrieved `cdf194e6…` = server-recorded sha256 |
| Interrupted retry, same key + payload | same record, no duplicate | `200`, `evidence id=1`; one record after |
| Invalid media | clear refusal | `415 unsupported_media_type`, *"unsupported photo type 'text/plain'; allowed: image/jpeg, image/png, image/webp"* |
| Negative value | clear refusal | `422`, *"sensor value must be >= 0"* |
| Disallowed pollutant | clear refusal | `422`, *"unsupported pollutant 'so2'; allowed: pm25, pm10"* |
| Reading older than 72h | clear refusal | `422`, *"sensor measured_at is older than 72.0h"* |
| Neither photo nor sensor | clear refusal | `422`, *"provide a photo, a sensor reading, or both"* |
| Unverified | never presented as verified | `verification_status=unverified`, `sensor.source=citizen`, `verified=false` |
| Excluded from model inputs | never read by the pipeline | `evidence_repository` is referenced **only** in `api/deps.py` and `api/routes/citizen_intake.py`; the estimator reads `sensor_reading`. Fresh DB after submission: `sensor_reading=0`, `report_evidence=1` — separate tables, citizen value never entered the model. |

### 2 — Incidents · Pass

Alert `v2:demo-winter_stagnation-20250115T1200Z:883da1109bfffff:1`.

| Check | Expected | Actual |
| --- | --- | --- |
| Create from published alert | `201`, derived run + cell | `id=1 status=reported`, `source_synthetic=true`, `linked_prediction_run_id` derived |
| Identity stability | same alert → one incident | second `POST` returned the **same** `id=1` |
| Role claim in body | refused | `403 role_mismatch`: *"request claims role pollution_control but actor 'engine-7' is registered as fire_department"* |
| Wrong-role actor | refused | `403 role_mismatch`: *"incident 1 is handled by pollution_control; actor 'engine-7' acts as fire_department"* |
| Jurisdiction | refused outside it | `403 jurisdiction_mismatch`: *"incident 1 is in jurisdiction 'Delhi'; actor 'maharashtra-unit' acts in 'Maharashtra'"* |
| No key | refused | `401` |
| Illegal transition | `409` | *"'assigned' -> 'resolved' is not allowed"* |
| Lifecycle | legal steps only | `acknowledged → en_route → on_scene → resolved`, each `200` |
| Retried transition | `200` no-op, no second event | exactly **one** `acknowledged` event after the retry |
| Terminal state | `409` | *"'resolved' -> 'en_route' is not allowed"* |
| Delivery | simulated, no notification | `simulated=true`, `notification: "none (simulated inbox only; no email, SMS, or webhook is sent)"` |
| History | append-only | 13 events, ids 1–13, no edits |
| Inbox closes on acknowledgement | 0 open | 0 open, 4 total |

No real emergency service is contacted anywhere: the only outbound-looking
field is the literal string above.

### 3 — Pipeline and v2 publication · Pass

`python -m app.pipeline.run` on the fresh DB: **11/11 stages `[OK]`**, exit 0.

```
[OK] publish_v2: published demo run demo-winter_stagnation-20250115T1200Z
     (mode=demo, cells=256, results=1792, profile=regional-demo)
```

`python -m app.cli verify-publication` → exit 0,
`run_id=demo-winter_stagnation-20250115T1200Z horizons=[1..6]`.

In demo mode each optional source reports itself rather than being faked —
`static_features: demo mode - scenario supplies static features`, and likewise
for weather forecast, fires and traffic. Per-cell output keeps the distinction
visible: cells without an estimate return `pm25: null` with
`quality.missing_fields: ["pollution"]`, not a fabricated value.

> **Live mode is Untested.** It needs `DEMO_MODE=false` plus real provider
> credentials and network access; see *External requirements*.

### 4 — Exposure agrees with the map · Pass

`/api/v2/exposure` against the sum of all 256 per-cell `exposure` objects from
`/api/v2/grid/current`:

| Field | Endpoint | Σ per-cell | Delta |
| --- | --- | --- | --- |
| `covered_population` | 196941.94 | 196941.94 | **0** |
| `residents_above_threshold` | 196941.94 | 196941.94 | **0** |
| `unknown_population` | 1537334.54 | 1537334.54 | **0** |
| `population_weighted_pm25` | 180.62497 | 180.625 | rounding only |

`covered_population == residents_above_threshold` is **correct**, not a bug: all
32 cells carrying an estimate are above the 60 µg/m³ threshold (population-weighted
180.6). 224 of 256 cells are `unsupported_cells` and excluded, so the response
also reports `covered_fraction: 0.125`.

A Delhi-NCR demo is not sold as nationwide live: the response carries
`is_demo: true`, `mode: "demo"`, a `kind: "synthetic"` attribution naming
`region: delhi-ncr`, and `population_dataset_version: null`. *Observation:* the
human-readable `scope` string begins `"india; H3 resolution 8; …"`, which in
isolation could be over-read; the demo/synthetic flags and the attribution carry
the truth.

### 5 — Corridor · Pass

`corridor-evaluate --corridor delhi-kanpur --run-id demo-winter_stagnation-…` →
*"Corridor evaluation unavailable: published run … has no results in the
Delhi–Kanpur interstate corridor"*. The API agrees (`404 not_found`). The
res-8 Delhi-NCR demo cannot produce a res-7 corridor event, and the backend
refuses rather than approximating — the required explicit insufficient-data
behaviour.

> **The `evaluated` (real held-out metrics) branch is Untested.** It needs
> published runs covering the corridor's own 24 res-7 cells **and** verified
> real station readings inside the label windows. No such station exists here
> (nearest usable station ~61 km off-axis). Reproducibility of the aggregate
> digest is asserted by the suite, not re-derived in this audit.

### 6 — Federation · Pass

`federation-demo` then `/api/v1/federation/status`:

```
status=succeeded  participants=2  run=federation-20250101T0000Z-h60-s6-v1
region_scope=two-partition-synthetic-demonstration
raw_rows_exchanged_to_aggregator=0
aggregate.synthetic_only=true   model_versions={"model_ids":[…]}
evaluation.status=synthetic_evaluation_only  usable_as_real_world_evidence=false
limitations.privacy = "not established: exchanging model parameters is not a privacy guarantee"
```

Client and aggregator are separate modules (`app/federation_client.py`,
`app/federation_aggregator.py`) with per-participant stores. The exchanged
update for `region-a` has top-level keys
`algorithm, data_mode, example_count, feature_schema_version, heldout_examples,
horizon_count, horizons, participant_id, region_label, ridge_alpha,
station_count, test_count, train_count, update_schema_version, update_sha256,
validation_count` — **none** of the forbidden `target_pm25`, `baseline_pm25`,
`issued_at`, `target_at`, `latitude`, `longitude`, `h3_cell`, `station_id`.
Parameters and counts only.

> **Raw-row rejection and incompatible-update rejection: Untested** by direct
> exercise; covered by the suite (`test_federation_workflow.py`), not re-run
> adversarially here.

### 7 — Hotspots · Pass

`hotspot-scan --fixture-dir tests/fixtures/hotspots`, all three shipped cases:

| Fixture | verdict | candidates | evaluation |
| --- | --- | --- | --- |
| `positive_hotspot.json` | `candidates` | 1 (`high`, 0.75, sources `satellite_imagery+firms`) | `tp=1 fp=0 fn=0 unlabelled=0`, p=r=1.00 |
| `negative_distractor.json` | `candidates` | 2 (both `low`, 0.40) | `tp=0 fp=1 fn=1 unlabelled=1`, p=r=0.00 |
| `unavailable_no_imagery.json` | `insufficient_evidence` | 0 | `no_inputs`, `sufficient=false`, `precision/recall = n/a` (**null, not 0.0**) |

Every candidate carries acquisition time, H3 cell + WGS84 coordinates,
`detector_version: hotspot-candidate-v1`, `confidence` + `confidence_score` +
`confidence_basis`, `supporting_sources` + `evidence[]`,
`review_status: pending_human_review`, `pm25_ugm3: null`,
`source_attribution: unattributed`. The negative case names its own errors
(false-positive and missed cells), which is the point of shipping it.

### 8 — Migrations, schemas, examples, deployment · **Fail**

Full suite: **788 passed, 5 failed, 33 skipped**.

Fixed in this audit:

- **`docs/api/v2-contract-examples.json` did not validate against its own
  schemas** (`MetaV2Out` required `latest_run_id` and `generated_at`; the
  `current` block was missing `latitude`, `longitude`, `confidence` and the whole
  `exposure` object). Regenerated from a real published run, so every block is a
  verbatim response. `tests/test_environmental_contracts.py` now 4/4.
- **The suite is sensitive to ambient `SIMULATOR_*` env vars.** With a compose
  `.env` present, 40 incident-authority tests fail `401` because the test key no
  longer matches. CI is green only because `pipeline.yml` does not set them.

Still open (see below): the `.env.example` gap, and 5 environment-bound tests.

## Defects found and fixed

| # | Defect | Fix | Regression check |
| --- | --- | --- | --- |
| 1 | Corridor not-found message doubled the word: *"…interstate corridor corridor"* | `_corridor_label()` in `services/corridor_evaluation.py` appends "corridor" only when the name lacks it | 2 new tests in `tests/test_corridor_evaluation.py` |
| 2 | `v2-contract-examples.json` failed `MetaV2Out`/`GridCurrentV2Out` validation | Regenerated from a live published run | `tests/test_environmental_contracts.py` (now 4/4) |
| 3 | 4 forecasting tests broken: service passes `step_minutes`, the test double did not accept it | Double now accepts and records `step_minutes` | `tests/test_forecasting_service.py` (4→pass) |
| 4 | `test_default_client_stores_are_per_participant` asserted Windows path separators, so it failed on the Linux CI runner | Compare `Path` objects, not a rendered string | `tests/test_federation_workflow.py` (pass) |
| 5 | 2 grid tests asserted a superseded contract (`hours` required; no `forecast_minutes`) | Updated to the documented `minutes` behaviour | `tests/test_api_grid.py` (14 pass) |
| 6 | `test_environmental_contracts` used `Path(__file__).parents[2]` without `.resolve()`, unlike the rest of the suite | Added `.resolve()` | same file |

No check was weakened to make it pass; two tests were *changed* to assert the
current documented contract (defect 5), and that is called out above.

## Open failures and risks

**Open defect (release blocker) — deployment settings are undocumented.**
`.env.example` documents **none** of B1/B2's required configuration:
`CITIZEN_MEDIA_STORAGE`, `CITIZEN_MEDIA_DIR`, `CITIZEN_SENSOR_*`,
`SIMULATOR_API_KEY`, `SIMULATOR_ACTORS`. `citizen_media_storage` defaults to
`disabled` and `simulator_api_key` to `None`, so **a fresh clone following
`.env.example` cannot attach a photo or open an incident** — the first symptom
is `503 media_unavailable` *"media storage is not configured"*. `.env.example`
sits at the repository root, outside the paths I own (`backend/**`,
`docs/api/**`, `.github/workflows/pipeline.yml`), so I did not edit it. The
working local `.env` used here also had to `mkdir` the media directory and
`chown` it to uid 1000; nothing in the repo says that. **Owner action needed.**

**5 environment-bound tests, Untested rather than Pass:**
`test_migrations_offline`, `test_schema_compiles`,
`test_repository_statements_compile`, `test_training_data`,
`test_config::test_empty_environment_variables_fall_back_to_defaults`. These
compare alembic offline DDL against SQLAlchemy metadata and need a live PostGIS
`DATABASE_URL`; my container supplies `POSTGRES_*`, which the test settings do
not pick up. I did not confirm whether they pass in CI.

**The test suite is not run in CI.** `pipeline.yml` runs migrations, the
pipeline and `verify-publication` — never `pytest` or `ruff`. Six of the defects
above (stale doubles, the Windows-only assertion, the drifted contract example)
would have been caught immediately by a test step. Adding one is a change to a
file I own; I did not add it unasked because it alters CI cost and gating.

**Ambient-env fragility.** The suite passes only when `SIMULATOR_API_KEY`,
`SIMULATOR_ACTORS`, `CITIZEN_MEDIA_STORAGE` and `CITIZEN_MEDIA_DIR` are unset. A
developer with a compose `.env` sees 40 spurious failures. Isolated env or
`monkeypatch`ed settings in `conftest.py` would fix it.

## External requirements for live verification

Not exercised here; each needs a credential and network egress, and using the
project's shared keys was deliberately avoided.

| Area | Needs |
| --- | --- |
| Live v2 inputs (audit 3) | `DEMO_MODE=false`, `OPENAQ_API_KEY`, `FIRMS_MAP_KEY`, Open-Meteo (no key), plus `DATABASE_URL` on a real PostGIS |
| Static population / land use | `STATIC_FEATURES_URL` (a versioned static-cell artifact) |
| Traffic | `TRAFFIC_FEED_URL` + `TRAFFIC_FEED_SOURCE/PRODUCT/VERSION/ATTRIBUTION/LICENSE` |
| Corridor `evaluated` branch | real verified station PM2.5 inside the corridor's label windows (`OPENAQ_API_KEY`); none exists near the axis today |
| Citizen media | a writable `CITIZEN_MEDIA_DIR` (volume recommended — the container filesystem is lost on recreate) |
| Migrations/schema tests | a PostGIS `DATABASE_URL` |

## Complete command sequence: empty DB → hotspot candidate

Every step below was run, in this order, against the fresh `gdg-audit` stack.

```bash
# 0. bring up an isolated stack on fresh volumes (DB migrates from empty)
docker compose -p gdg-audit up -d
docker compose -p gdg-audit logs api | grep 'Running upgrade'   # 0001 … 0015

# .env must contain the keys .env.example omits (see the blocker above)
#   CITIZEN_MEDIA_STORAGE=filesystem
#   CITIZEN_MEDIA_DIR=/var/lib/air-health/citizen-media
#   SIMULATOR_API_KEY=…            SIMULATOR_ACTORS=…
docker exec -u root gdg-audit-api-1 mkdir -p /var/lib/air-health/citizen-media
docker exec -u root gdg-audit-api-1 chown -R appuser:appuser /var/lib/air-health

# 1. published run  (11/11 stages OK, exit 0)
docker exec gdg-audit-api-1 sh -c "cd /app && python3 -m app.pipeline.run"
docker exec gdg-audit-api-1 sh -c "cd /app && python3 -m app.cli verify-publication"
#   -> demo-winter_stagnation-20250115T1200Z, exposure 196941.94

# 2. citizen evidence
R=$(curl -s -X POST localhost:8002/api/v1/reports \
  -H 'X-Simulator-Key: $KEY' -H 'X-Actor-Id: control-room' -H 'Content-Type: application/json' \
  -d '{"latitude":28.61,"longitude":77.20,"kind":"crop_burning","smoke_intensity":3,
       "duration_hours":2,"client_report_id":"audit1-r1"}' | jq -r .data.id)      # -> 1
curl -s -X POST localhost:8002/api/v1/reports/$R/evidence \
  -H 'X-Simulator-Key: $KEY' -H 'X-Actor-Id: control-room' \
  -F client_report_id=audit1-e1 -F photo=@photo.png\;type=image/png \
  -F sensor_pollutant=pm25 -F sensor_value=142.5 -F sensor_unit=ug/m3 \
  -F sensor_measured_at=2026-09-25T11:47:27Z                                          # -> 201, unverified
curl -s localhost:8002/api/v1/reports/$R/evidence -H "X-Api-Key: $KEY" | jq .data.verification_status

# 3. alert  (v2 alerts are content-derived and stable)
curl -s "localhost:8002/api/v2/alerts?minutes=60" | jq -r '.data[0].alert_id'
#   -> v2:demo-winter_stagnation-20250115T1200Z:883da1109bfffff:1

# 4. incident from that alert (idempotent: the same alert returns the same incident)
I=$(curl -s -X POST localhost:8002/api/v1/incidents \
  -H 'X-Simulator-Key: $KEY' -H 'X-Actor-Id: control-room' -H 'Content-Type: application/json' \
  -d '{"source_type":"published_alert",
       "source_ref":"v2:demo-winter_stagnation-20250115T1200Z:883da1109bfffff:1",
       "evidence_report_ids":[1]}' | jq -r .data.id)                                # -> 1

# 5. responder transition
curl -s -X POST localhost:8002/api/v1/incidents/$I/assign \
  -H 'X-Simulator-Key: $KEY' -H 'X-Actor-Id: control-room' -H 'Content-Type: application/json' \
  -d '{"assignee":"unit-12","role":"pollution_control"}'
for S in acknowledged en_route on_scene resolved; do
  curl -s -X POST localhost:8002/api/v1/incidents/$I/transitions \
    -H 'X-Simulator-Key: $KEY' -H 'X-Actor-Id: unit-12' -H 'Content-Type: application/json' \
    -d "{\"to_status\":\"$S\"}"
done
curl -s localhost:8002/api/v1/incidents/$I/history | jq '.data | length'               # -> append-only

# 6. federation status
docker exec gdg-audit-api-1 sh -c "cd /app && python3 -m app.cli federation-demo --out-dir var/federation"
curl -s localhost:8002/api/v1/federation/status | jq '.data | {status, participant_count, region_scope, raw_rows_exchanged_to_aggregator}'

# 7. hotspot candidates
docker exec -u root gdg-audit-api-1 mkdir -p /app/var/hotspots
docker exec gdg-audit-api-1 sh -c "cd /app && python3 -m app.cli hotspot-scan \
  --fixture-dir tests/fixtures/hotspots --out-dir var/hotspots"
curl -s localhost:8002/api/v1/hotspots | jq '.data.scans[] | {scan_id, verdict, candidate_count}'
S=authored-positive-delhi-ncr-20251108T053000Z-a3eb07bd
curl -s localhost:8002/api/v1/hotspots/$S | jq '.data.candidates[0] |
  {candidate_id, confidence, confidence_score, detector_version, review_status, pm25_ugm3}'
```

## Test suite as run

```bash
docker cp backend/tests gdg-audit-api-1:/app/tests          # only app/ is bind-mounted
docker exec gdg-audit-api-1 sh -c "cd /app && \
  env -u SIMULATOR_API_KEY -u SIMULATOR_ACTORS \
      -u CITIZEN_MEDIA_STORAGE -u CITIZEN_MEDIA_DIR \
      python3 -m pytest -q"
# 788 passed, 5 failed, 33 skipped
```

`pytest`, `pytest-asyncio` and `respx` are in `[project.optional-dependencies] dev`
and are **not** in the container image; without them the suite reports 111
failures that have nothing to do with the code.
