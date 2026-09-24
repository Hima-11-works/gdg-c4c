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
# Photo evidence is refused with 503 media_unavailable unless a durable store
# is configured. This is the backend's default, not an oversight.
CITIZEN_MEDIA_STORAGE=filesystem
CITIZEN_MEDIA_DIR=/var/lib/air-health/citizen-media
ENV

# The media directory has to be a volume, not a path inside the container.
cat > "$TEMP/published-wt/docker-compose.override.yml" <<'YML'
services:
  api:
    volumes:
      - ./backend/app:/app/app
      - citizen_media:/var/lib/air-health/citizen-media
volumes:
  citizen_media:
YML

docker compose -p gdg-demo --env-file "$TEMP/published-wt/.env" \
  -f "$TEMP/published-wt/docker-compose.yml" up -d --build
```

Migrations run on boot. `/health/ready` tells you when it is up. Then prove the
photo store actually works before demonstrating anything — this is the backend's
own deploy gate, and it fails loudly rather than accepting bytes it cannot
return:

```bash
docker exec gdg-demo-api-1 python -m app.cli verify-media-storage   # must exit 0
```

A fresh named volume is owned by root while the API runs as an unprivileged
user, so chown it once (`docker exec -u 0 gdg-demo-api-1 chown -R 1000:1000
/var/lib/air-health/citizen-media`) and re-run the gate. Then point the
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

The report form is two steps against two endpoints, and the split is the point:

1. `POST /api/v1/reports` **creates the report** and returns its integer `id`.
2. `POST /api/v1/reports/{id}/evidence` **attaches the selected photo and/or the
   resident's own sensor reading** to that report.

Zoom in past the country tier first (the report button only exists once the map
has a viewport), press **Report a fire**, and submit. The form then uploads the
evidence against the id step 1 returned, showing a progress bar while the bytes
move.

**A photo.** Choose a file. The confirmation shows the stored evidence: the
photo rendered *from the backend's own URL*, its type and size, the content
address it was stored under, the verification status the server returned
(`Unverified`), and the sentence that matters —
*"A citizen reading is stored only on this evidence record. It never becomes a
station observation and never feeds the pollution model."* The stored bytes can
be fetched back and are byte-identical to what was uploaded.

**A sensor reading.** Type a value, tick *Attach this reading to the report*, and
send. The stored record carries the pollutant, value, **your unit**, the time you
say it was measured, and `source: citizen · verified: false`. The backend places
it at the report's own position when no coordinate is supplied.

**An interrupted upload.** Kill the connection mid-upload (or throttle the
network to zero). The form says the evidence was not stored, names the report id
that *is* already stored, and offers a retry that re-sends the identical
payload under the identical evidence key — which is why the retry returns the
stored record instead of creating a second one. **The report is never created
twice**: the retry path is the evidence call alone.

**A rejected photo.** Send a file whose bytes are not an image (a text file
renamed `.jpg`, or a truncated transfer). The server refuses it and the form
shows the code and the server's own message:

| What was sent | Response |
| --- | --- |
| text file named `.jpg` | `415 unrecognized_media_content` |
| truncated photo | `415 media_content_invalid` |
| PNG announced as JPEG | `415 media_content_mismatch` |
| unsupported pollutant (`co2`) | `422 validation_error` |

None of these are dressed up as a retry that could work — the button reads *Send
it again (after changing it above)*, because a refusal fails identically next
time. Fix the file and resend: it goes to the **same report id**, and the
evidence then stores normally.

Click that cell on the map and the drawer shows the **Citizen report** with its
smoke, duration and age, labelled **Unverified — resident submitted, not a
measurement**.

### 4. Forecast alert

The **alerts bell** (beside the search box) lists the run's alerts, each with the
current value and a forecast horizon, framed as a **Pollution-control response**
(an area, not a source). On the timeline, drag to a frame the publication did not
forecast at — the horizon label reads e.g. **`+2 HR 15 MIN interpolated`**, and
the banner says the same: interpolated between published anchors, no calibrated
interval.

### 5. Open an incident from the alert you are looking at

The alerts list carries a real workflow. Open the bell, pick an alert, and press
**Incident…**. The panel states the source by its published-alert identity
(`v2:<run_id>:<h3_cell>:<forecast_hours>`) and says one of three different
things, which are never blurred together:

| State | What the panel says |
| --- | --- |
| **No incident** | "No incident is open for this source. That is a fact about the service, not a gap in this browser." |
| **Cannot reach the service** | "This says nothing about whether an incident exists for this source — it only says this browser could not ask." |
| **Write rejected** | The server's own code and message, e.g. `403 role_mismatch`. |

**Open an incident** creates it. Writes need two credentials, and the panel says
which one is missing when either is: the deployment's `X-Simulator-Key` **and**
an `X-Actor-Id` naming a responder. The service resolves that actor's role and
jurisdiction from its own registry, so nothing the browser asks for can widen
its authority.

The incident is a **persistent operational record**, not a browser note: an
integer id, an append-only history, and it survives a reload because the server
holds it. Creating the same source again returns `200` with the *same*
incident, and the panel simply shows the existing one.

### 6. Assign it — a simulated hand-off

With the incident `reported`, name a unit and press **Assign**. The status
becomes `assigned`, the history gains an `assigned` **and** a `delivered` event,
and the incident appears in that role's inbox:

```bash
curl -s "localhost:8001/api/v1/incidents/inbox?role=pollution_control"
```

The delivery is explicitly a simulation, and the panel says so where it shows
it: *no email, SMS, webhook or push is sent by this system*. The database
forces `simulated = true` and has no channel, address or provider column at
all, so a dispatch cannot be recorded even by accident.

### 7. Flutter responder update

```bash
cd partner_apps/fire_dept_simulator
flutter run --dart-define=INCIDENT_API_BASE_URL=http://localhost:8001 \
            --dart-define=SIMULATOR_API_KEY=sim-local-dev-key \
            --dart-define=SIMULATOR_ACTOR_ID=engine-7
```

The actor id is not optional: it names the responder, and its registered role
must match the incident's. A key on its own is refused with `401`.

Open the incident in the queue and walk the response: **Assign to a unit** →
**Acknowledge** → **Mark en route** → **Mark on scene** → **Mark resolved**.
Each tap re-reads the incident from the server, reports what actually
happened, and appends one row to the event history. Every screen carries the
simulation banner.

### 8. Web status — the same record after a reload

Reload the dashboard. The same alert shows the same incident id, the status the
responder set, and their updates in the history, each row naming **which
authority** acted (`unit-12 · Pollution control · Delhi`) rather than only which
role:

```bash
curl -s localhost:8001/api/v1/incidents/7
curl -s localhost:8001/api/v1/incidents/7/history
```

Expected history: `created`, `assigned`, `delivered`, then one `transition` per
responder step, ending at `resolved` with `resolved_at` set.

### 9. Federation demonstration

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

Steps 1–4 and 9 were driven in a browser against the live backend.

Step 3's evidence upload was verified **end to end in the web client**, all four
ways: a report with a photo stored (and the stored bytes read back
byte-identical), a report with a sensor reading stored, an interrupted upload
retried against the same report id with the same idempotency key, and a rejected
photo — each refusal showing the server's own code and a button that does not
pretend a retry could work.

Steps 5–8 were verified as a **two-client demonstration**: the web panel opened
an incident from a published alert and assigned it, the responder sequence was
driven through the exact requests the Flutter app makes, and the web page was
then **reloaded** and showed the same incident id, the responder's terminal
status, and the full history naming the acting authority. The three states the
panel must keep apart were each demonstrated — no incident, an unreachable
service (and the panel explicitly *not* claiming no incident exists), and a
rejected write. The duplicate and invalid-action refusals were verified against
the live service: an identical create returns `200` with the same id, a
differing one `409 conflict`, a skipped step `409 invalid_transition`, assigning
through the transition route `409 use_assign_endpoint`, a fire actor on a
pollution incident `403 role_mismatch`, a Delhi actor on a Mumbai incident
`403 jurisdiction_mismatch`, and an unregistered actor or a missing credential
`401`.

**Neither Flutter app has been run.** There is no Dart/Flutter SDK on the machine
this was built on, so `flutter analyze` and `flutter test` are still outstanding
for both `air_health_flutter` and `fire_dept_simulator`. The responder app's
writes in step 7 were driven over HTTP with the same headers and the same bodies
its client sends, and its contract was verified that way — but the app's own UI
did not run, and neither did the Flutter half of step 3. See the limitations at
the end of this document.

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

## Report evidence (photo + local sensor reading)

The report form is two calls, and the report id from the first is what makes the
second retryable:

| Step | Call | Idempotency |
| --- | --- | --- |
| create | `POST /api/v1/reports` | `client_report_id`, minted per open form |
| attach | `POST /api/v1/reports/{id}/evidence` | its own `client_report_id`, **the same on every retry** |

`lib/reportEvidence.ts` owns the evidence half: it mirrors the documented bounds
(pollutant allow-list, non-negative value, non-empty unit, a reading no older
than 72h and no more than 300s in the future, a 5 MiB photo cap), builds the
multipart body, and classifies the server's codes. Two rules it exists to
enforce:

- **A refusal is not retryable.** `415`/`422`/`409` fail identically next time,
  so the form says what to change. Only a transport failure, `media_unavailable`
  and `media_not_durable` get a retry button — the last two because the backend
  itself says "retry later".
- **A transport failure is never given a status.** It reads
  `network_error`, not `503`, because nothing is known about what the server
  received. The retry settles that question: an identical payload under the same
  key returns `200` with the stored record.

Upload progress needs `XMLHttpRequest` — the only browser API that reports bytes
sent — so `submitReportEvidence` is the one call in `lib/api.ts` that does not
use `fetch`. When the browser cannot compute a total, the bar is indeterminate
rather than showing an invented percentage.

The photo and the reading are shown as the server stored them: the image is
fetched back from the evidence URL, the digest is the content address, and the
status is whatever `verification_status` says. A citizen reading is never
presented as a measurement, and the panel says so in as many words.

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
- **The dashboard reads incidents, but only the two sources the API accepts.**
  A pollution-control incident can be opened from a **published alert** (its
  `v2:` identity) and a fire incident from a **citizen report**. There is no
  source for an incident on a cell that merely has a thermal detection, so the
  drawer says there is nothing to open one from rather than offering a button
  with no source.
- **Exposure is per-cell.** The run-wide aggregate lives on
  `GET /api/v2/exposure`, which the dashboard does not call.

---

# Incident workflow (web)

The dashboard's incident surface used to be a **notebook in `localStorage`**: an
operator grouped an alert or a report into a case, typed an assignee, moved a
status dropdown and kept a local history. Every panel said out loud that it was
device-local, because there was no incidents API to build against. There is one
now, so that notebook is gone — status, assignment and history are read from
`/api/v1/incidents` and written to it.

`lib/incidents.ts` owns the vocabulary and the failure classification;
`lib/operatorIdentity.ts` owns the two write credentials. What the panel will
not do:

- **It never presents a device-local record as an operational one.** There is no
  local copy left to confuse with the real thing.
- **It never implies a dispatch.** Assignment is a *simulated* hand-off into a
  role's inbox; no notification of any kind is sent, and the panel says so
  where the hand-off is shown.
- **It never resolves a status itself.** The status moves when a responder moves
  it; the panel shows their updates as they arrive, and offers no control that
  would resolve an incident from the operator's screen.
- **It keeps "no incident", "cannot reach the service" and "write rejected"
  apart.** A refused write shows the server's own code; an unreachable service
  says plainly that nothing is known about whether an incident exists.

Two response tracks are still kept apart in wording and colour: a **Fire
response** is about a source (a filed report) and a **Pollution-control
response** is about an area (PM2.5 over a cell). The source decides the role: a
fire report routes to the fire department, a `kind: other` report and every
published alert route to pollution control.

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

- **Writes need a key *and* an actor.** `X-Simulator-Key` authenticates the
  deployment; `X-Actor-Id` names the responder and is resolved against
  `SIMULATOR_ACTORS`, which is the authority on who exists. A key alone is
  `401`, an unregistered actor is `401`, a wrong role is `403 role_mismatch`, and
  a scoped actor outside its jurisdiction is `403 jurisdiction_mismatch`. Because
  the role comes from the registry, the `role` field that `assign`/`transitions`
  used to accept is now optional and only agreement-checked — the responder app
  no longer sends it at all.
- **The `409` code disagreement is resolved.** An earlier backend returned
  `conflict` for all three 409 cases; the current one pins its own codes, and
  both were observed live: `invalid_transition` for a skipped step,
  `use_assign_endpoint` for assigning through the transition route, and
  `conflict` for the same source with differing attributes.
- **A repeat of the current status is a no-op `200`**, not an error — so a
  duplicated tap cannot double-apply. It is also why a *repeat* of
  `to_status: assigned` from `assigned` returns `200` rather than the
  `use_assign_endpoint` refusal: the no-op rule is checked first.
- **`model_versions` differs between a fresh demonstration and a persisted run**
  (see the federation section above).
- **Writes need both credentials; reads need neither.** With no key *or* no actor
  registry configured server-side, every write is `503 simulator_disabled`, while
  reads stay `200` — so a read-only dashboard shows the real workflow without a
  single credential.

---

# Remaining limitations, in one place

1. **Both Flutter apps are unverified** — no SDK on the build machine, so analyze
   and test are outstanding and neither app has ever been compiled. The
   `air_health_flutter` evidence flow (photo picker, progress, preserved report
   id, retry, refusal) is implemented and covered by tests, but those tests have
   never been run. The responder app's writes in step 7 were driven with the same
   headers and bodies its client sends, but its own UI did not run.
2. **Photo evidence needs deployment configuration.** `CITIZEN_MEDIA_STORAGE` is
   `disabled` by default, so an unconfigured deployment answers
   `503 media_unavailable` for photos while sensor-only intake keeps working. Run
   `python -m app.cli verify-media-storage` as a deploy gate.
3. **Incident writes need credentials in the browser.** The dashboard reads
   incidents with none, but creating and assigning need `VITE_SIMULATOR_API_KEY`
   and an actor id. The key reaches the browser bundle, which is acceptable only
   because this is a **simulator** deployment whose key authenticates a
   demonstration rather than a real authority — the same caveat the whole
   incident workflow is built around. Do not reuse this pattern for a real
   deployment.
4. **The federation demonstration is a demonstration.** Two partitions of one
   synthetic dataset; the panel repeats the server's own caveats rather than
   summarising them.
5. **The demo run is one region** (Delhi-NCR) published under `region: india`.
6. **`backend/**` is untouched by all of this.** The backend used for the
   walkthrough is a detached worktree of the published branch, and its extra
   configuration (ports, `SIMULATOR_API_KEY`, `CORS_ORIGINS`, media storage) lives
   in that worktree's `.env` and a compose override, not in the repo.
