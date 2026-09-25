# Frontend verification report

Audit of the web dashboard, the resident Flutter app and the fire-department
Flutter simulator against a **running** backend.

| | |
| --- | --- |
| Branch audited and fixed | `frontswasti` |
| Backend under test | `origin/backhima` = **`6631397f785c1c40c9c089427b649b6eaf51d7a6`** — *"Detect candidate hotspots from satellite imagery, for human review"* |
| Backend checkout | `C:\Users\KIIT\Documents\gdg-backhima-audit` (git worktree, detached at `6631397`) |
| Merge into `frontswasti` | **none.** The backend is never merged; it runs from its own checkout. |
| Backend edited | **no.** `backend/**`, `.github/**` and `docs/api/**` untouched. |
| Files changed by this audit | `frontend/**`, `partner_apps/**`, `frontend/VERIFICATION.md` only |

> The audited backend tip is **newer than the one the frontend was last written
> against** (`3c1e98c`). It added `docs/api/hotspots.md`, the hotspot detector and
> three shipped fixtures, plus a separate-client federation workflow. The hotspot
> work in `frontswasti` had been built against a *guessed* schema; this audit
> rewrote it against the real contract. That rewrite is the bulk of the diff.

## Result matrix

`Pass` = exercised against the running backend in this audit. `Untested` = not
exercised, with the reason. Nothing is marked Pass on inspection alone.

| # | Check | Web | Resident Flutter | Fire-dept Flutter |
| --- | --- | --- | --- | --- |
| 1 | Report + evidence, retry keeps report id, persisted unverified after reopen | **Pass** | **Untested** (a) (b) | n/a |
| 2 | Incident from published alert/report; assignment + history from API; responder update reaches web | **Untested** (c) | n/a | **Untested** (b) (d) |
| 3 | PM2.5 vs exposure values; honest states; map/drawer/timeline same run | **Untested** (c) | n/a | n/a |
| 4 | Corridor: backend event id, geography, time, evidence, validation; insufficient ≠ accuracy | **Pass** (e) | n/a | n/a |
| 5 | Federation: success / failed / no run / unreachable; synthetic vs observed | **Pass** (f) | n/a | n/a |
| 6 | Hotspots distinct from PM2.5 + FIRMS; provenance, confidence, review; missing imagery | **Pass** | n/a | n/a |
| 7 | Fire-dept app: API, assigned incidents, legal actions, safe retry, server history | n/a | n/a | **Untested** (b) (d) — static checks pass (g) |
| 8 | Labels distinguish simulation / prediction / observation / authority contact | **Untested** (c) | **Untested** (a) | **Untested** (a) |

Reasons:
- **(a)** No Android/iOS device or emulator is available (`flutter devices` lists
  only Windows desktop, Chrome and Edge), so no Flutter app was run on a device.
- **(b)** `flutter analyze` reports **10 errors** in the resident app (listed
  below), so it does not compile and cannot be run at all. The fire-dept app
  compiles and its tests pass, but still needs a device.
- **(c)** Not re-exercised in this audit. The demo backend's published-run state
  was not re-seeded for these four items within the audit's time budget, and they
  are not hot-spot areas. Previously demonstrated on `3c1e98c`; **not re-verified
  against `6631397`**, so they are recorded as Untested rather than Pass.
- **(d)** Not exercised against the live backend in this audit.
- **(e)** Verified against a real recorded corridor event, run
  `corridor-demo-20260925-144513`, event
  `corridor:delhi-kanpur:corridor-demo-20260925-144513:h052e2c55`.
  `docs/api/corridor-evaluation.md` is unchanged between `3c1e98c` and `6631397`.
- **(f)** Live status is genuinely `no_federation_run`; the other three states
  were exercised with contract-shaped responses. The status payload keys are
  identical on `6631397`.
- **(g)** `flutter analyze`: 0 errors, 1 pre-existing warning. `flutter test`:
  32/32 pass.

## Defects found and fixed

### 1. Hotspot view was built on a guessed contract (web, critical)

The previous hotspot layer read `GET /api/v1/fires` and triaged FIRMS
`confidence_class`, and told the reader that **detector version, human review
state and station corroboration were "not reported by the API"**. Against
`6631397` that was wrong on every count:

| Frontend claimed | Backend actually provides |
| --- | --- |
| wrong endpoint (`/api/v1/fires`) | `/api/v1/hotspots`, `/api/v1/hotspots/{scan_id}` |
| "Detector version: not reported" | `detector_version: "hotspot-candidate-v1"` on every candidate |
| "Human review state: not reported" | `review_status`, `reviewed_by`, `reviewed_at` |
| "No station evidence is linked" | `supporting_sources` + `evidence[]` incl. station readings |
| triage from FIRMS confidence | detector-owned `confidence` / `confidence_score` / `confidence_basis` |
| — | `verdict: insufficient_evidence`, `pm25_ugm3` (always null), `source_attribution`, `limitations` |

Rewritten against the real contract. A candidate is now drawn from the recorded
scan, its review state is shown as the detector reported it, and the limitation
"imagery is the only trigger" is stated where the panel shows FIRMS/station counts.

### 2. Hotspot panel printed `0` where the detector reported a real count (web)

`signal_counts` keys are `fires_supplied` / `fires_usable` /
`stations_excluded_source` … The panel read `signal_counts.fires`, which is
`undefined`, so the insufficient-evidence case displayed **"0 fire and 0 station
records"** while the detector had recorded 1 and 1.

- Expected: `FIRMS: 1 supplied, 1 usable` / `Stations: 1 supplied, 0 usable — 1 excluded by source`
- Actual before fix: `0 fire and 0 station record(s)`
- Now reports supplied vs usable vs excluded, which is the contract's point: what
  was used *and what was dropped, and why*.

### 3. Resident Flutter app did not unwrap the API envelope (4 compile errors)

`submitReport`, `listActiveReports`, `submitEvidence` and `fetchEvidence` are
declared to return `FireReport` / `List<FireReport>` / `ReportEvidence` but
returned `ReportEnvelope<T>`. The backend wraps every citizen-intake response in
`{generated_at, is_demo, data}`, so at runtime every caller would have received
an object with **no `id`** — the exact field evidence is attached to.

Fixed by adding `_unwrapEnvelope` (the envelope is still parsed, so
`generated_at` / `is_demo` remain available). Regression test added:
`partner_apps/air_health_flutter/test/data/fire_report_envelope_test.dart`
(3 tests, pass) — it fails against the old code.

### 4. Fire-dept app: `ResponderRole` had no `label` (1 compile error)

`IncidentEvent.actorSummary` called `role!.label`, but the enum only had a wire
map, so the history line could not compile. Added a `label` getter
(`Fire department` / `Pollution control`) distinct from the wire value.

### 5. Two sensor-bound tests never tested the bounds (test defect)

`_readingOrNull` in `report_evidence_test.dart` defaulted `raw` to `''`, so
`double.tryParse('')` returned null and **every** call was refused before the age
and future-skew checks ran. The 72-hour and 300-second bounds were never
exercised. With a parseable default the bounds are reached and both tests pass —
the production logic in `report_evidence.dart:93-95` was correct.

## Open failures (not fixed, and why)

The resident app still has **10 analyzer errors** and therefore does not build.
They are pre-existing, in areas this audit did not cover, and fixing the
notification plugin API drift is a separate piece of work:

| Location | Error |
| --- | --- |
| `lib/data/grid/grid_api.dart:393` | `Expected to find '}'` |
| `lib/features/reports/report_fire_sheet.dart:522` | expression has type `void`, its value is used |
| `lib/features/reports/report_fire_sheet.dart:792` | `AppTypography.titleSmall` undefined |
| `lib/notifications/forecast_alarm_scheduler.dart:68,118` | `SensitivityRules` undefined |
| `lib/notifications/forecast_alarm_scheduler.dart:124` | required named parameter `freshness` missing |
| `lib/notifications/notification_service.dart:112` | `canScheduleExactAlarms` undefined on the plugin |
| `lib/notifications/notification_service.dart:125` | `Permission.alarm` undefined |
| `lib/notifications/notification_service.dart:178,179` | `uiLocalNotificationDateInterpretation` / `UILocalNotificationDateInterpretation` undefined |

Consequence: `flutter test` in that app is **208 passed / 6 failed**, and all six
failures are `loading` failures of `grid_api_provider_test.dart`,
`app_widget_test.dart`, `report_fire_sheet_test.dart`,
`alert_notification_dispatcher_test.dart` and two more, caused by the compile
errors above — not by failing assertions.

## Backend contract observations

No backend defect was found. Two client-side misunderstandings worth recording so
the next person does not repeat them:

1. **`POST /api/v1/reports/{id}/evidence` is `multipart/form-data`** with flat
   fields (`photo`, `sensor_pollutant`, `sensor_value`, `sensor_unit`,
   `sensor_measured_at`, `notes`) — not a nested JSON body. The four sensor
   parts must be sent together; omitting `sensor_measured_at` yields
   `validation_error`.
2. **Idempotent replay returns the stored record.** Re-sending an identical
   `client_report_id` *and* identical payload returns the same evidence `id` with
   `200`. A different payload under the same key is `409 conflict`
   (*"a different evidence record already exists for this report"*), which is
   correct. Only **one** evidence record may exist per report.
3. **`signal_counts` is bucketed**, not a single count. The backend's own scan
   report prints `supporting signals supplied: fires=1 stations=1`; the JSON uses
   `fires_supplied` / `fires_usable` / `stations_excluded_source`. A UI that reads
   `signal_counts.fires` silently renders 0.

## Commands

```powershell
# fetch + pin
git fetch --all --prune
git rev-parse origin/backhima            # 6631397f785c1c40c9c089427b649b6eaf51d7a6

# backend, from its own checkout (never merged into frontswasti)
git worktree add --detach C:\Users\KIIT\Documents\gdg-backhima-audit origin/backhima
docker compose -p gdg-inc `
  -f C:\Users\KIIT\Documents\gdg-backhima-audit\docker-compose.yml `
  -f C:\Users\KIIT\Documents\gdg-backhima-audit\docker-compose.override.yml up -d

# the three shipped hotspot cases must be recorded before the API serves them
docker cp C:\Users\KIIT\Documents\gdg-backhima-audit\backend\tests\fixtures\hotspots `
  gdg-inc-api-1:/app/tests/fixtures/hotspots
docker exec -u root gdg-inc-api-1 mkdir -p /app/var/hotspots
docker exec -u root gdg-inc-api-1 chown -R appuser:appuser /app/var
docker exec gdg-inc-api-1 sh -c "cd /app && python3 -m app.cli hotspot-scan \
  --fixture-dir tests/fixtures/hotspots --out-dir var/hotspots"

# web
cd frontend; npm run build; npm run lint

# flutter
$env:Path = "C:\Users\KIIT\flutter-sdk\flutter\bin;$env:Path"
cd partner_apps\air_health_flutter; flutter pub get; flutter analyze; flutter test
cd ..\fire_dept_simulator;        flutter pub get; flutter analyze; flutter test
```

## Browser walkthrough — hotspot review (exercised)

Run with the dev server on `:5174` against `:8001`. Screenshots in
`%TEMP%\opencode\hot_positive.png`, `hot_negative.png`, `hot_insufficient.png`.

1. Open the dashboard; the published run is `corridor-demo-20260925-144513`
   (banner: *Demo simulation · illustrative, not measured*).
2. Click **Fire candidates…**. Network: `GET /api/v1/hotspots`.
3. The panel lists all three recorded scans with their verdicts.
4. Open `authored-positive-delhi-ncr-20251108T053000Z-a3eb07bd`
   (`GET /api/v1/hotspots/{scan_id}`). Expected vs actual:

| Expected (from the real record) | Shown |
| --- | --- |
| verdict `candidates`, 1 candidate | "verdict CANDIDATES" |
| `confidence: high`, `confidence_score: 0.75` | "high (0.75 of a 0.90 ceiling)" |
| 3 basis terms | all three, verbatim |
| `supporting_sources: [satellite_imagery, firms]` | "satellite_imagery + firms" |
| `review_status: pending_human_review` | shown, with "no reviewer has looked at this yet" |
| `pm25_ugm3: null` | "not reported — a candidate carries no concentration" |
| `source_attribution: unattributed` | "unattributed" |
| 2 evidence items | both, with UTC times and detail strings |
| `tp=1 fp=0 fn=0 unlabelled=0`, p=r=1.00, labels `authored-fixture` | all shown, prefixed "Measured against authored fixture labels… Not evidence of real-world performance." |

5. Open `authored-negative-distractor-delhi-ncr-20251109T060000Z-e92220f7`:
   2 candidates both `low (0.40)`, and the evaluation shows the detector's
   errors by name — false positive `883da11445fffff`, missed
   `883da11405fffff`, 1 unlabelled *"not scored either way"*, precision 0.00,
   recall 0.00.
6. Open `authored-unavailable-no-imagery-20251110T070000Z-61490c66`: the row and
   the detail both read **"Insufficient evidence — could not look"** /
   **"The detector could not look"**, with the backend reason verbatim, and the
   supplied-but-unused signals. This is never rendered as "no hotspots found".
7. Candidate markers render hollow, dashed and cyan — no fill, so not a PM2.5
   hex; cyan, in neither the PM2.5 ramp nor the FIRMS magenta. The four
   fire-adjacent layers keep separate labels and toggles.

### End-to-end walkthrough: **Untested**

The full chain *published run → exposure map → citizen evidence → alert →
backend incident → Flutter responder update → web status → federation panel →
hotspot review* was **not** completed in this audit. The stops verified
individually are the published run, citizen evidence (report `36`, evidence
`28`), federation and hotspot review; the exposure-map, alert, incident and
Flutter-responder legs were not walked end to end (reasons (b) and (c)). The
backend accepts the full chain — report creation, evidence, and the documented
incident workflow all responded correctly when exercised directly — but the
cross-client hand-off is unverified and must not be read as passing.

## IDs used

| Kind | Value |
| --- | --- |
| Backend commit | `6631397f785c1c40c9c089427b649b6eaf51d7a6` |
| Published run | `corridor-demo-20260925-144513` |
| Corridor event | `corridor:delhi-kanpur:corridor-demo-20260925-144513:h052e2c55` |
| Citizen report | `36` (`client_report_id: audit-probe-001`) |
| Evidence record | `28` (`client_report_id: audit-probe-ev-001`, `unverified`) |
| Hotspot scan (positive) | `authored-positive-delhi-ncr-20251108T053000Z-a3eb07bd` |
| Hotspot candidate | `hotspot:authored-positive-delhi-ncr-20251108T053000Z-a3eb07bd:883da11467fffff` |
| Hotspot scan (negative) | `authored-negative-distractor-delhi-ncr-20251109T060000Z-e92220f7` |
| Hotspot scan (insufficient) | `authored-unavailable-no-imagery-20251110T070000Z-61490c66` |

## Sample responses

`GET /api/v1/hotspots/{scan_id}` (positive), candidate verbatim:

```json
{
  "candidate_id": "hotspot:authored-positive-delhi-ncr-20251108T053000Z-a3eb07bd:883da11467fffff",
  "h3_cell": "883da11467fffff",
  "latitude": 28.61,
  "longitude": 77.2,
  "acquired_at": "2025-11-08T10:00:00+05:30",
  "detector_version": "hotspot-candidate-v1",
  "confidence": "high",
  "confidence_score": 0.75,
  "confidence_basis": [
    "imagery index 0.82 >= trigger threshold 0.55 (+0.4)",
    "index 0.82 >= strong threshold 0.75 (+0.15)",
    "1 FIRMS detection(s) at or above 1 MW in the same cell (+0.2)"
  ],
  "supporting_sources": ["satellite_imagery", "firms"],
  "pm25_ugm3": null,
  "value_semantics": "candidate_location_for_human_review",
  "source_attribution": "unattributed",
  "review_status": "pending_human_review",
  "reviewed_by": null,
  "reviewed_at": null
}
```

`POST /api/v1/reports/36/evidence` (sensor only):

```json
{"id":28,"report_id":36,"client_report_id":"audit-probe-ev-001",
 "verification_status":"unverified","media":null,
 "sensor":{"pollutant":"pm25","value":78.5,"unit":"ug/m3",
           "source":"citizen","verified":false}}
```
