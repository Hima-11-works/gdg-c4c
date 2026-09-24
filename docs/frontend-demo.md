# Frontend demos

Two demos live here:

1. **[Web dashboard](#web-dashboard-demo-one-published-run-two-measures)** — one
   published prediction run, shown as two measures.
2. **[Fire-department simulator](#fire-department-simulator)** — a separate
   Flutter console that progresses a **persistent backend incident** through its
   response states.

---

# Web dashboard demo: one published run, two measures

This describes the dashboard's published-run demo: what it shows, where each
number comes from, and how to reproduce the states it has to handle honestly
(a run with population, a run without it, and a stale run).

## Running it

```bash
# backend + database
docker compose up -d

# frontend (expects the API on http://localhost:8000)
cd frontend && npm install && npm run dev
```

The dashboard reads `GET /api/v2/*` only. Every map, timeline and drawer read
is pinned to **one published run**: `lib/api.ts` resolves the run id once from
`GET /api/v2/meta` (`latest_run_id`), caches it for five minutes, and sends it
as `run_id=` on every subsequent read. The current-conditions grid, the
forecast frames, the weather, the alerts and the cell drawer therefore all
describe the same publication — the status panel and the map cannot disagree
about which run is on screen.

## What the dashboard states about the run

The **Published run** panel (top right, collapsible) reports five facts, all
read off the same response the map is drawn from — no client-side inference:

| Fact | Field |
| --- | --- |
| Run time | envelope `generated_at` (absolute + relative) |
| Demo / live status | envelope `mode`, plus `is_demo` for the demo warning |
| Source age | `metadata.quality.max_observation_age_hours` (oldest across cells) |
| Coverage | envelope `coverage` (covered fraction, returned/requested cells, resolution) |
| Prediction method | `metadata.prediction_method` (+ `model_version`, `input_kind`) |

It also lists how many cells carry a population-weighted value, the residents
covered, and the datasets the backend attributed the run to.

`lib/runFacts.ts` derives all of this in one pure function so the panel, the
empty states and the banners can't drift apart.

## The two measures

The **Measure** switcher (bottom-left panel) selects what each cell is coloured
by. Both come from the same run and the same cells; only the value changes.

- **PM2.5** — the area-averaged concentration (`GridCurrentV2Out.pm25` /
  `ForecastV2Out.predicted_pm25`), on the CPCB ramp.
- **Population-weighted PM2.5** — the backend's exposure field
  (`ExposureOut.population_weighted_pm25`), on its own cool cyan → violet ramp
  over the same µg/m³ bands. A crowded cell dominates; an empty one counts for
  nothing. It is a population-weighted *concentration*, not a medical dose
  estimate.

The two layers deliberately use different colour families, so a layer that is
not the plain area-average can never be mistaken for it. Because the value is
still a concentration, the band boundaries are identical — the legend says so.

The measure applies to every rendering path: hex cells, the smooth raster, and
the contrast-mode range contours. Forecast playback switches the value too, so
an exposure animation is the same population weighting applied to each
published forecast anchor.

## Empty states

Both are shown next to the map, not swallowed into a colour — a blank exposure
layer with no explanation would read as "no pollution here", which is the one
thing it must never mean.

- **No population to weight by.** Shown in exposure mode when no cell in the
  read carries a population-weighted value. The message distinguishes the two
  causes: the run has no population estimates at all, or population is known
  but no cell in this frame has both a population and a prediction.
- **Stale data.** Shown when the run's `generated_at` is more than 24h old, or
  when the newest observation behind it is more than 24h old (either alone is
  enough — they fail separately). The notice names the age rather than only
  asserting staleness, and the status panel carries a matching badge.

Thresholds live in `lib/format.ts` (`STALE_RUN_HOURS`, `STALE_SOURCE_HOURS`)
and are stated in the UI next to the numbers they qualify.

## Reproducing the states

The dashboard shows whatever the latest published run is. The three states can
be produced with the backend CLI (these write to the database, not the repo):

```bash
# 1. A population-backed demo run (the manifest's regional-demo scenario)
docker exec gdg-c4c-api-1 sh -c "python -m app.cli demo-features \
  --profile regional-demo --replay-hour 24 --history-hours 6 \
  --out /tmp/demo-features.json"

docker exec gdg-c4c-api-1 sh -c "python -m app.cli prediction-publish \
  --input /tmp/demo-features.json --feature-run-id demo-pop \
  --run-id demo-population-1 --mode demo --region india \
  --scenario-id winter_stagnation --generated-at 2026-01-01T00:00:00Z"

# 2. A stale run: the same features published with an old generated_at
#    (--generated-at 30 days ago), then a fresh run published after it.

# 3. A missing-population run: any run id the backend serves from its
#    no-database fallback, e.g. demo-fallback-<YYYYMMDDTHH>Z
```

To view a specific run without republishing, point the app at it by pinning
the run id — the API accepts `run_id=` on every v2 read, and the frontend takes
its run id from `/api/v2/meta`, so a request rewrite of `latest_run_id` is
enough (this is how the missing-population and stale states were verified
against real runs rather than mocked data).

## Honest limitations

- **The demo run is regional.** `regional-demo` is a Delhi-NCR scenario (256
  H3 res-8 cells), published under `region: india`. At the country tier it
  covers one H3 res-3 cell, so the nationwide view shows a small cluster over
  Delhi and nothing elsewhere. That is the run's real coverage, and the panel's
  coverage figure says so.
- **Source age often reads "Not reported".** The published demo features carry
  `max_observation_age_hours: 0.0`, and the backend's aggregation maps a zero
  age to `null`. The dashboard shows "Not reported" rather than claiming
  freshness it wasn't told about.
- **Exposure is per-cell, not a summary.** The map colours each cell by its own
  `population_weighted_pm25`. The run-wide aggregate lives on
  `GET /api/v2/exposure`, which this dashboard does not currently call.
- **The population figure is only as good as the run.** `covered_population`
  and `population_dataset_version` are the backend's; the frontend never
  estimates a population.

---

# Fire-department simulator

A separate Flutter app, `partner_apps/fire_dept_simulator/`, that progresses a
**persistent backend incident** through its response states.

> **Simulation.** The app is labelled as one on every screen: it works on
> synthetic records and notifies nobody. The status changes it makes *are* real,
> persistent writes to the incident database — which is the whole point, and why
> the API demands a simulator key.

## What it needs

A backend that serves the incident API (`docs/api/incidents.md`) **and** has
`SIMULATOR_API_KEY` configured. Without the key every write is refused with
`503 simulator_disabled`; with it, writes require `X-Simulator-Key`.

The web dashboard's own branch may not have that route. To host one without
touching this branch:

```bash
# a detached worktree of the published backend branch, outside the repo
git worktree add --detach "$TEMP/backhima-wt" origin/backhima

# its own .env: distinct ports and its own volume, so the main stack is untouched
cat > "$TEMP/backhima-wt/.env" <<'ENV'
POSTGRES_USER=pollution
POSTGRES_PASSWORD=princi
POSTGRES_DB=pollution
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
API_PORT=8001
H3_RESOLUTION=8
SIMULATOR_API_KEY=sim-local-dev-key
ENV

docker compose -p gdg-inc --env-file "$TEMP/backhima-wt/.env" \
  -f "$TEMP/backhima-wt/docker-compose.yml" up -d --build
```

Migrations run on boot; `0012_incidents` creates the incident tables.
`/health/ready` tells you when it is up.

## Step 1 — start with a backend incident

An incident is created from an **eligible source**: a fire alert (`warning` or
`critical`), or a citizen report with a fire `kind`. The public report endpoint
is the easiest source to seed:

```bash
KEY=sim-local-dev-key
BASE=http://localhost:8001

# a source: an ordinary citizen fire report (public — no key)
curl -s -X POST $BASE/api/v1/reports -H 'Content-Type: application/json' \
  -d '{"latitude":28.6139,"longitude":77.2090,"kind":"crop_burning","smoke_intensity":4,"duration_hours":2}'

# the incident (201; a repeat is 200 with the same id)
curl -s -X POST $BASE/api/v1/incidents \
  -H "X-Simulator-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"source_type":"report","source_id":1,"severity":"critical","jurisdiction":"Delhi","linked_prediction_run_id":"demo-run-1"}'
```

The response carries `status: "reported"` and `responder_role:
"fire_department"` — a fire report routes to the fire department, a `kind:
"other"` report routes to pollution control instead.

## Step 2 — progress it in the app

```bash
cd partner_apps/fire_dept_simulator
flutter run --dart-define=INCIDENT_API_BASE_URL=http://localhost:8001 \
            --dart-define=SIMULATOR_API_KEY=sim-local-dev-key
```

1. The **queue** lists the incident with its status, severity, jurisdiction,
   coordinates and assignee. The simulation banner sits above it throughout.
2. Open it. The detail shows location, jurisdiction, H3 cell, source, the linked
   published run, the **evidence** (report #1 joined from the public report list,
   marked *unverified*), and the event history so far (`Created`).
3. **Assign to a unit** → name it (e.g. `unit-12`). Status becomes *Assigned*;
   the history gains a second row.
4. **Acknowledge** → *En route* → *On scene* → *Mark resolved*, one tap each.
   Every tap re-reads the incident from the server and reports what actually
   happened ("status is now en route"), and the history grows a row per change.
5. Try to break it, because that is the point of a simulator:
   - **Duplicate tap** — press an action twice quickly: the buttons disable while
     a request is in flight, and a repeat that does land is accepted by the
     server as a no-op, not as a second change.
   - **Rejected transition** — after resolving, the app offers no further action
     (terminal state). Steering around the UI with curl gives the honest answer:
     `409`, and the app shows the server's own words, then re-syncs.
   - **Network loss** — stop the backend and pull to refresh: the screen says it
     cannot reach the backend and offers a retry, without inventing an HTTP
     status and without discarding the data it already had.
   - **No key** — Settings → clear the key: the app warns *before* you act that
     writes will be refused.

## Step 3 — confirm it persisted, from a browser

Refresh the browser against the same backend; the state the app wrote is the
state the API returns, because the app wrote rows and nothing else:

```
http://localhost:8001/api/v1/incidents/1
http://localhost:8001/api/v1/incidents/1/history
http://localhost:8001/docs          # the API's own console
```

Expected history after the walkthrough: `created`, `assigned`, and one
`transition` per state change, ending at `resolved` with `resolved_at` set.

Note this confirms the **backend** record, not the web dashboard: the dashboard
on this branch renders a *local* incident notebook (see its own limitations) and
does not read `/api/v1/incidents`. A dashboard that showed these incidents would
need a change to `frontend/**`, which is outside this task's scope.

## What was verified, and what was not

**Verified against a live backend** (the contract, exercised over HTTP with the
exact requests the app makes):

- create → `201`; idempotent repeat → `200` with the same id; conflicting
  attributes → `409`
- no key / wrong key → `401 unauthorized`; wrong role → `403 role_mismatch`
- assign → `assigned`; re-assign updates the assignee and appends `reassigned`
- `acknowledged → en_route → on_scene → resolved`, `resolved_at` stamped
- duplicate transition to the current status → `200` (no-op); invalid jump and
  any transition out of a terminal state → `409`
- `to_status: "assigned"` on a `reported` incident → `409` ("use
  POST /incidents/{id}/assign to assign")
- key unset server-side → `503 simulator_disabled`, while reads stay `200`
- history shape: `created`, `assigned`, `reassigned`, `transition` × 4

**Not verified: the Flutter UI itself.** There is no Dart/Flutter SDK on the
machine this was built on, so `flutter analyze` and `flutter test` were **not
run** and the app has never been compiled. It is written to be dependency-free
(Flutter SDK only) to keep the first build as uneventful as possible, but treat
the UI as unreviewed until someone runs those two commands. The state machine,
wire parsing and formatters have unit tests in `test/models_test.dart` waiting
to be run.

## Limitations

- **No backend changes here.** This task owns `partner_apps/**` and
  `docs/frontend-demo.md`; `backend/**` and `frontend/**` are untouched, and the
  backend was hosted from a detached worktree of the published branch rather
  than modified.
- **The app cannot create incidents.** Per the contract, creation is an explicit
  API call — the console reads, assigns and transitions. Its empty state says so
  and points at `POST /api/v1/incidents`.
- **Settings are not persisted** (no storage dependency); re-supply the key with
  `--dart-define` or in Settings each run.
- **`dart:io` rules out Flutter web** — this is a mobile/desktop console.
- **The API returns `409 conflict` for all three 409 conditions**, not the
  `invalid_transition` / `use_assign_endpoint` codes the doc's §6 lists, so the
  app keys off the status and the server's message instead. Worth tightening on
  the backend side.
- **No real dispatch, by design.** Acknowledging an incident writes one row and
  appends one event. Nobody is notified, and the app says so on every screen.
