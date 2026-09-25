# Implementation scope — India pollution platform, F1–F11

**Stage:** Prompt 0 (scope and architecture). No application behaviour is
changed by this document; it adds no code and no schema.

| | |
| --- | --- |
| Scoped against | `main` @ `1685e85` (integrated with the one commit that landed during this pass) |
| Brief | `india_pollution_implementation_prompts.md` (received out-of-band, 2026-09-26) |
| Assessment baseline in the brief | `origin/main` @ `1685e85` — **reached and verified**; the brief writes it as `1685e855`, which is one character longer than the real abbreviated SHA and resolves to nothing (§1.3) |
| F1 owner | **TBD** — §15 defines the hand-off contract so any owner can start |

---

## 1. Verification (what I actually ran and read)

### 1.1 Repository state

| Item | Finding |
| --- | --- |
| Branch | `main`, tracking `origin/main`, **0 ahead / 0 behind** |
| HEAD | `8cb39f1` — `[ux] Major roads join the highways at level 4` |
| Worktree | Clean before and after this pass, including untracked files |
| Stashes | 4 pre-existing (`satellite AOD fix`, incidental `pubspec.lock`, etc.) — untouched |
| Repository instructions | **None.** No `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` or `.cursorrules`. `README.md` is the developer reference; `tests/test_architecture.py` is the only mechanically enforced rule (layer imports) |
| Local branches | `backhima` @ `6631397`, `himanshi` @ `b044b18` — **both remote branches are deleted upstream**; only `origin/main` exists |

### 1.2 Tooling

| Tool | Version | Available |
| --- | --- | --- |
| Python | 3.14.7 (`.venv`) | yes |
| pytest | 9.1.1 | yes |
| Ruff | 0.16.8 | yes |
| Node / npm | 24.16.0 / 11.13.0 | yes |
| Flutter / Dart | Flutter on PATH at `C:\src\flutter`, Dart 3.13.3 | yes |

### 1.3 Changes since the assessment baseline: none on `main`

The brief is written against `origin/main` @ `1685e855`. The real abbreviated
SHA is **`1685e85`** — the brief has one character too many, and `1685e855`
resolves to nothing in this repository. That is worth recording, because an
owner who copies the SHA from the brief will conclude the baseline is missing
when it is simply misquoted.

With the typo corrected, **`1685e85` is the current tip of `origin/main`**, so
**no teammate commits have landed on `main` since the assessment.** The brief's
per-feature "current footing" column can therefore be read directly against
`main` as it stands, with one qualification:

> One commit landed on `origin/main` *during* this scoping pass —
> `1685e85` `[fix] Stop the map drawing cells and the smooth field outside
> India` (+349/−2 across `frontend/src/components/MapView.tsx` and the new
> `frontend/src/lib/indiaOutline.ts`). The document was rebased onto it and the
> frontend gates were re-run (§1.5).

The substantive movement since the assessment is therefore **not** on `main` at
all — it is the unmerged `backhima` line (§1.4).

### 1.4 Finding: a large body of F1–F11 work is not on `main`

`backhima` is **8 commits, 131 files, +29,725 / −216 lines** ahead of `main`, and
its remote branch has been deleted. It already contains work that maps onto most
of the brief:

| On `backhima`, not on `main` | Brief feature it pre-satisfies (partially) |
| --- | --- |
| `report_evidence` + `media_storage` + `citizen_intake` (migrations `0011`) | F1 (verification status), F2 (photo evidence) |
| `incident`, `incident_delivery`, actor registry (migrations `0012`, `0014`) | F8 (incidents, jurisdiction, simulated delivery) |
| `hotspot_detection` + `docs/api/hotspots.md` | F5 (candidate detector, image-driven) |
| `corridor_evaluation` (migration-free) | F6 (one named corridor, illustrative geometry) |
| `federation`, `federation_workflow`, `federation_client/_aggregator` (migration `0013`) | F11 (two simulated nodes, signed updates) |
| `static_features`, `weather_forecast`, live publication stages (migration `0015`) | F3, F4 (live feature inputs) |
| `partner_apps/fire_dept_simulator/` (new Flutter app) | F8 (the required simulator app) |
| `DataQualityNotice`, `RunStatusPanel`, `IncidentNotebook`, `runFacts` | F10 (source/mode labels) |

**Consequence for sequencing:** an F1 owner starting from bare `main` will
re-implement, or worse, collide with, ~29.7k lines of existing work. Step 0 of
the release sequence (§12) is to recover and merge this line. It is a
**demonstration-grade** implementation, not brief compliance: hotspots are
imagery-driven scans written to disk with authored fixtures (no persisted event
lifecycle), federation is two local processes (no node registry), corridors are
straight-line illustrative geometry. Each feature in §4–§11 states what the
backhima line already covers and what the brief still requires.

### 1.5 Baselines (exact, on `main` @ `1685e85`)

Backend and Flutter figures were taken on `8cb39f1`; the only commit added
thereafter (`1685e85`) touches `frontend/` alone, so the three frontend rows were
re-run on the integrated tree and the other five are unaffected.

| Gate | Command | Result |
| --- | --- | --- |
| Backend tests | `cd backend && pytest` | **553 passed, 11 failed, 31 skipped** (11.2s) — exit 1 |
| Backend lint | `ruff check .` | **79 errors**, 20 auto-fixable — exit 1 |
| Backend format | `ruff format --check .` | **36 files** would be reformatted, 126 clean — exit 1 |
| Frontend build | `npm run build` | **pass** (62 modules, 1.54 MB chunk warning) — exit 0 |
| Frontend lint | `npm run lint` (oxlint) | **pass** — 1 warning, 0 errors (46 files) — exit 0 |
| Frontend format | `npx prettier --check .` | **23 files** unformatted — exit 1 |
| Flutter analyze | `flutter analyze` | **58 issues** incl. hard `error`s — exit 1 |
| Flutter test | `flutter test` | **180 passed, 8 failed (load errors)** — exit 1 |

Ruff error mix: 58 × E501 (line length), 10 × I001 (import order), 6 × UP035, 3 ×
F401, 1 × B904, 1 × UP037. Hottest files: `app/api/routes/predictions_v2.py` (17),
`app/services/prediction_queries.py` (10), `app/models/tables.py` (8),
`app/services/prediction_publication.py` (6).

On the integrated tree the Prettier count moved from 22 to 23: the new
`frontend/src/lib/indiaOutline.ts` from `1685e85` is itself unformatted, so the
format debt grew with the fix. Build and lint are unaffected (exit 0).

The **11 backend failures are inherited, not new**, and are the same 11 the
`backhima` line also fails: `test_api_grid` (2), `test_environmental_contracts`
(1), `test_forecasting_service` (4), `test_migrations_offline` (1),
`test_repository_statements_compile` (1), `test_schema_compiles` (1),
`test_training_data` (1). Root causes are visible: `_FakeModel.forecast()` missing
`step_minutes`, `alert.forecast_hours` SMALLINT vs FLOAT, and a stale expected
table set.

**The Flutter failures are missing imports, not missing features.**
`lib/notifications/forecast_alarm_scheduler.dart` uses `SensitivityRules` without
importing `domain/sensitivity_rules.dart`; `test/features/app_widget_test.dart`
and `test/features/report_fire_sheet_test.dart` use `FireReportApiClient` without
importing `data/reports/fire_report_api.dart`. All three classes exist. Eight
test files therefore fail to load; the other 180 tests pass.

`flutter pub get` rewrites `partner_apps/air_health_flutter/pubspec.lock`. I
restored it; **it must never be committed** (it is already in a stash as
"incidental").

### 1.6 Stale README claims, resolved against code

| README claim | Reality on `main` |
| --- | --- |
| "five tables" in `app/models/tables.py` | **14 tables**, migrations `0001`–`0010` |
| Pipeline makes "1h/3h/6h forecasts" | `app/pipeline/run.py` produces **24 quarter-hour horizons** (`0.25`–`6.0`, `step_minutes=15`) |
| "All five stages should print `[OK]`" | **six** stages — `fire_reports` was added |
| "**No scheduler**" | `.github/workflows/pipeline.yml` **is** the scheduler: hourly cron + `workflow_dispatch`, `alembic upgrade head` then the pipeline against Neon. The README never mentions it |
| Env-var table | Omits `FIRMS_*`, `GIBS_BASE_URL`, `NO2_WMS_*`, `TILE_*`, `TRAFFIC_STALE_AFTER_HOURS`, `FIRE_*`, `PDI_FIRE_PRESSURE_WEIGHT`, `DATABASE_URL`. (`.env.example` itself is complete: 71 keys matching 71 `Settings` fields) |
| Repository structure | Omits `partner_apps/air_health_flutter` entirely, plus the v2 and tiles routes |
| "No authentication … every route on `/api/v1/*` and `/api/v2/*` is open" | **True on `main`** — but already false on `backhima` (simulator key + actor registry). The claim must be qualified per-branch |
| Flutter README: "Dummy provider active today; Remote is a Dio skeleton" | **Stale.** `GridApiPollutionDataProvider` calls `/api/v2/{grid/current,grid/forecast,weather,alerts}` and is selected whenever a `GridApiClient` is configured |
| README: repo structure "routes/ sensors, weather, grid, cells, alerts, reports, fires" | Also `tiles.py`, `health.py`, `predictions_v2.py` |

Recommendation: fold these corrections into the F3 README pass; do not let a
feature depend on a README sentence that is already wrong.

---

## 2. User journeys

### 2.1 Citizen (web `ReportFireForm` and Flutter `ReportFireSheet`)

```
Open app → India map, run-pinned current/forecast (v2)
  → "Report fire" → pick location (map or device) → kind + smoke intensity
    + duration + notes (+ photo, F2) → optional local sensor reading
  → POST /api/v1/reports (stable client_report_id for retry)
  → server: India geofence + rate/abuse check + dedup cluster → stored
  → citizen sees: report id, status, and when it was last updated
  → if/when corroborated: it is allowed to influence the modeled plume
  → next pipeline run: status and modeled effect visible in the app
Today: open endpoint, no geofence/rate limit/moderation, and the report feeds
the plume immediately.
```

### 2.2 Analyst

```
Scheduled run (GitHub Actions hourly, or CLI) → ingest OpenAQ + Open-Meteo
  → H3 current + PDI → publish 0–6h forecasts as ONE run → alerts
  → analyst opens decision map → sees per-cell value, data time, coverage,
    source, uncertainty, and demo/live state at point of use
  → filters by status/severity/source/jurisdiction → opens a cell or an event
  → inspects evidence (station anomaly, verified reports, FIRMS, imagery index)
  → for ML: trains a candidate on observed labels, validates on temporal +
    station/spatial holdouts, promotes or leaves it unpromoted
  → monitors coverage, drift and observed-label residuals
```

### 2.3 Simulated fire department (F8 simulator app — **simulated, never a real dispatch**)

```
Alert / verified report / hotspot event becomes an eligible incident
  → routed by location + type + severity to a jurisdiction
  → agency inbox (authenticated; agency sees only its own incidents)
  → acknowledge → assign → update → resolve
  → every transition written to an immutable audit trail
  → missed push never loses an incident (in-app inbox is authoritative)
  → web dashboard shows server-backed status, not a local checklist
  → web incident notebook (currently localStorage-only) becomes server-backed
```

---

## 3. Architecture and data flow (verified)

```
OpenAQ ─┐
        ├─▶ [ingest] ─▶ sensor_reading ─┐
Open-Meteo ┘                            │
FIRMS ────────▶ fire_hotspot            ▼
                                   [compute] IDW + PDI ─▶ grid_state ─▶ forecast ─▶ alert
static cells ─▶ cell_feature_snapshot     │                  │
weather fcst ─▶ weather_forecast           │                  │
citizen report ─▶ fire_report ─▶ report_evidence (F1/F2)       │
                                              │               │
                   ┌──────────────────────────┴───────────────┴──────────┐
                   ▼                                                      ▼
        [publish] PredictionPublicationService            [routes] /api/v1/*  /api/v2/*
        run-pinned, immutable, dataset_refs                   (public, unauthenticated on main)
                   │
                   ▼
        prediction_run + prediction_result  ──▶ /api/v2/{grid/current,grid/forecast,
                   │                              cells/{h3_cell},weather,alerts,exposure,meta}
                   ├──▶ alert identity (v2:<run>:<cell>:<horizon>) ──▶ incident ──▶ agency inbox
                   └──▶ hotspot / corridor / federation / federation client+aggregator
                                        │
                                        ▼
                    frontend (React+MapLibre)          partner_apps/air_health_flutter
                    v2 reads, POST /v1/reports         v2 reads, local notifications
                    partner_apps/fire_dept_simulator  (F8, on backhima)
```

Verified facts behind the diagram: `app/pipeline/run.py` runs six stages in one
session; `PredictionPublicationService.publish` refuses an empty snapshot set, a
mixed feature-schema run, duplicate `(cell, horizon)`, and
`assert_live_snapshots_available` for live mode; `prediction_run` carries
`mode IN ('live','demo','mixed')`, `region`, `feature_run_id`, `model_versions`
and `dataset_refs`; `prediction_result` is keyed `(run_id, h3_cell,
horizon_hours)` with CHECK constraints on non-negative PM2.5, interval ordering,
`horizon_hours >= 0` and `input_kind IN ('observed','modeled','synthetic','derived')`.
Seven `/api/v2` GET routes, all public. The React map reads v2 for
meta/grid/weather/alerts/cells and writes only `POST /api/v1/reports`.

---

## 4–11. Feature scope

Each entry: **footing** (verified) → **gap** → **new entities** → **API
contract** → **acceptance** → **proof mode** (`O` = provable offline,
`E` = needs a configured external service, `D` = needs a device/emulator).

### F1 — Citizen reports (make trustworthy)

- **Footing.** `fire_report` (11 columns, **no status column**) with
  `UNIQUE(client_report_id)` for retry idempotency, `geom` for spatial queries,
  age-bounded by `FIRE_REPORT_MAX_AGE_HOURS`; `POST /api/v1/reports` is
  **unauthenticated**; `PlumeFireGradientModel` lets a report alter the modeled
  field immediately. Web `ReportFireForm` and Flutter `ReportFireSheet` exist.
  `backhima` adds `report_evidence` with
  `verification_status IN ('unverified','pending','verified','rejected')`.
- **Gap.** No report lifecycle, no India geofence, no rate/abuse control, no
  duplicate clustering, no moderation path, no status/timing surfaced to
  clients, and a report that is unverified still moves the modeled field.
- **New entities.** `fire_report.status`
  (`submitted|under_review|corroborated|rejected|expired`), `moderated_by`,
  `moderated_at`, `moderation_note`, `cluster_id`, `corroborating_report_count`,
  `expires_at`, `india_geofence_verified`, `abuse_score`. Reversible
  `ALTER TABLE … ADD COLUMN` with server defaults so existing rows backfill.
- **API.** Keep `POST /api/v1/reports` and its response keys; add
  `GET /api/v1/reports/{id}` (status + update timing),
  `POST /api/v1/reports/{id}/moderation` (role-gated), and a clustered list
  view. If the response must change materially, add `/api/v2/reports` and leave
  v1 delegating.
- **Acceptance.** Out-of-country coordinates rejected; malformed notes rejected;
  duplicate retry returns the original; abusive burst throttled; every moderation
  transition legal and audited; expiry enforced; **an uncorroborated report
  demonstrably does not alter the plume**; list permissions enforced; web and
  Flutter status visible after a pipeline run.
- **Proof.** `O` for all of the above. `D` only for the on-device Flutter leg.

### F2 — Citizen photos (implement)

- **Footing.** None on `main`. `backhima` implements
  `report_evidence` + `media_storage`: content sniffing by bytes (JPEG EOI / PNG
  IEND / WebP RIFF size), bounded reads, fsync→rename→read-back durable writes,
  `CITIZOTEN`-style `CITIZEN_MEDIA_STORAGE=disabled` by default (503
  `media_unavailable`, sensor-only still works), `verify-media-storage` CLI, and
  a 503-not-404 rule for recorded-but-missing bytes.
- **Gap.** Capture UI, consent, retention/deletion jobs, reviewer access control,
  safe re-encoding, malware/quarantine, and orphaned-file cleanup on failure.
- **New entities.** `report_evidence.media_*` (already on `backhima`) plus
  `consent_at`, `retention_expires_at`, `deleted_at`, `derivative_key`,
  `review_state`.
- **API.** `POST /api/v1/reports/{id}/evidence` (narrowly scoped), reviewer-only
  read of the safe derivative, delete-by-policy endpoint. Never a public object
  URL, never raw storage credentials.
- **Acceptance.** Format/size/misleading-MIME/corrupt rejected; interrupted
  upload retryable without duplicates; unauthorized retrieval refused; deletion
  works; report succeeds without a photo; no orphaned files.
- **Proof.** `O` for the whole contract; `D` for camera/gallery and for the
  reviewer view on a real device.

### F3 — PM2.5 forecast and alerts (operate live)

- **Footing.** Six-stage pipeline; v2 publication with run-pinned reads and
  immutable results; four alert rules; hourly GitHub Actions scheduler applying
  migrations then running the pipeline; `prediction_queries` aggregates native
  cells up to a coarser display resolution; per-endpoint `is_demo` fallback.
- **Gap.** No source-health record per run, no explicit staleness in the
  envelope, silent fallback risk, no replay command, no retry bounds.
- **New entities.** `ingestion_run` already exists — extend with per-source
  status (`present|empty|stale|missing|failed`), latency, and error summary.
- **API.** `GET /api/v2/meta` already reports mode/run/coverage; add
  `source_health` and `stale` semantics; add `python -m app.cli replay-run`.
- **Acceptance.** Fresh/stale/empty/partial/failed/duplicate source windows
  covered; seeded end-to-end run queried for one run id; demo runs stay
  explicitly demo; thresholds documented as India CPCB NAQI PM2.5 bands and
  never conflated with all-pollutant AQI.
- **Proof.** `O` for all logic; `E` for one live OpenAQ/Open-Meteo cycle.

### F4 — Satellite fusion (connect evidence to inference)

- **Footing.** FIRMS ingestion and `fire_hotspot` storage with
  `acquired_at`/`available_at`; GIBS tile proxy with an in-process cache; NO2
  optional and 404 when unset. `backhima` adds bounded FIRMS staging into
  publication and static-cell features.
- **Gap.** FIRMS is **not** in the live model. Imagery layers are display-only;
  a raster overlay is not a training feature.
- **New entities.** `fire_feature` per `(h3_cell, run)` with `acquired_at`,
  `available_at`, FRP, distance/upwind relation, quality flags, and raw
  detection provenance.
- **API.** Internal; surfaced as `dataset_refs` on published results.
- **Acceptance.** No-detection vs failed-feed distinguished; bounds, dates,
  duplicates, staleness covered; **an as-of join test proving a
  later-available detection cannot influence an earlier forecast**; held-out
  comparison with and without each source group, recorded even if excluded.
- **Proof.** `O` for parsing, joins, and the as-of test; `E` for a live FIRMS
  cycle and for a licensed quantitative AOD/NO2 product.

### F5 — Hidden hotspots (build detection)

- **Footing.** `backhima` has a bounded, versioned
  `hotspot-candidate-v1` detector: imagery index trigger, FIRMS/station support,
  cloud masking, freshness, georeference verification, `insufficient_evidence`,
  and false-positive/missed-detection evaluation against labelled fixtures.
- **Gap.** The brief needs a **persisted event** with footprint, time window,
  severity, evidence links, lifecycle, cross-cell/run dedup, and separation of
  potential/corroborated/resolved — and it should combine **time-aligned station
  anomalies** and wind context, not imagery alone. The backhima detector writes
  scans to disk and never creates a measured PM2.5 value, which is the right
  instinct but not the required event model.
- **New entities.** `hotspot_event` (id, region, footprint cells, window,
  severity, status, run_id, detector_version, confidence, uncertainty),
  `hotspot_evidence` (event_id, source, observed_at, available_at, ref).
- **API.** `GET /api/v1/hotspots`, `GET /api/v1/hotspots/{id}` with evidence and
  uncertainty; map cards read these.
- **Acceptance.** Replay cases for real signal, sensor-only spike, FIRMS-only
  fire, citizen-only false report, missing coverage, stale satellite, and
  adjacent-cell duplicates; precision/recall reported **only** where labels
  exist; time-leakage, stable ids, idempotency, status transitions, and
  map/API consistency tested; no precise PM2.5 for an unsensed cell.
- **Proof.** `O` for everything; `E` for real fire cycles.

### F6 — India regions and corridors (expand coverage)

- **Footing.** India-scoped map and search (geoBoundaries, GeoNames); a single
  configured bbox defaulting to Delhi NCR; `backhima` has one named corridor with
  **straight-line illustrative geometry** and an explicit
  insufficient-data verdict when withheld station labels are absent.
- **Gap.** No versioned region registry; ingestion/publication/alerts are not
  region-aware; no per-region coverage or last-successful-run reporting;
  illustrative shapes risk being read as authoritative.
- **New entities.** `region` (id, name, kind, bbox, h3_resolution, version,
  source, license, `geometry_source`), `region_run` (region, run_id, coverage,
  station_count, gaps).
- **API.** `GET /api/v1/regions`; `region` and `run_id` parameters on v2 reads;
  `GET /api/v1/regions/{id}/coverage`.
- **Acceptance.** Two disjoint regions plus one corridor replayed, including an
  overlap case; no cross-region alert leakage; consistent aggregation;
  documented limits for national queries; nationwide map discloses
  observed/modelled/demo/unsupported.
- **Proof.** `O` with fixtures; `E` for real per-region source cycles.

### F7 — AI prediction (make validated live inference)

- **Footing.** Ridge residual candidate, model registry, feature snapshots,
  manual promotion tooling, published `model_versions`; `backhima` adds a
  publication pipeline and federation-held-out evaluation. Deterministic
  dispersion remains the baseline.
- **Gap.** No observed-label training set, no temporal + station/spatial holdout,
  no calibration gate, no live activation, no monitoring, no model card.
- **New entities.** Extend `model_version` with `evaluation_report`, holdout
  definition, `activated_at`, `rolled_back_at`; new `model_evaluation` rows.
- **API.** `GET /api/v2/meta` already names the active model; add
  `GET /api/v1/models/{id}/card`.
- **Acceptance.** Reproducible training from pinned inputs; leakage guards;
  incompatible-schema rejection; artifact-tamper detection; promotion, rollback
  and visible fallback; a published live run naming the active model and
  differing from the baseline as expected. **If no real observed dataset passes
  the gates, leave the candidate unpromoted and state that live AI is not
  established.**
- **Proof.** `O` for training/validation/promotion logic; `E` for a real
  historical observed dataset, which is the binding constraint.

### F8 — Authority app alerts (simulated response loop)

- **Footing.** `backhima` has `incident` + `incident_delivery` + an actor
  registry enforcing role **and** jurisdiction, simulated (never real) delivery,
  an in-app inbox, and a new `partner_apps/fire_dept_simulator` Flutter app.
- **Gap.** The web incident notebook is still localStorage-only; escalation
  timers, dispatch-attempt records, agency isolation tests, and audit
  immutability need formal acceptance.
- **New entities.** `incident` (present), `incident_delivery` (present),
  `incident_event` (append-only audit), `agency`/`jurisdiction` registry.
- **API.** `GET/POST /api/v1/incidents`, `POST /incidents/{id}/acknowledge`,
  `/assign`, `/resolve`, `GET /incidents/inbox`. Writes require
  `X-Simulator-Key` **plus** `X-Actor-Id` resolved against a configured actor
  registry; a single shared key must never grant any role anywhere.
- **Acceptance.** End-to-end verified report/hotspot → routed incident →
  simulator receipt → acknowledgement → action → closure → dashboard update;
  wrong-jurisdiction, duplicate, retry, unauthenticated, agency-isolation,
  delayed-acknowledgement and audit-immutability tests. **No claim that a real
  authority was contacted.**
- **Proof.** `O` for the entire backend loop; `D` for the Flutter app on a
  device/emulator.

### F9 — Personal health alerts (make reliable)

- **Footing.** Flutter alert engine, notifications, forecast alarm scheduler,
  onboarding, sensitivity profile, secure storage; the app already reads
  `/api/v2/*` through `GridApiPollutionDataProvider`.
- **Gap.** Evaluations are not pinned to one published run; stale/low-coverage
  data is not visibly downgraded; alarm reconciliation on run/permission/
  location/profile change is incomplete; lock-screen privacy and in-app
  explanation need review.
- **New entities.** None required — this is client state; keep it minimal.
- **API.** Consume existing v2 meta/run metadata.
- **Acceptance.** Deterministic Dart tests for threshold edges, rapid rise,
  recovery, duplicate suppression, quiet-hour override, stale input, forecast
  revision, and alarm cancellation; `flutter analyze` and `flutter test` clean.
- **Proof.** `O` for the logic; `D` for permission granted/denied, app resume,
  app-closed-with-alarm, and tap-to-route.
- **Note.** The app **does not currently build** (§1.5). Fixing the missing
  imports is a prerequisite for any F9 claim.

### F10 — Decision map (harden and connect)

- **Footing.** Rich MapLibre layers, LOD, India search, alerts panel, fire and
  freight overlays; `backhima` adds `DataQualityNotice`, `RunStatusPanel` and
  `runFacts`.
- **Gap.** Every layer needs an observed/modelled/derived/illustrative label at
  point of use; mock fire anomalies and fixed freight nodes must be labelled or
  replaced; filters for status/severity/source/jurisdiction; accessibility;
  loading/empty/stale/failed states.
- **New entities.** None; consumes F5/F8 contracts.
- **API.** Existing v2 plus F5/F8 reads.
- **Acceptance.** Run-id consistency across layers; no false live labels;
  desktop and narrow-screen walkthroughs for live, demo, empty, failed-source
  and acknowledged-incident states; the map must not blend unrelated runs or
  make a mock look real.
- **Proof.** `O` for logic and a headless DOM pass; manual walkthrough needed
  for the visual/accessibility criteria.

### F11 — India federation (build across agencies)

- **Footing.** `backhima` has a versioned API with provenance and a
  demonstration federation: two client processes with distinct data stores, signed
  versioned updates, a real HTTP aggregator, held-out evaluation, and an
  explicitly simulated two-node claim.
- **Gap.** The brief needs a **node registry**, authenticated exchange, replay
  protection, idempotent imports, sync status/lag/failure visibility, a real
  federation badge, and **governed model sharing** (signed manifests,
  compatibility/evaluation gates, opt-in activation, rollback). The
  demonstration deliberately does not present its two clients as independent
  Indian agencies; the brief's framing is stronger and needs a different,
  explicitly simulated node identity.
- **New entities.** `federation_node` (id, region, public key, status, last_sync,
  lag, version), `federation_event` (node, type, payload, signature, replay
  nonce, imported_at), `model_artifact_manifest` (hash, signature, schema,
  evaluation summary, activation state).
- **API.** `POST /api/v1/federation/exchange`, `GET /api/v1/federation/nodes`,
  `POST /api/v1/federation/models/{id}/activate`.
- **Acceptance.** Two-node offline tests for exchange, repeated delivery,
  tampered signature/hash, incompatible schema, unavailable node, delayed data
  and privacy boundaries; prove a node can consume a permitted event/model
  **without** receiving private photos or raw profiles; audit record; real status
  UI. Document exactly which capability was achieved.
- **Proof.** `O` for the whole two-node exchange; `E` for any cross-node network
  deployment.

---

## 12. Release sequence

| # | Step | Gate | Notes |
| --- | --- | --- | --- |
| **0** | **Recover the `backhima` line** (8 commits, +29.7k lines) and merge to `main` without force | backend pytest at or better than today's 553/11; migrations `0011`–`0015` apply on a fresh PostGIS DB | **Blocking.** The remote branch is deleted; recover from the local clone or another holder's clone first. Without this, F1–F11 owners duplicate existing work |
| 0.1 | Fix the 3 missing Flutter imports so the citizen app builds | `flutter analyze` 58 → 0 errors; `flutter test` 8 → 0 load failures | Cheap, unblocks F2/F9 claims |
| 0.2 | Resolve the 11 inherited backend test failures | `pytest` 11 → 0 failed | Known root causes, all mechanical |
| 0.3 | Land a lint/format baseline decision | Either fix (79 + 36 files) or record a documented, enforced ratchet | Do not silently carry a red gate; do not let a feature PR grow a 36-file reformat diff |
| 0.4 | Correct the stale README claims (§1.6) | README matches code | Cheap; prevents downstream features building on wrong statements |
| 1 | **F1** citizen reports | F1 acceptance | Owner **TBD** |
| 2 | **F2** citizen photos | F2 | Needs F1's schema for linkage; privacy review |
| 3 | **F3** forecast and alerts | F3 | Unblocks live claims for F4/F6/F7 |
| 4 | **F4** satellite fusion | F4 | Gate any model use on observed-label ablation |
| 5 | **F5** hidden hotspots | F5 | Needs F1 (verified reports) and F4 (fire features) |
| 6 | **F6** regions and corridors | F6 | Needs F3 region-aware publication |
| 7 | **F7** AI prediction | F7 | Needs F6 per-region runs; may legitimately end unpromoted |
| 8 | **F8** authority app alerts | F8 | Needs F5 events and F1 verified reports |
| 9 | **F9** personal health alerts | F9 | Needs 0.1; consumes F3 run metadata |
| 10 | **F10** decision map | F10 | Consumes F5/F8 contracts; do before final demo polish |
| 11 | **F11** federation | F11 | Last: needs every other feature's contract stable |
| 12 | **Final** end-to-end verification | Backend pytest + Ruff, frontend build/lint/format, both Flutter apps, fresh-DB migration, and one integration scenario from photo-backed report → reviewed report → hotspot/forecast → routed incident → simulator acknowledgement → citizen alert → dashboard | Record unrun external/device checks explicitly |

F1 → F2 and F1 → F8 are the tight couplings; F3 → F4 → F7 and F5 → F8/F10 are the
long chains.

---

## 13. Cross-cutting rules

### 13.1 Source and mode rules

Every new environmental value must carry: source, observation time
(`acquired_at`), availability time (`available_at`), region, provenance, and
demo/live mode. Rules the codebase already enforces and every feature must
preserve:

- Never present synthetic, interpolated, or satellite-proxy data as a measured
  ground reading.
- A value is usable only if `available_at <= <the run's issue time>` — the
  as-of/lookahead guard, which F4 and F5 must test explicitly.
- `live` mode may not contain synthetic inputs; `mixed` must be visible as such.
- `missing`, `stale`, `empty` (a valid zero) and `failed` stay distinct; never
  collapse a failure into "no data".
- A candidate/hotspot is not a PM2.5 measurement and not a source attribution.
- A scheduled demo run stays explicitly demo.

### 13.2 Migration and compatibility strategy

- One hand-written Alembic revision per change; **never edit an existing
  revision** — the README documents the `weather_reading` incident that proved
  this. `0011`–`0015` already exist on the `backhima` line, so **new work
  starts at `0016` after step 0.**
- Revision ids ≤ 32 characters (`alembic_version.version_num` is `VARCHAR(32)`).
- Reversible: additive columns with server defaults, new tables, and explicit
  downgrades. No destructive change to a table a deployed app reads.
- `/api/v1` clients keep working. Extend `/api/v2` or add endpoints; version a
  contract only when it must change, and have v1 delegate to the new service.
- `tests/test_migrations_offline.py` and `test_schema_compiles.py` must be
  updated in the same commit as a schema change — and both currently fail.

### 13.3 Authentication and authorization boundary

| Surface | Today | Target |
| --- | --- | --- |
| Read `/api/v1/*`, `/api/v2/*` | public, unauthenticated | stays public for the hackathon; document it |
| Write `POST /api/v1/reports` | **open** | rate/abuse limits, geofence, lifecycle, moderation — not identity |
| Media/evidence | none on `main` | reviewer-scoped; no public object URLs; no raw credentials in config |
| Incidents (`backhima`) | `X-Simulator-Key` + `X-Actor-Id` against an actor registry enforcing role **and** jurisdiction | keep; add audit immutability and agency isolation |
| Federation | per-participant HMAC registry | add node registry, replay protection, idempotent imports |
| Any real emergency service | **none** | explicitly out of scope; the F8 app is a simulator and must never imply a real dispatch |

### 13.4 External credentials and licensing

| Need | Used by | Status |
| --- | --- | --- |
| `DATABASE_URL` (Neon, direct) | pipeline workflow, migrations | required secret |
| `OPENAQ_API_KEY` | live PM2.5 | required for `DEMO_MODE=false` |
| `FIRMS_MAP_KEY` | FIRMS ingestion | **undocumented in README** (§1.6) |
| `GIBS_BASE_URL` | raster tile proxy | NASA GIBS, public; terms review before any redistribution |
| `NO2_WMS_URL`/`_LAYER`/`_TOKEN` | NO2 layer | unset ⇒ 404, layer stays inert (correct) |
| Traffic feed | congestion features | licensed; unset ⇒ features stay `null` |
| Static cells (population/land use) | exposure | licensed; unset ⇒ `missing`, never invented |
| Fire-department simulator | F8 | **simulated only**; no agency credential exists or is required |

Existing licensed data already in the repo: geoBoundaries ADM1/ADM2
(ODC-ODbL), GeoNames (CC BY 4.0), Natural Earth roads (public domain).

---

## 14. Offline vs external vs device

| Outcome | Offline (`O`) | Needs external service (`E`) | Needs device (`D`) |
| --- | --- | --- | --- |
| F1 lifecycle, geofence, abuse, moderation, gating | ● | — | Flutter submission leg |
| F2 content validation, durability, retry, deletion | ● | object storage for the real adapter | camera/gallery, reviewer view |
| F3 source-window matrix, run pinning, replay | ● | one live OpenAQ/Open-Meteo cycle | — |
| F4 parsing, no-detect vs failed, as-of join, ablation | ● | live FIRMS; licensed AOD/NO2 | — |
| F5 replay cases, event lifecycle, dedup, precision/recall on fixtures | ● | real fire cycles | — |
| F6 two-region replay, overlap, leakage, coverage | ● | real per-region source cycles | — |
| F7 training, holdouts, tamper, promote/rollback/fallback | ● | **a real historical observed dataset** | — |
| F8 full incident loop, jurisdiction, audit, isolation | ● | — | simulator app on a device |
| F9 threshold/recovery/dedup/quiet-hour/alarm tests | ● | — | permission, resume, closed-app, tap-through |
| F10 run-id consistency, filters, labels, states | ● (DOM) | — | visual + screen-reader walkthrough |
| F11 two-node exchange, tamper, replay, privacy boundary | ● | cross-node deployment | — |

**The two genuinely external blockers** are F7's observed historical labels and
F4's licensed quantitative product. Everything else is provable offline with
fixtures; the honest report for any feature is which column it landed in.

---

## 15. Hand-off to the F1 owner

**Owner: TBD.** The scope is written so any owner can start.

1. **Start from `main` *after* step 0.** Do not begin F1 on bare `main`:
   `b6c003f` and `71aea3d` already contain `report_evidence`, `media_storage`
   and `citizen_intake`, and the merge is unavoidable.
2. **Read first:** `docs/api/citizen-intake.md` (on `backhima`),
   `app/services/reports.py`, `app/services/fire_gradient.py`,
   `app/api/routes/reports.py`, `fire_report` in `app/models/tables.py`,
   `frontend/src/components/ReportFireForm.tsx`,
   `partner_apps/air_health_flutter/lib/features/reports/report_fire_sheet.dart`,
   and `frontend/src/lib/reportValidation.ts`.
3. **Decide first:** the lifecycle column set and the default state; the
   geofence definition of "India"; the abuse-control mechanism (per-IP
   submission rate is the minimum); and the exact predicate under which a report
   is allowed to influence the plume.
4. **Then implement** per the F1 scope in §4–11, run the checks, and report
   scope/design, changed files, migrations and configuration, tests and results,
   manual demo evidence, and limitations — and do not mark F1 complete while any
   acceptance criterion is unverified.
5. **Never commit** `partner_apps/air_health_flutter/pubspec.lock`.
