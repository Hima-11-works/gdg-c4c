# Implementation scope — India pollution platform, F1–F11

**Stage:** Prompt 0 (scope and architecture). No application behaviour is
changed by this document; it adds no code and no schema.

| | |
| --- | --- |
| Scoped against | `main` @ `78cb15a` (HEAD when this revision was written; every gate in §1.5 was re-measured on it) |
| Brief | `india_pollution_implementation_prompts.md` (received out-of-band, 2026-09-26) |
| Assessment baseline in the brief | `origin/main` @ `1685e85` — **reached and verified**; the brief writes it as `1685e855`, which is one character longer than the real abbreviated SHA and resolves to nothing (§1.3) |
| F1 owner | **UNASSIGNED — blocking.** §15 defines the hand-off contract; naming the owner is the one thing this pass cannot decide |

---

## 1. Verification (what I actually ran and read)

### 1.1 Repository state

| Item | Finding |
| --- | --- |
| Branch | `main`, tracking `origin/main`, **0 ahead / 0 behind** |
| HEAD | `78cb15a` — `Correct the scope doc after rebasing onto the real assessment baseline`; the two commits before it are earlier revisions of this same document (§1.3) |
| Worktree | **Not clean.** Three *untracked* files, all from a parallel effort and **left untouched by this pass** (§1.4) |
| Stashes | 4 pre-existing (`satellite AOD fix`, incidental `pubspec.lock`, etc.) — untouched |
| Repository instructions | **None.** No `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` or `.cursorrules`. `README.md` is the developer reference; `tests/test_architecture.py` is the only mechanically enforced rule (layer imports) |
| Local branches | `backhima` @ `6631397`, `himanshi` @ `b044b18` — **both remote branches are deleted upstream**; only `origin/main` exists |

### 1.2 Tooling

| Tool | Version | Available |
| --- | --- | --- |
| Python | 3.14.7 (`.venv`) | yes |
| pytest | 9.1.1 | yes |
| Ruff | 0.16.8 | yes, but **only inside `backend/.venv`** — not on `PATH`; invoke as `backend/.venv/Scripts/ruff.exe` |
| Node / npm | 24.16.0 / 11.13.0 | yes |
| Flutter / Dart | Flutter at `C:\src\flutter`, Dart 3.13.3 | yes |
| Postgres / PostGIS | **no local instance** | DB-backed tests are skipped (31 of them) |


### 1.3 Changes since the assessment baseline: none on `main`

The brief is written against `origin/main` @ `1685e855`. The real abbreviated
SHA is **`1685e85`** — the brief has one character too many, and `1685e855`
resolves to nothing in this repository. That is worth recording, because an
owner who copies the SHA from the brief will conclude the baseline is missing
when it is simply misquoted.

With the typo corrected, `1685e85` was the tip of `origin/main` when this
document was first written. **`origin/main` is now `78cb15a`**, and the two
commits since `1685e85` are both revisions of *this document*:

| Commit | What it is |
| --- | --- |
| `2e89f24` | `Scope F1-F11 before any implementation (Prompt 0)` — added this document |
| `78cb15a` | `Correct the scope doc after rebasing onto the real assessment baseline` — rebased onto `1685e85`, re-ran the frontend gates |

So **no teammate commit has landed on `main` since the assessment.** The brief's
per-feature "current footing" column can be read directly against `main` as it
stands, with one qualification:

> One commit landed on `origin/main` *during* the first scoping pass —
> `1685e85` `[fix] Stop the map drawing cells and the smooth field outside
> India` (+349/−2 across `frontend/src/components/MapView.tsx` and the new
> `frontend/src/lib/indiaOutline.ts`). The document was rebased onto it and the
> frontend gates were re-run (§1.5).

The substantive movement since the assessment is therefore **not** on `main` at
all — it is the unmerged `backhima` line (§1.4).


### 1.4 Finding: a large body of F1–F11 work is not on `main`

`backhima` is **8 commits ahead of `main`** — `git diff --shortstat main..backhima`
reports **133 files changed, 29,726 insertions, 1,181 deletions** as of `main`
@ `78cb15a` — and its remote branch has been deleted. (That deletion count is
`main`-relative: part of it is `1685e85`'s MapLibre India-outline fix, which
`backhima` predates. An earlier pass measured this against an older `main` and
recorded "131 files, −216", which understated the divergence.) It already
contains work that maps onto most of the brief:

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
the release sequence (§12) is to recover and merge this line — and the merge will
need a real decision about `1685e85`, which `backhima` does not contain. It is a
**demonstration-grade** implementation, not brief compliance: hotspots are
imagery-driven scans written to disk with authored fixtures (no persisted event
lifecycle), federation is two local processes (no node registry), corridors are
straight-line illustrative geometry. Each feature in §4–§11 states what the
backhima line already covers and what the brief still requires.

#### Second collision risk: uncommitted work in the working tree

While this pass was running, three files appeared in the worktree, untracked and
**left untouched**:

```
?? backend/app/domain/india.py                   6.7 KB  2026-09-26 00:53:23
?? backend/app/domain/data/india_geofence.json  317 KB  2026-09-26 00:53:31
?? scripts/build-india-geofence.py               9.3 KB  2026-09-26 00:53:48
```

They implement **exactly the F1 gap "no India geofence"**: a server-side
point-in-polygon test against **ADM1 states and union territories** (deliberately
not the dissolved country outline, which omits small UTs such as Diu), fed by a
geofence generated by `scripts/build-india-geofence.py` from published boundaries.
The stated motivation is that `POST /api/v1/reports` is open and unauthenticated,
so report coordinates are attacker-controlled.

This is **work in progress by a parallel effort**, not a reviewer's change to
make. Two consequences, both carried into the baseline (§1.5) and the hand-off
(§15):

* The Ruff and format baselines are each **+1** versus the committed tree, and
  both come from this one file. They must not be "fixed" as inherited debt.
* F1 should **adopt and finish** it rather than write a second geofence. Its
  remaining F1 obligations — lifecycle, rate/abuse control, moderation, and the
  predicate for whether a report may influence the plume — are untouched by it.

### 1.5 Baselines (re-measured on `main` @ `78cb15a`)

Every row below was re-run by this pass on the current HEAD. Figures are given
for the **committed tree**, with the working-tree delta attributed separately,
because three untracked files (§1.4) are physically present and would otherwise
be silently counted as inherited debt.

| Gate | Command | Result |
| --- | --- | --- |
| Backend tests | `cd backend && pytest -q` | **553 passed, 11 failed, 31 skipped** (12.2s) — exit 1 |
| Backend lint | `ruff check .` | **80 errors**, 20 auto-fixable — exit 1 (**79 on the committed tree**) |
| Backend format | `ruff format --check .` | **37 files** would be reformatted, 126 clean — exit 1 (**36 on the committed tree**) |
| Frontend build | `npm run build` | **pass**, exit 0 — 63 modules; `index-*.js` 1,545 kB / 438 kB gzip, chunk warning |
| Frontend lint | `npm run lint` (oxlint) | **pass**, exit 0 — 1 warning, 0 errors, 47 files, 116 rules |
| Frontend format | `npx prettier --check .` | **23 files** unformatted — exit 1 |
| Flutter analyze | `flutter analyze` | **58 issues** (21 error / 7 warning / 30 info) — exit 1 |
| Flutter test | `flutter test` | **180 passed, 8 failed**, all 8 compile/load failures — exit 1 |

Ruff is **not on `PATH`**; it is `backend/.venv/Scripts/ruff.exe` at 0.16.8. Committed-tree
error mix: 58 × E501, 10 × I001, 6 × UP035, 3 × F401, 1 × B904, 1 × UP037. Hottest files:
`app/api/routes/predictions_v2.py` (17), `app/services/prediction_queries.py` (10),
`app/models/tables.py` (8), `app/services/prediction_publication.py` (6).

**The +1 lint and +1 format finding are both `app/domain/india.py`** — the
untracked geofence file from §1.4 (`india.py:114` is one line too long, and the
file is unformatted). Nothing inherited changed: the committed tree is still 79
errors and 36 unformatted files.

Prettier's 23 includes `frontend/src/lib/indiaOutline.ts`, added by `1685e85`
itself unformatted, so the format debt grew with that fix. Build and lint are
unaffected (exit 0).

The **11 backend failures are inherited, not new**, and are the same 11 the
`backhima` line also fails: `test_api_grid` (2), `test_environmental_contracts`
(1), `test_forecasting_service` (4), `test_migrations_offline` (1),
`test_repository_statements_compile` (1), `test_schema_compiles` (1),
`test_training_data` (1). Root causes are visible: `_FakeModel.forecast()` missing
`step_minutes`, `alert.forecast_hours` SMALLINT vs FLOAT, and a stale expected
table set.

**The Flutter failures are code defects, and the previous diagnosis in this
document was wrong.** An earlier pass recorded them as three missing imports
("all three classes exist"). Re-running the gates and reading the compiler output
shows four distinct root causes, only one of which is a missing import:

1. **A one-character syntax error makes the live v2 adapter unparseable.**
   `lib/data/grid/grid_api.dart:393` reads `  ) async {` — the closing `}` for
   the named-parameter group opened on line 391 is simply absent. Confirmed two
   ways: `dart analyze` reports `grid_api.dart:393:3 — Expected to find '}'`, and
   `dart format` refuses the file outright ("the source could not be parsed").
   A brace-balance scan shows `class DioGridApiClient` (opened line 263) never
   closes. This single missing brace is why **4 test files plus
   `acceptance_test.dart` fail to load**, and why the citizen app cannot compile
   its live API path at all — the dummy provider is not a preference, it is the
   only thing that can run.
2. **A real contract mismatch**, not an import: `fire_report_api.dart:108,117`
   return `ReportEnvelope<FireReport>` / `ReportEnvelope<List<FireReport>>` from
   methods typed `Future<FireReport>` / `Future<List<FireReport>>`. The adapter
   does not unwrap the platform's `{generated_at, is_demo, data}` envelope.
3. **A missing import plus a signature drift**: `forecast_alarm_scheduler.dart`
   cannot resolve `SensitivityRules` *and* fails on
   `Required named parameter 'freshness' must be provided`. Fixing the import
   alone will not make it compile.
4. **Plugin API drift against the locked dependency**: with
   `flutter_local_notifications 19.5.0`, `notification_service.dart` calls
   `canScheduleExactAlarms` (undefined), `.alarm` (member not found) and
   `uiLocalNotificationDateInterpretation` (undefined). This is a dependency
   problem, not a source problem, and F9 owns it.

Only 180 of 188 tests run at all today. No Flutter gate can be quoted as a
baseline for F2 or F9 until (1) and (2) are fixed.

`flutter pub get` rewrites `partner_apps/air_health_flutter/pubspec.lock` (it
dropped 37 entries and changed 37 dependencies in this pass). It was restored and
is **not** part of this change; it must never be committed (it is already in a
stash as "incidental").


### 1.6 Stale README claims, resolved against code

| README claim | Reality on `main` |
| --- | --- |
| "five tables" in `app/models/tables.py` (line 625) | **14** `Table(...)` declarations; migrations `0001`–`0010` |
| Pipeline makes "1h/3h/6h forecasts" (lines 52, 698, 1193) | `app/pipeline/run.py` calls `service.run(hours=[i * 0.25 for i in range(1, 25)], step_minutes=15)` — **24 quarter-hour horizons**, `0.25`–`6.0` |
| "All five stages should print `[OK  ]`" (line 165) | **six** stages — `_seed_fire_reports` was added; `_print_report` prints `[OK  ]`/`[FAIL]` per stage |
| "**No scheduler**" (lines 62, 259, 684, 726, 1245) | `.github/workflows/pipeline.yml` **is** the scheduler: `cron: "0 * * * *"` + `workflow_dispatch`, then `alembic upgrade head` and `python -m app.pipeline.run`. The README never mentions the file |
| Env-var table | Omits `FIRMS_*`, `GIBS_BASE_URL`, `NO2_WMS_*`, `TILE_*`, `TRAFFIC_STALE_AFTER_HOURS`, `FIRE_*`, `PDI_FIRE_PRESSURE_WEIGHT`, `DATABASE_URL`. (`.env.example` itself is complete and exact: **71 keys = 71 `Settings` fields**, re-verified) |
| Directory-tree comment for `routes/` (line 416) lists "sensors, weather, grid, cells, alerts, health" | Ten route modules exist; the comment **omits `fires.py`, `reports.py`, `tiles.py`, `predictions_v2.py`**. Note the *endpoint tables* further down are complete and accurate — all 18 routes, including both v1 reports/fires and all seven v2 routes, are documented there. Only the tree comment is stale |
| Repository structure | Omits `partner_apps/air_health_flutter` entirely, and the v2 + tiles route modules |
| "No authentication … every route on `/api/v1/*` and `/api/v2/*` is open" (lines 279, 1246) | **True on `main`** — verified: no route module on `main` takes a `Depends(require_*)` auth dependency. Already false on `backhima` (simulator key + actor registry), so the claim must be qualified per branch |
| Flutter README: "Dummy provider active today; Remote is a Dio skeleton" | **Stale, but read the caveat.** `data_providers.dart` returns `GridApiPollutionDataProvider(client: client)` whenever `gridApiClientProvider` is non-null and the dummy otherwise; `grid_api.dart` pins `_apiPrefix = '/api/v2'` and `DioGridApiClient implements GridApiClient`. So the adapter is written and selected, not a skeleton — **but it does not compile** (missing `}` at `grid_api.dart:393`, §1.5). The dummy is the only provider that can actually run today |

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
  `CITIZEN_MEDIA_STORAGE=disabled` by default (503 `media_unavailable`,
  sensor-only still works), a `verify-media-storage` CLI, and a 503-not-404 rule
  for recorded-but-missing bytes.
- **Gap.** Capture UI, consent, retention/deletion jobs, reviewer access control,
  safe re-encoding, malware/quarantine, and orphaned-file cleanup on failure. The
  Flutter app also cannot demonstrate any of it until step 0.1 lands (§1.5).
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
  onboarding, sensitivity profile, secure storage. The app is *written* to read
  `/api/v2/*` through `GridApiPollutionDataProvider`, but that adapter does not
  compile (§1.5), so today the app runs on the dummy provider.
- **Gap.** Evaluations are not pinned to one published run; stale/low-coverage
  data is not visibly downgraded; alarm reconciliation on run/permission/
  location/profile change is incomplete; lock-screen privacy and in-app
  explanation need review. **`notification_service.dart` also targets a plugin API
  that the locked `flutter_local_notifications 19.5.0` does not expose**
  (`canScheduleExactAlarms`, `.alarm`, `uiLocalNotificationDateInterpretation`) —
  F9 owns that dependency decision, not a source fix.
- **New entities.** None required — this is client state; keep it minimal.
- **API.** Consume existing v2 meta/run metadata.
- **Acceptance.** Deterministic Dart tests for threshold edges, rapid rise,
  recovery, duplicate suppression, quiet-hour override, stale input, forecast
  revision, and alarm cancellation; `flutter analyze` and `flutter test` clean.
- **Proof.** `O` for the logic; `D` for permission granted/denied, app resume,
  app-closed-with-alarm, and tap-to-route.
- **Note.** The app **does not currently build** (§1.5, root causes 1–4). Step 0.1
  is a prerequisite for any F9 claim, and root cause 4 may force a plugin
  upgrade or a downgrade of the calls — decide that before writing new alarm
  logic, not after.

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
| **0** | **Recover the `backhima` line** (8 commits, +29.7k lines) and merge to `main` without force, resolving `1685e85` (which `backhima` lacks) | backend pytest at or better than today's 553/11; migrations `0011`–`0015` apply on a fresh PostGIS DB | **Blocking.** The remote branch is deleted; recover from the local clone or another holder's clone first. Without this, F1–F11 owners duplicate existing work |
| 0.1 | Fix the citizen app so it compiles: the missing `}` at `grid_api.dart:393`, then the `ReportEnvelope` unwrap in `fire_report_api.dart` | `flutter analyze` 21 errors → 0; `flutter test` 8 load failures → 0 | **One character for the first one.** A previous pass mis-diagnosed these as missing imports; §1.5 gives the four real causes. Until this lands, the Flutter app runs on dummy data only, so no F2 or F9 claim can be demonstrated on a device |
| 0.2 | Resolve the 11 inherited backend test failures | `pytest` 11 → 0 failed | Known root causes, all mechanical |
| 0.3 | Land a lint/format baseline decision | Either fix (79 + 36 files) or record a documented, enforced ratchet | Do not silently carry a red gate; do not let a feature PR grow a 36-file reformat diff. Re-measure after the `backhima` merge, which brings its own files |
| 0.4 | Correct the stale README claims (§1.6) | README matches code | Cheap; prevents downstream features building on wrong statements. Correct the "no scheduler" claim first: it is the one that misleads an operator |
| 1 | **F1** citizen reports | F1 acceptance | Owner **unassigned** (§15) — and it must first adopt the untracked geofence work (§1.4) |
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

**One precondition sits outside all three columns.** Every `D` cell assumes the
citizen app compiles, and today it does not (§1.5, step 0.1). Until that is
fixed, the `D` column for F1, F2 and F9 is *unreachable*, not merely unrun — the
app cannot be installed against the live API at all. A feature report that claims
device evidence while `grid_api.dart` still has a parse error is not honest, so
step 0.1 gates the whole device column rather than just the Flutter tickets.


---

## 15. Hand-off to the F1 owner

**Status: the scope is complete and committed; the owner is the one open item.**
Assigning a person is a human decision this pass cannot make, so it is recorded
as a blocker rather than papered over with a name. Everything else below is
ready to execute the moment an owner is named.

**Owner: UNASSIGNED.** Needed from whoever runs the pack: a name, and a decision
on whether F1 is owned by the person who already has the geofence work in
progress (§1.4) or by someone starting clean — that choice changes step 1.

### Entry checklist for whoever picks it up

1. **Start from `main` *after* step 0.** Do not begin F1 on bare `main`:
   `b6c003f` and `71aea3d` already contain `report_evidence`, `media_storage`
   and `citizen_intake`, and the merge is unavoidable. An F1 owner who starts on
   bare `main` will collide with both that and the untracked geofence work.
2. **Adopt, do not duplicate, the geofence.** `backend/app/domain/india.py` +
   `data/india_geofence.json` already implement ADM1 point-in-polygon. F1's
   remaining obligations are lifecycle, rate/abuse control, moderation, and the
   plume-gating predicate — none of which that work touches.
3. **Read first:** `docs/api/citizen-intake.md` (on `backhima`),
   `app/services/reports.py`, `app/services/fire_gradient.py`,
   `app/api/routes/reports.py`, `fire_report` in `app/models/tables.py`,
   `frontend/src/components/ReportFireForm.tsx`,
   `partner_apps/air_health_flutter/lib/features/reports/report_fire_sheet.dart`,
   and `frontend/src/lib/reportValidation.ts`.
4. **Decide first, in this order** — each is a contract other features inherit:
   1. the lifecycle column set and the **default state** for an existing row;
   2. the **plume-gating predicate**: the exact condition under which a report may
      alter the modeled field (this is the acceptance criterion most likely to be
      got wrong, and it is what makes an uncorroborated report harmless);
   3. the **abuse-control** mechanism — per-IP submission rate is the minimum,
      and note that no route is authenticated, so the control cannot assume
      identity;
   4. whether moderation needs a real role or a shared reviewer key, given that
      §13.3 keeps reads public.
5. **Then implement** per the F1 scope in §4–11, run the gates in §1.5, and report
   scope/design, changed files, migrations and configuration, tests and results,
   manual demo evidence, and limitations — and do not mark F1 complete while any
   acceptance criterion is unverified.
6. **Never commit** `partner_apps/air_health_flutter/pubspec.lock`.

### What the F1 owner inherits, stated plainly

| Inherited | State |
| --- | --- |
| `fire_report` table | 11 columns, **no status column**, `UNIQUE(client_report_id)`, spatial index, age-bounded |
| `POST /api/v1/reports` | unauthenticated, no geofence, no rate limit, no moderation, response keys are contract-locked by `tests/test_api_reports.py` |
| Plume coupling | an unverified report alters the modeled field **immediately** — the behaviour F1 must gate |
| Report UI | web `ReportFireForm` and Flutter `ReportFireSheet` both exist and post today |
| `report_evidence` + `citizen_intake` | on `backhima` only; lands with step 0 and gives F1 its verification-status vocabulary |
| India geofence | untracked, in progress, ADM1-based, untested by any committed test |
| Baseline | 553 passed / 11 failed / 31 skipped; Ruff 79 errors and 36 unformatted **on the committed tree** |

