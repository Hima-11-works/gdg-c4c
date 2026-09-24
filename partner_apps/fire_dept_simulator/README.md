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
  --dart-define=SIMULATOR_API_KEY=sim-local-dev-key \
  --dart-define=SIMULATOR_ACTOR_ID=engine-7
```

- An **Android emulator** reaches the host's localhost at `10.0.2.2`, so use
  `--dart-define=INCIDENT_API_BASE_URL=http://10.0.2.2:8001`.
- All three values are also editable in the app's **Settings**, held in memory
  (see below). A **Test connection** button there distinguishes "cannot reach
  the backend" from "the backend answered and refused".
- `SIMULATOR_ACTOR_ID` must be an id the backend's `SIMULATOR_ACTORS` registry
  knows, and its registered role must match the incident: a fire-department
  console needs a fire-department actor. Anything else is refused with `401` or
  `403`, which is the point.

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

### The identity contract: key **and** actor

Every write now carries **two** credentials, and they are not interchangeable:

| Header | What it authenticates |
| --- | --- |
| `X-Simulator-Key` | the *deployment* |
| `X-Actor-Id` | *which responder within it* is acting |

The actor id is resolved server-side against `SIMULATOR_ACTORS`
(`<actor_id>:<role>[:<jurisdiction>]`), which is the authority on who exists.
That has three consequences this app is built around:

- **The request body cannot widen authority.** The `role` field that
  `assign`/`transitions` used to send is gone; the service decides the acting
  role from the actor's registry entry, and a body could only ever contradict
  it.
- **An unregistered id is `401`**, and an actor whose role does not own the
  incident is `403 role_mismatch` — as is a scoped actor acting outside its
  jurisdiction (`403 jurisdiction_mismatch`).
- **A key alone is not enough.** `canWrite` requires both, and the Settings
  screen says which one is missing before you press anything.

### The 409 codes the doc and the server previously disagreed about — resolved

An earlier revision of `docs/api/incidents.md` §6 listed `invalid_transition` and
`use_assign_endpoint`, while the implementation returned `409 conflict` for all
three cases; this app keyed off the HTTP status plus the message as a
workaround. **That disagreement is gone** — the routes now pin their own codes,
verified live against the current backend:

```
POST /incidents/1/transitions {"to_status":"on_scene"}   (from assigned)
→ 409 {"error":{"code":"invalid_transition","message":"'assigned' -> 'on_scene' is not allowed"}}

POST /incidents/2/transitions {"to_status":"assigned"}   (from reported)
→ 409 {"error":{"code":"use_assign_endpoint","message":"use POST /incidents/{id}/assign to assign"}}
```

So the app now keys off the code itself (`isInvalidTransition`), and still shows
the server's own message verbatim.

### Other verified behaviour worth knowing

- A **repeat** of the status the incident is already in is a **no-op `200`**, not
  an error — so a retried or duplicated tap cannot double-apply.
- Transitioning *out of* a terminal state is `409 invalid_transition`.
- A **published-alert incident** has `source_id: null` and is named by
  `source_ref` (`v2:<run>:<cell>:<hours>`). `sourceId`, `latitude` and
  `longitude` are nullable in the model for exactly this reason, and a missing
  coordinate reads as "not recorded" rather than as `0, 0`.
- Assignment writes a **simulated** delivery row: the incident appears in the
  addressed role's inbox, `simulated` is forced true by a database constraint,
  and no email, SMS, webhook or push exists anywhere in the service.

## Demo

See `docs/frontend-demo.md` § *Fire-department simulator* for the end-to-end
walkthrough: create an incident on the backend, progress it here, then confirm
the persisted status from the browser.
