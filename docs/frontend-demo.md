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

## Corridor events and what can honestly be said about their accuracy

**Corridor event…** (top left) opens a named corridor, its geometry, and the
evaluation of the forecast published over it. The governing rule, from
`docs/api/corridor-evaluation.md`: **a forecast may only be reported as accurate
against real, withheld station observations.** When those are missing, the
answer is an explicit insufficient-data result — never a synthetic number
presented as real accuracy.

Three things stay visibly apart, each labelled where it appears:

| Label | What it is |
| --- | --- |
| **Measured evidence** | Station observations in the target window (`valid_at ± 1h`). A label always post-dates the forecast's issue time — that is what "withheld" means. |
| **Model prediction** | The run's forecast PM2.5, including the corridor's `peak_predicted_ugm3` and its horizon. |
| **Illustrative geometry** | The cells themselves: a straight line between two published city coordinates, **not** a road route. The backend says so in every response and the panel repeats it; the map draws the axis **dashed** so it cannot be traced as NH-48/NH-44. |

The panel shows the event id, the affected geography, the forecast timing
(issued/valid per horizon), the source observations with their count and
provenance, the uncertainty (`coverage`: cells and horizons scored), and the
evaluation.

### Insufficient data is a result, not a failure

When the backend returns `verdict: insufficient_data`, the panel says so in
those words — **"Insufficient data — no performance claim can be made"** — names
every reason it gives, and shows **no metric at all**:

> No metric is shown. Every slice in this evaluation is below the 5-observation
> minimum, so each one reports its metrics as null. A number computed from too
> few observations would describe the sample, not the corridor.

Metrics are tabulated only when `verdict` is `evaluated` *and* the slice itself
says `sufficient`. Slices that do not qualify are listed in a collapsed section
with their counts and are deliberately not tabulated — a null recall stays null
rather than becoming 0%, because "no positives" is not "0% recall". The quotable
table carries every metric the contract defines: MAE, RMSE, bias, high-pollution
**recall and precision**, plus the pair and station counts each one rests on.
Omitting precision while showing recall would read as "precision was not
computable", which is a different claim from the one the backend makes.

### An event is pinned to the publication on screen

Lookups carry `run_id`, and if the event that comes back belongs to a different
publication the panel refuses to merge the two:

> **Different publication.** This event is from run `prediction-features-…`, while
> the dashboard is showing `demo-stale-…`. Nothing below describes the run on
> screen, and its cells are not drawn on the map.

That guard was added because the demonstration caught the dashboard rendering
two publications on one screen: the pin said `demo-stale-…` while the detail
showed `prediction-features-…`.

### What could be demonstrated here, and what could not

| Case | Status |
| --- | --- |
| The **catalog** (corridor, endpoints, cell count, `geometry_source`) | **Live** — `GET /api/v1/corridors` |
| **No event for the run on screen** | **Live** — a real `404 not_found`: *"published run … has no results in the Delhi–Kanpur interstate corridor"* |
| **Insufficient data** | **Live** — demonstrated against a real published run (below), and also covered by the backend's committed fixture `tests/fixtures/corridors/insufficient_labels.json` |
| **A corridor event that scored against real observations** | **Not producible here, and not faked** |

The last one cannot be produced in this environment: every demo profile is a
Delhi-centred res-8 disk, and real station readings in the corridor's windows need
an `OPENAQ_API_KEY` — the nearest station that could serve the axis is ~61 km away,
so the backend reports `0 candidate reading(s) checked`. The `evaluated` branch of
the panel is therefore **unexercised**; the only case that exists here is the one
the contract says must not become a performance claim.

#### Getting an event to load at all

The event id is a backend digest (`corridor:<id>:<run_id>:h<digest>`) that a
client cannot compute, and the route *validates* the id in the path: a wrong one
is a `404` whose message names the right one. So the Event id box ships as the
placeholder `latest`, and `fetchCorridorEvent` reads the named id out of that
refusal and asks once more. Only that specific refusal is retried — the two 404s
that are *findings* rather than mistyped ids still reach the panel, and each says
something different:

| Backend says | Panel treats it as |
| --- | --- |
| `… (that one is 'corridor:…')` | A mistyped id — retried with the id the service named |
| `published run … has no results in the … corridor` | A fact about the run: no event, and no verdict |
| `no published prediction run with id '…'` | A fact about the run: it does not exist |

#### Reproducing the live insufficient-data case

The shipped `regional-demo` scenario is res-8 Delhi-NCR, so no demo run produces a
corridor event: the corridor is res-7 and results are looked up by exact cell. To
exercise the live path, the committed `winter_stagnation` scenario's **own feature
vectors** were re-indexed onto the corridor's 24 res-7 cells and published:

```
python -m app.cli prediction-publish \
  --input df-corridor.json --feature-run-id features-winter_stagnation-corridor \
  --run-id corridor-demo-<timestamp> --mode demo --scenario-id winter_stagnation \
  --generated-at 2025-01-16T00:00:00Z
```

Three things keep this honest rather than a faked corridor reading:

- The run is `mode=demo` and `run_synthetic=true`, and the app's own banner reads
  *"Demo simulation · illustrative, not measured"* while the map is showing it.
- The PM2.5 values are the **synthetic scenario's**, carried onto the real axis
  cells. They are not observations of the corridor, and nothing in the panel
  presents them as such.
- `generated_at` must not be later than the scenario's replay hour; a later stamp
  makes the builder refuse with `valid_at must not precede issued_at`.

It demonstrates that the panel **refuses** to score, not anything about corridor air
quality. The verdict comes back `insufficient_data`,
`usable_as_real_world_evidence: false`, all 7 slices below the 5-observation
minimum, and **no metric rendered at all** — not a zero, not a dash.


## The federation panel

`GET /api/v1/federation/status`, public, no key. The panel reports one
**separate-client** run: participants that each trained on their own partition
and sent fitted parameters, never rows.

### Four states, and what each one displays

`FederationRunStatus` is exactly `succeeded | failed`, so a recorded run either
completed or did not. That makes four states, and they are kept apart on purpose —
only the last is an error, and an unavailable endpoint is never rendered as "no
run", which would be a claim about the backend rather than about the connection.

| State | Pill | Panel |
| --- | --- | --- |
| **No run recorded** | `Federation: no demo run recorded`, grey dot | *"No federation run recorded"* — the endpoint answered, and says to run `python -m app.cli federation-demo`. Shows the scope that *would* apply, marked as conditional. |
| **Completed run** | `Federation demo: N regions · succeeded`, green dot | Full run record: run id, participant count, regional scope, run time, feature schema, label basis, participants, model versions, evaluation, what was exchanged, recorded limitations. |
| **Failed run** | `Federation demo: N regions · failed`, **red** dot | *"Federation run failed · did not complete"*, then run id, how many participants joined, scope, run time, and **"What this run did not produce"**: no aggregate, no registered model version, evaluation `unavailable`. **No metric is ever shown for a run that did not complete.** |
| **Unreachable** | `Federation: status unavailable`, orange dot | *"Federation status unavailable"* and the transport error, with a retry. No run facts, and explicitly not "no run recorded". |

The failed state is deliberately **not** the completed view with a different word
in it. A run that never aggregated has nothing to quote, so it is never shown a
model version or an evaluation metric beside its participants and timestamps —
that is exactly how a half-finished run would come to read as a result.

### What a run states about itself

| Fact | Source |
| --- | --- |
| Participant count | `participant_count`, shown next to how many participants the payload actually **listed**. If the two disagree the panel says so rather than silently preferring one. |
| Regional scope | `region_scope`, verbatim (`two-partition-synthetic-demonstration`), never paraphrased into a coverage claim |
| Run time | `started_at` → `finished_at`, plus elapsed time; a missing endpoint reports *not recorded* |
| Model version | The model ids, and whether the payload carried per-model status or ids only |
| Evaluation status | `evaluation.status`, `usable_as_real_world_evidence`, `reason`, and the held-out metrics **only** for a completed run |
| Synthetic or observed labels | Reconciled from `aggregate.synthetic_only`, `evaluation.status` and the model versions, with each signal shown |

That last row is the subtle one. The payload can state it in three places, and
only an explicit `false` counts as observed: a signal that merely *exists* — an
evaluation status of `unavailable`, say — says nothing either way and is reported
as *not reported*, never as evidence that observed labels took part. If the
signals contradict each other, that contradiction is shown rather than resolved.

### Privacy is never inferred

Participants exchange fitted parameters and counts. The panel reports the
**count** of raw rows that reached the aggregator and then says plainly, in both
run views, that this **is not a privacy guarantee** and that a row count is not
evidence of one — then prints the recorded `limitations.privacy` verbatim. There
is no differential privacy, secure aggregation or membership-inference analysis
behind this feature, so a screen that said "private" because the row count is
zero would be claiming something the system never established.

### Demonstrating the four states

| State | How |
| --- | --- |
| No run recorded | **Live** — the shipped deployment has no recorded run, so the endpoint really answers `no_federation_run` |
| Completed run | The contract's own *"After a run"* payload (`docs/api/federation.md` §4), served to the panel |
| Failed run | The same §4 shape with `status: "failed"` — the fields a run that never aggregated would not have: no aggregate, no model ids, evaluation `unavailable`, `finished_at: null` |
| Unreachable | The status request refused at the transport layer |

Two details worth knowing when reading the payload:

- **`model_versions` has two shapes.** A freshly demonstrated run (and the
  contract's example) returns a list of objects with `status` and
  `synthetic_only`; a run read back from the database returns only
  `{"model_ids": [...]}`. The panel handles both and, for the persisted shape,
  says *per-model status not reported for a persisted run* rather than implying
  one.
- **`feature_schema_version` appears in two places** too — at the top level from
  the persisted reader, and inside the `aggregate` block in the contract's
  example. The panel reads both, because checking only one reports a real value
  as *not recorded*.
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

### What the run panel, legend and drawer show

The run panel is the one place that states what the publication is and is not.
It reads only the envelope and cells of the read the map is painted from, so it
cannot describe a different run than the map is showing.

| Fact | Where it comes from | What it shows when absent |
| --- | --- | --- |
| **Mode** | envelope `mode` | "Mode not reported" |
| **Source age** | `quality.max_observation_age_hours` (oldest across cells) | "Not reported" **plus why** — no observation feeds the run, or it is synthetic |
| **Coverage** | envelope `coverage` | "Not reported" |
| **Missing inputs** | union of `quality.missing_fields` | omitted — an empty list means nothing was missing |
| **Backend warnings** | union of `quality.warnings` | omitted |
| **Population** | `exposure.population_weighted_pm25` per cell | "No population estimate in this run" |
| **Horizons** | `/meta.supported_horizons_hours` | "Published forecast range: not reported by this run yet." |

Two things the panel says that a bare count cannot:

- **Coverage names what is *not* there.** A run covering a fraction of the
  country has no rows for the rest, so that area is bare basemap on screen —
  and bare basemap reads as "nothing to report". The panel says so explicitly:
  *"857 of 858 cells in this view are outside this run and are not drawn — that
  area is basemap, not clean air."*
- **Source age says why it is unknown.** "Not reported" alone does not
  distinguish a run with a gap from a run that never had live observations; the
  panel distinguishes them.

`missing_fields` and `warnings` were collected but **not displayed** until now,
which made a run built from a failed fire feed look exactly like a complete one.
The field names are the backend's own vocabulary (`pollution`, `traffic`,
`population`, `roads`, `land_cover`, `fires`, `weather`,
`observed_station_count`), mapped to plain words in `lib/runQuality.ts`; an
unrecognised name is passed through rather than hidden, because an unknown field
is still a real missing input.

## Uncovered cells are hatched, not dark

A cell with no estimate is drawn with a **diagonal hatch** over its dark fill,
from a canvas-generated pattern — no asset to load. A cell *with* a value keeps
its solid ramp colour. The reason is specific: a flat dark patch is exactly what
a *low* reading looks like on a dark basemap, and "no estimate" is the absence of
a reading rather than a low one. The legend repeats the same stripe so the key
and the map cannot disagree.

It is a **separate filtered layer**, not a per-feature `fill-pattern` on the main
fill. MapLibre resolves a pattern expression to an image and rejects `null`
outright, so a `case` expression that returns `null` for the valued cells fails
the whole layer and the map goes blank. That was observed, not assumed.

## The four run shapes

| Run | How it is produced | What the map shows | What the panel shows |
| --- | --- | --- | --- |
| **Complete demo** | `prediction-publish` from the `regional-demo` export | coloured cells where the run has values; hatched elsewhere | `Demo · illustrative, not measured`; `Population: 1/858 cells · 196,942 residents covered`; missing inputs and warnings listed |
| **Partial run** | the same export with the static-cell and traffic fields nulled | every cell hatched; the exposure layer has nothing to draw | `Population: No population estimate in this run`; warning *"No population estimates are available for this aggregation."* |
| **Stale run** | the same export published with a `generated_at` a month old | identical geometry — staleness is a property of time, not shape | `Run time … 31d ago`; a **Stale** badge; *"This run was published 733h ago (over 24h old). Values may no longer describe current conditions."* and a matching banner naming the run |
| **Live run** | **cannot be produced here** | — | — |

A live run needs an observed PM2.5 source (`OPENAQ_API_KEY`) plus the static-cell
and traffic datasets; none are available in this environment. It is not faked:
publishing the synthetic export with `--mode live` is **refused** by the backend
(`ValueError: synthetic feature inputs cannot be published as a live run`), so
every run this dashboard can reach is `mode=demo` and it keeps saying so. What
the *partial* run demonstrates is the missing-input reporting, which is the part
of a live run's honesty that does not need a live feed.

## Runs cannot be mixed

- **Switching measure never re-fetches.** PM2.5 and population-weighted PM2.5
  are two fields on the *same* rows, so the switch only rewrites `fill-color`.
  The run id was verified unchanged across PM2.5 → exposure → PM2.5 in all three
  runs.
- **Playback stays on one run.** The forecast cache key contains the run id, and
  a frame from a different run is refused rather than drawn: the panel shows
  *"Two different runs. The forecast frame on screen is from X while this panel
  describes Y. Nothing is drawn until they agree."*
- **The published range is stated.** *"This run published +1h, +2h, +3h, +4h, +5h,
  +6h and nothing beyond +6h, so the timeline stops there."* A frame between
  anchors is additionally labelled `interpolated`.



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
