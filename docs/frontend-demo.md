# Frontend demos

One walkthrough, end to end, across everything built here:

**published India scenario → exposure map → fire evidence → forecast alert →
persistent incident → Flutter responder update → web status → federation
demonstration.**

Everything in it runs against **one backend**. Read the walkthrough first; the
sections after it are the reference detail behind each step.

---

## The complete walkthrough

### 0. One backend, three features

The walkthrough needs a backend that serves the v2 prediction reads *and* the
incident workflow *and* the federation status. That is the published backend
branch, not necessarily the branch this dashboard lives on. Host it without
touching your working tree:

```bash
# a detached worktree of the published backend branch, outside the repo
git worktree add --detach "$TEMP/published-wt" origin/backhima

cat > "$TEMP/published-wt/.env" <<'ENV'
POSTGRES_USER=pollution
POSTGRES_PASSWORD=princi
POSTGRES_DB=pollution
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
API_PORT=8001
H3_RESOLUTION=8
SIMULATOR_API_KEY=sim-local-dev-key
# The dashboard's dev-server origin. Without this the browser blocks every
# read and the dashboard shows empty panels — the API itself is fine.
CORS_ORIGINS=http://localhost:5173,http://localhost:5174
ENV

docker compose -p gdg-demo --env-file "$TEMP/published-wt/.env" \
  -f "$TEMP/published-wt/docker-compose.yml" up -d --build
```

Migrations run on boot. `/health/ready` tells you when it is up. Then point the
dashboard and the simulator at it:

```bash
cd frontend
VITE_API_BASE_URL=http://localhost:8001 npm run dev -- --port 5174
```

### 1. Published India scenario

The dashboard reads `GET /api/v2/*` and pins every read to one published run
(`latest_run_id` from `/api/v2/meta`). Publish a population-backed demo run so
the next step has something to show:

```bash
docker exec gdg-demo-api-1 sh -c "python -m app.cli demo-features \
  --profile regional-demo --replay-hour 24 --history-hours 6 \
  --out /tmp/demo-features.json"

docker exec gdg-demo-api-1 sh -c "python -m app.cli prediction-publish \
  --input /tmp/demo-features.json --feature-run-id demo-pop \
  --run-id demo-population-f5 --mode demo --region india \
  --scenario-id winter_stagnation --generated-at 2026-09-24T09:31:00Z"
```

Open `http://localhost:5174`. The **Published run** panel (top right) reports the
run time, demo/live status, source age, coverage and prediction method for that
exact run, and the banner marks it as a demo simulation.

### 2. Exposure map

Bottom-left panel → **Measure → Population-weighted PM2.5**. The legend swaps to
the exposure key (cool ramp, same µg/m³ bands, plus a *No population estimate*
row) and the run panel's *Population* row confirms the run carries population.
Search a place to zoom in: the cells colour by the backend's
`population_weighted_pm25`, which differs from the area-averaged PM2.5 in the
same cells.

### 3. Fire evidence

Turn on **Citizen Fire Reports**, then file a report at a spot you are looking
at and reopen that cell:

```bash
curl -s -X POST localhost:8001/api/v1/reports -H 'Content-Type: application/json' \
  -d '{"latitude":28.6139,"longitude":77.2090,"kind":"crop_burning","smoke_intensity":4,"duration_hours":2,"notes":"Stubble burning behind the market"}'
```

Click that cell: the drawer shows the **Citizen report** with its smoke,
duration and age, labelled **Unverified — resident submitted, not a measurement**.

### 4. Forecast alert

The **alerts bell** (beside the search box) lists the run's alerts, each with the
current value and a forecast horizon, framed as a **Pollution-control response**
(an area, not a source). On the timeline, drag to a frame the publication did not
forecast at — the horizon label reads e.g. **`+2 HR 15 MIN interpolated`**, and
the banner says the same: interpolated between published anchors, no calibrated
interval.

### 5. Persistent incident

Create the incident from the report filed in step 3:

```bash
KEY=sim-local-dev-key

curl -s -X POST localhost:8001/api/v1/incidents \
  -H "X-Simulator-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"source_type":"report","source_id":3,"severity":"critical","jurisdiction":"Delhi",
       "linked_prediction_run_id":"demo-population-f5","evidence_report_ids":[3],
       "latitude":28.6139,"longitude":77.2090}'
```

`201` with `status: "reported"`, `responder_role: "fire_department"` (a fire
report routes to the fire department; a `kind: "other"` report routes to
pollution control). Running it twice returns `200` with the same id — creation is
idempotent on `(source_type, source_id)`.

### 6. Flutter responder update

```bash
cd partner_apps/fire_dept_simulator
flutter run --dart-define=INCIDENT_API_BASE_URL=http://localhost:8001 \
            --dart-define=SIMULATOR_API_KEY=sim-local-dev-key
```

Open the incident in the queue, then walk the response: **Assign to a unit** →
**Acknowledge** → **Mark en route** → **Mark on scene** → **Mark resolved**. Each
tap re-reads the incident from the server, reports what actually happened, and
appends one row to the event history. Every screen carries the simulation banner.

### 7. Web status

The state the app wrote is the state the API returns — the app writes rows and
nothing else. Refresh a browser against:

```
http://localhost:8001/api/v1/incidents/3
http://localhost:8001/api/v1/incidents/3/history
http://localhost:8001/docs
```

Expected history: `created`, `assigned`, then one `transition` per state change,
ending at `resolved` with `resolved_at` set.

### 8. Federation demonstration

Run the two-region demonstration and the dashboard's top-right pill picks it up:

```bash
docker exec gdg-demo-api-1 sh -c "python -m app.cli federation-demo --out-dir /tmp/federation"
```

The pill reads **Federation demo: 2 regions · succeeded** — the real
`GET /api/v1/federation/status`, not the fixed placeholder it used to show.
Expanding it shows the participating regions, the latest aggregation time, the
model versions, and the demonstration's own caveats: the scope is
`two-partition-synthetic-demonstration`, the aggregate is synthetic-only, the
evaluation is *not usable as real-world evidence*, zero raw observation rows
reached the aggregator, and the recorded privacy/geography/accuracy limitations
appear word for word. Before any run the pill says *no demo run recorded*; if the
endpoint cannot be read it says *status unavailable*, and never conflates the
two.

### What this walkthrough verified, and what it could not

Steps 1–4 and 8 were driven in a browser against the live backend (screenshots
in `docs/` history of the change); steps 5 and 7 were exercised over HTTP with
the exact requests the app makes; step 6's **contract** was verified the same
way, but **the Flutter UI itself was not run** — there is no Dart/Flutter SDK on
the machine this was built on, so `flutter analyze` and `flutter test` are still
outstanding. See the limitations at the end of this document.

---

# Web dashboard reference: one published run, two measures

## What the dashboard states about the run

The **Published run** panel reports five facts, all read off the same response
the map is drawn from — no client-side inference:

| Fact | Field |
| --- | --- |
| Run time | envelope `generated_at` (absolute + relative) |
| Demo / live status | envelope `mode`, plus `is_demo` for the demo warning |
| Source age | `metadata.quality.max_observation_age_hours` (oldest across cells) |
| Coverage | envelope `coverage` (covered fraction, returned/requested cells, resolution) |
| Prediction method | `metadata.prediction_method` (+ `model_version`, `input_kind`) |

`lib/runFacts.ts` derives all of this in one pure function so the panel, the
empty states and the banners cannot drift apart.

## The two measures

- **PM2.5** — the area-averaged concentration, on the CPCB ramp.
- **Population-weighted PM2.5** — the backend's exposure field
  (`ExposureOut.population_weighted_pm25`), on its own cool cyan → violet ramp
  over the same µg/m³ bands. A crowded cell dominates; an empty one counts for
  nothing. It is a population-weighted *concentration*, not a medical dose
  estimate.

The measure applies to every rendering path: hex cells, the smooth raster, and
the contrast-mode range contours. Forecast playback switches the value too.

## The federation pill

`GET /api/v1/federation/status`, public, no key. Three states are kept apart on
purpose: a recorded run, **no run recorded**, and **the endpoint being
unreachable**. Only the last is an error, and an unavailable endpoint is never
rendered as "no run" — that would be a claim about the backend rather than about
the connection.

Two details worth knowing when reading the payload:

- **`model_versions` has two shapes.** A freshly demonstrated run (and the
  contract's example) returns a list of objects with `status` and
  `synthetic_only`; a run read back from the database returns only
  `{"model_ids": [...]}`. The panel handles both and, for the persisted shape,
  says *per-model status not reported for a persisted run* rather than implying
  one.
- **Nothing in the payload claims privacy, geography or accuracy**, and neither
  does the panel: `region_scope`, `synthetic_only`,
  `evaluation.usable_as_real_world_evidence` and the `limitations` block are
  shown as the server sent them.

## Empty states

- **No population to weight by.** Shown in exposure mode when no cell in the
  read carries a population-weighted value, distinguishing "the run has no
  population estimates at all" from "no cell in this frame has both".
- **Stale data.** Shown when the run's `generated_at` is more than 24h old, or
  the newest observation behind it is more than 24h old. The notice names the
  age rather than only asserting staleness.

Thresholds live in `lib/format.ts` (`STALE_RUN_HOURS`, `STALE_SOURCE_HOURS`).

## Reproducing the run states

The dashboard shows whatever the latest published run is.

```bash
# 1. A population-backed demo run (the manifest's regional-demo scenario)
docker exec gdg-demo-api-1 sh -c "python -m app.cli demo-features \
  --profile regional-demo --replay-hour 24 --history-hours 6 \
  --out /tmp/demo-features.json"

docker exec gdg-demo-api-1 sh -c "python -m app.cli prediction-publish \
  --input /tmp/demo-features.json --feature-run-id demo-pop \
  --run-id demo-population-1 --mode demo --region india \
  --scenario-id winter_stagnation --generated-at 2026-01-01T00:00:00Z"

# 2. A stale run: the same features published with an old generated_at
#    (--generated-at 30 days ago), then a fresh run published after it.

# 3. A missing-population run: any run id the backend serves from its
#    no-database fallback, e.g. demo-fallback-<YYYYMMDDTHH>Z
```

To view a specific run without republishing, pin the run id — every v2 read
accepts `run_id=`, and the frontend takes its run id from `/api/v2/meta`, so a
request rewrite of `latest_run_id` is enough.

## Web limitations

- **The demo run is regional.** `regional-demo` is a Delhi-NCR scenario (256
  res-8 cells) published under `region: india`. At the country tier it covers one
  res-3 cell; that is the run's real coverage and the panel says so.
- **Source age often reads "Not reported".** The demo features carry
  `max_observation_age_hours: 0.0`, and the backend's aggregation maps a zero age
  to `null`. The dashboard shows "Not reported" rather than claiming freshness it
  wasn't told.
- **The dashboard does not read `/api/v1/incidents`.** It renders an on-device
  incident notebook (see below); the persistent incident in step 5 is visible
  through the API, not in the dashboard UI.
- **Exposure is per-cell.** The run-wide aggregate lives on
  `GET /api/v2/exposure`, which the dashboard does not call.

---

# Incident notebook (web)

Because no incidents contract was reachable when this was built, the dashboard's
incident notebook is **local to the browser**: an operator can group an alert or
a detection into a case, name who has it, note a jurisdiction, move it through
states and keep a running history, and it survives a reload because
`localStorage` does. It is explicitly **not** shared, **not** an official log,
and **not** evidence — the evidence it links points at real backend records (a
filed report's id, a cell's readings), but it stores the reference, not the
record. Every incident says so in the UI.

Two response tracks are kept apart throughout, in wording and in colour: a
**Fire response** is about a source (a detection, a filed report) and a
**Pollution-control response** is about an area (PM2.5 over a cell). No action in
either offers to dispatch, notify or escalate to an authority, because nothing in
this deployment can — the copy actions say what they do ("Copy fire-response
note", "Add to watch list (this device)") and the status line repeats that
nothing left the device.

---

# Fire-department simulator (Flutter)

`partner_apps/fire_dept_simulator/` — a separate app that progresses persistent
backend incidents. It depends on nothing but the Flutter SDK (`dart:io`); see its
README for why, and for the two costs (no Flutter web, settings held in memory).

It shows the queue (filtered to `role=fire_department`), and per incident:
location, jurisdiction, H3 cell, source, assignee, timings, the linked published
run, the **evidence** (`evidence_report_ids` joined against the public reports
list, marked unverified), and the **append-only history**. Actions come from the
same transition table the server enforces, so an invalid step is never offered.

Failure handling is the point of the app: a transport failure is never given a
fabricated HTTP status (it says it could not reach the backend and offers a
retry, while a refused transition is deliberately **not** retryable); one write
runs at a time so a duplicate tap cannot fire twice, with server idempotency as
the backstop; and a `409`/`404` shows the server's own message verbatim, then
re-reads the incident and history so the buttons match the server. Every accepted
change re-reads too, and says whether the status moved or was already there.

**Unrun.** With no Dart/Flutter SDK on the build machine, `flutter analyze` and
`flutter test` have not been run and the app has never been compiled.
`test/models_test.dart` covers the state machine, wire parsing and formatters.

## Contract notes that bit, and are worth knowing

- **All three `409` conditions return `code: "conflict"`**, not the
  `invalid_transition` / `use_assign_endpoint` codes the contract's §6 lists
  (the error handler maps status→code and the incident routes pin no override).
  The app keys off the HTTP status and the message. Worth fixing backend-side;
  the app does not depend on it either way.
- **`model_versions` differs between a fresh demonstration and a persisted run**
  (see the federation section above).
- **Writes need `X-Simulator-Key`; reads need nothing.** With no key configured
  server-side every write is `503 simulator_disabled`, while reads stay `200`.

---

# Remaining limitations, in one place

1. **The Flutter app is unverified** — no SDK on the build machine; analyze and
   test are outstanding, and step 6 of the walkthrough is documented from the
   contract rather than driven through the UI.
2. **The dashboard's incident notebook is local**, not the backend's incidents.
   Wiring it to `/api/v1/incidents` is a `frontend/**` change.
3. **The federation demonstration is a demonstration.** Two partitions of one
   synthetic dataset; the panel repeats the server's own caveats rather than
   summarising them.
4. **The demo run is one region** (Delhi-NCR) published under `region: india`.
5. **`backend/**` is untouched by all of this.** The backend used for the
   walkthrough is a detached worktree of the published branch, and its extra
   configuration (ports, `SIMULATOR_API_KEY`, `CORS_ORIGINS`) lives in that
   worktree's `.env`, not in the repo.
