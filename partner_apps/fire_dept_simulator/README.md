# Fire Department Simulator

A response console for the Air Health **incident workflow** — the fire-department
side of `docs/api/incidents.md`.

> **This is a simulation.** It works on synthetic records, sends no
> notifications, and contacts no emergency service. The status changes it makes
> *are* real, persistent writes to the incident database, which is why the API
> requires a simulator key.

## What it does

- **Queue** — incidents from `GET /api/v1/incidents`, filtered to
  `role=fire_department` by default (a toggle shows every role). Each row shows
  id, status, severity, jurisdiction, coordinates, assignee and how long ago it
  moved.
- **Detail** — location, jurisdiction, H3 cell, source (alert or report),
  assignee, timing, the linked published run, the **evidence** (the incident's
  `evidence_report_ids` joined against the public report list, so a responder
  reads the actual report rather than an id), and the **append-only event
  history**.
- **Responder actions** — assign, then acknowledge → en route → on scene →
  resolve, plus cancel. The buttons come from the same transition table the
  server enforces, so an invalid step is never offered; assignment is offered
  separately because the API gives it its own endpoint.

## What it deliberately handles

| Failure | How it shows up |
| --- | --- |
| Loading | Spinner; the list and detail both have a pull-to-refresh. |
| Network loss | A `dart:io` transport failure is *not* given a fake HTTP status. It shows "could not reach the backend at …", plus a **Retry** (retryable), and the last good data stays on screen. |
| Retries | Retry is offered only where it could help. A refused transition is **not** given a retry button — it says the state has to allow it first and points at refresh instead. Create and transition are safe to retry regardless, because the API is idempotent (create on `(source_type, source_id)`; a repeated transition to the current status is a no-op). |
| Duplicate taps | One write at a time: every action button disables while a request is in flight, and the in-flight request shows a spinner. The server's idempotency is the backstop, not the plan. |
| Rejected transitions | The **server's own message** is shown verbatim (e.g. `409 · "use POST /incidents/{id}/assign to assign"`), never replaced with a generic error. After a 409/404 the incident and history are re-read so the buttons match the server's truth. |
| No key configured | Reads work; the app says writes will be refused *before* you press anything. |

Every accepted change re-reads the incident from the server rather than trusting
an optimistic local update, and says whether the status actually moved ("status
is now acknowledged") or was already there ("already acknowledged, nothing
changed").

## Running it

```bash
cd partner_apps/fire_dept_simulator

# Point it at a backend that serves the incident API, and give it the key.
flutter run \
  --dart-define=INCIDENT_API_BASE_URL=http://localhost:8001 \
  --dart-define=SIMULATOR_API_KEY=sim-local-dev-key
```

- An **Android emulator** reaches the host's localhost at `10.0.2.2`, so use
  `--dart-define=INCIDENT_API_BASE_URL=http://10.0.2.2:8001`.
- Both values are also editable in the app's **Settings**, held in memory (see
  below). A **Test connection** button there distinguishes "cannot reach the
  backend" from "the backend answered and refused".

### Checks

```bash
flutter analyze
flutter test
```

## Why there are no pub dependencies

The console needs one origin, JSON in, JSON out, and one header on writes —
`dart:io` already provides all of it. A package would add moving parts without
removing any of the failure modes this app exists to handle.

Two costs, stated plainly:

- **`dart:io` means no Flutter web.** This is a mobile/desktop console.
- **Settings are not persisted.** Persisting the key would need a storage
  package, so it lives in memory and is re-set from `--dart-define` on relaunch.
  If that becomes annoying, `shared_preferences` is the one dependency worth
  adding.

`flutter_lints` is pinned to `^5.0.0` rather than the citizen app's `^6.0.0` so
the project resolves against packages already in the local pub cache; the rules
that matter are the same.

## The contract it speaks

`docs/api/incidents.md` on the published backend branch. The essentials:

- Writes need `X-Simulator-Key`; reads need nothing. No key configured
  server-side → every write is `503 simulator_disabled`.
- `reported → assigned → acknowledged → en_route → on_scene → resolved`, and
  `cancelled` from any non-terminal state. `resolved`/`cancelled` are terminal.
- `assigned` is reachable only through `POST /incidents/{id}/assign`.
- Create is idempotent on `(source_type, source_id)` — `201` for a new incident,
  `200` for the existing one.

### One place the doc and the server disagree

`docs/api/incidents.md` §6 lists `invalid_transition` and `use_assign_endpoint`
as the `409` codes. The implementation returns **`409 conflict`** with an
explanatory `message` for all three 409 cases (the error handler maps the status
to a code, and the incident routes pin no override). Observed live:

```
POST /incidents/2/transitions {"to_status":"assigned", ...}
→ 409 {"error":{"code":"conflict","message":"use POST /incidents/{id}/assign to assign"}}
```

So this app keys off the **HTTP status plus the message**, never off those two
code strings. Worth fixing on the backend side (an `X-Error-Code` override would
do it) — the app does not depend on it either way.

## Demo

See `docs/frontend-demo.md` § *Fire-department simulator* for the end-to-end
walkthrough: create an incident on the backend, progress it here, then confirm
the persisted status from the browser.
