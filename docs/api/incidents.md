# Incident workflow API

The contract for the persistent incident workflow used by the
fire-department simulator: how an incident is opened from what the web
actually shows, who may act on it, what a responder sees when it is assigned
to them, and what the whole thing refuses to do.

An **incident** is an operational record created from a published v2 alert, a
persisted fire alert, or a citizen report, then progressed through response
states by an authenticated responder. It is deliberately separate from its
source: an alert can exist without an incident, and an incident keeps its own
lifecycle, assignment, jurisdiction, and append-only history.

Three rules shape everything below:

1. **A published alert has a name.** The web reads `GET /api/v2/alerts`, so an
   alert from there can be acted on directly, idempotently (§2).
2. **Writes carry an identity, not just a key.** The simulator key
   authenticates the deployment; `X-Actor-Id` names the responder, and the
   server decides that responder's role and jurisdiction (§3).
3. **Delivery is simulated.** Assignment makes an incident visible in the
   addressed role's inbox. **No email, SMS, webhook, push, or any other
   notification is sent by this system** (§5, §6).

---

## 1. Concepts

| Term | Meaning |
| --- | --- |
| **Source** | What the incident was created from: a `published_alert`, a persisted fire `alert`, or a citizen `fire_report`. |
| **Source key** | `(source_type, source_id)` for an alert/report, `(source_type, source_ref)` for a published alert. Unique across incidents, so one source can never create two incidents. |
| **Status** | The incident's current response state. |
| **Responder role** | Who may act: `fire_department` or `pollution_control`. |
| **Actor** | An authenticated responder identity (`actor_id` + role + jurisdiction) resolved server-side. |
| **Jurisdiction** | The administrative area the incident falls in, and the area an actor is scoped to. |
| **Event** | An append-only history row. Every creation, assignment, delivery, and transition writes one. Never edited or deleted. |
| **Delivery** | A *simulated* hand-off of an incident to one responder role's inbox. |

### 1.1 What makes a source eligible

| Source | Eligible when | Role |
| --- | --- | --- |
| `published_alert` | the named run/cell/horizon exists in a published v2 run **and** its forecast PM2.5 is ≥ `ALERT_WARNING_PM25` (91 µg/m³) | `pollution_control` |
| `alert` (persisted v1) | severity is `warning` or `critical` (`watch` is informational) | `pollution_control` |
| `report` with a fire `kind` (`building_fire`, `industrial_fire`, `forest_fire`, `crop_burning`) | — | `fire_department` |
| `report` with `kind = other` | — | `pollution_control` |

Severity defaults to the source's own: the published alert's classification
(`critical` ≥ 250 µg/m³, `warning` ≥ 91), the persisted alert's severity, or a
role-derived default for a report. A caller may override `severity`; on a retry
a *different* severity is a conflict, not an update.

A pollution-only incident is still assignable, and to `pollution_control`
specifically — a role that may **not** act on a fire-department incident. This
keeps fire response and pollution enforcement from being conflated while
sharing one workflow.

---

## 2. Published alert identity

`GET /api/v2/alerts` derives alerts from a published run, so an alert has no
row of its own. It has a natural identity, returned as `alert_id`:

```
v2:<run_id>:<h3_cell>:<forecast_minutes>

v2:pred-20260924T0000Z-india:8861892e0dfffff:360
```

| Property | Why it holds |
| --- | --- |
| Deterministic | A pure function of the run, cell, and horizon — no lookup, no clock, no row number. |
| Stable | Coordinates in the publication space, not in the database: re-reading `/api/v2/alerts`, re-publishing, or restarting cannot change it. |
| Run-scoped | A cell critical in one run and clear in the next is a *different* alert, because the severity belongs to that run. |
| Value-free | The forecast value and severity are **not** part of the id, so retuning a threshold cannot invalidate ids already in use. They are resolved from the published run when the id is used. |

The id is computed by `app.domain.published_alerts.PublishedAlertIdentity` and
resolved by `app.services.published_alerts.PublishedAlertService`, which is also
the single definition of the severity rule shared with `/api/v2/alerts`. A
malformed id is `422`; an id naming a run/cell/horizon that does not exist is
`404`; a real published cell below the alert threshold is `422` (not an alert).

The final component is the forecast horizon in minutes, matching the API's
15-minute forecast intervals. The horizon must be > 0 and at most 360; the
current-hour row is not an alert.

---

## 3. Permissions: key **and** actor

| Step | Requirement |
| --- | --- |
| Simulator key | `X-Simulator-Key: $SIMULATOR_API_KEY` on every write. Unset → **all** writes are `503 simulator_disabled`. Wrong/missing → `401`. |
| Actor identity | `X-Actor-Id: <id>` on every write, resolved against `SIMULATOR_ACTORS`. Missing or unregistered → `401`. Empty registry → `503 simulator_disabled`. |
| Role | The actor's registered role must equal the incident's `responder_role` → else `403 role_mismatch`. |
| Jurisdiction | An actor scoped to a jurisdiction may only assign/transition incidents in that jurisdiction → else `403 jurisdiction_mismatch`. |
| Body `role` | Optional. If present it must equal the actor's role — a body can never widen authority → else `403 role_mismatch`. |

`SIMULATOR_ACTORS` is a comma-separated registry of
`<actor_id>:<role>[:<jurisdiction>]`:

```bash
SIMULATOR_ACTORS="control-room:pollution_control:Delhi,unit-12:pollution_control:Delhi,engine-7:fire_department:Delhi"
```

- The registry is the **authority on who exists**. A caller cannot invent a role
  or a jurisdiction; an `X-Actor-Id` that is not listed cannot act at all.
- An entry with no jurisdiction (`control-room:pollution_control`) is *not
  scoped* and may act anywhere it holds the right role.
- A malformed entry is a configuration error: every write fails `503` with the
  reason, rather than half the registry working.
- Reads need no key and no actor: `GET` routes are public, like the rest of the
  simulator views, and expose nothing the incident list does not already.

Because the acting role comes from the registry, a request body can no longer
assert authority — which is why `role` in the assign/transition bodies is
optional and only checked for agreement.

| Operation | Key | Actor | Role check | Jurisdiction check |
| --- | --- | --- | --- | --- |
| Create from source | required | required | — (the incident's role is derived from the source) | — (the payload may classify the incident; it defaults to the actor's jurisdiction) |
| Assign | required | required | actor role == `responder_role` | yes |
| Transition | required | required | actor role == `responder_role` | yes |
| Read incident / history / list / inbox / deliveries | not required | not required | — | — |

**Why creation is not jurisdiction-checked:** a new incident has no jurisdiction
to violate. Naming the jurisdiction is the act of *classifying* the incident,
which any authenticated actor may do; only *responding* to it is scoped. The
creating actor is recorded in the first history event either way.

---

## 4. Routes

All routes are under `/api/v1`. `Envelope` is the platform-wide wrapper:
`{"generated_at": ..., "is_demo": false, "data": ...}`.

### 4.1 Create an incident — `POST /api/v1/incidents`

**From a published alert** (what the web shows):

```bash
curl -X POST localhost:8000/api/v1/incidents \
  -H "X-Simulator-Key: $KEY" -H "X-Actor-Id: control-room" \
  -H 'Content-Type: application/json' \
  -d '{
        "source_type": "published_alert",
        "source_ref": "v2:pred-20260924T0000Z-india:8861892e0dfffff:6",
        "evidence_report_ids": [3, 4]
      }'
```

**From a citizen report or a persisted alert**:

```bash
curl -X POST localhost:8000/api/v1/incidents \
  -H "X-Simulator-Key: $KEY" -H "X-Actor-Id: control-room" \
  -H 'Content-Type: application/json' \
  -d '{"source_type": "report", "source_id": 17, "jurisdiction": "Delhi"}'
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_type` | `published_alert` \| `alert` \| `report` | yes | |
| `source_ref` | string (≤200) | for `published_alert` | The `v2:…` identity (§2). Must **not** be sent for `alert`/`report`. |
| `source_id` | integer | for `alert`/`report` | Must exist and be eligible. Must **not** be sent for `published_alert`. |
| `severity` | `watch` \| `warning` \| `critical` | no | Defaults to the source's own severity. |
| `jurisdiction` | string (≤120) | no | Defaults to the creating actor's jurisdiction. |
| `latitude` / `longitude` | float | no | Defaults to the source's location (the cell centre for a cell-level source). |
| `linked_prediction_run_id` | string (≤120) | no | For a published alert this is **derived** from the identity and cannot be overridden. |
| `evidence_report_ids` | integer[] | no | Citizen report ids used as evidence. |

Exactly one of `source_id` / `source_ref` is required, and it must match
`source_type`; anything else is `422`.

**Success — `201 Created`**, or `200 OK` for an idempotent retry (§7).

A published-alert incident also records `source_synthetic: true` when the run
is a demo/synthetic publication, so a fallback run's incident cannot be read as
a real-world event.

### 4.2 Assign — `POST /api/v1/incidents/{id}/assign`

`reported` → `assigned`. **This is what opens the simulated inbox (§5).**

```bash
curl -X POST localhost:8000/api/v1/incidents/5/assign \
  -H "X-Simulator-Key: $KEY" -H "X-Actor-Id: unit-12" \
  -H 'Content-Type: application/json' \
  -d '{"assignee": "unit-12", "role": "pollution_control"}'
```

- `role` is optional and, if given, must match the actor's registered role.
- `assignee` (≤120) names the unit/person the incident is handed to.
- Re-assigning appends a `reassigned` event and a **new** inbox item; the
  previous one is never edited or removed.
- Assigning an incident that is not `reported`/`assigned` → `409
  invalid_transition`.

### 4.3 Transition — `POST /api/v1/incidents/{id}/transitions`

```bash
curl -X POST localhost:8000/api/v1/incidents/5/transitions \
  -H "X-Simulator-Key: $KEY" -H "X-Actor-Id: unit-12" \
  -H 'Content-Type: application/json' \
  -d '{"to_status": "acknowledged", "note": "Unit dispatched"}'
```

- `to_status` must be allowed from the current status (§6), else `409
  invalid_transition`.
- `role` is optional, checked as in §4.2.
- Transitioning to `acknowledged` also closes the incident's open inbox items
  (§5) — the responder's acknowledgement is what answers the delivery.
- A repeat of the same `to_status` is a no-op `200`, so a retried request never
  double-applies or appends a second event.

### 4.4 Reads (public, no key)

| Route | Returns |
| --- | --- |
| `GET /api/v1/incidents?status=&role=` | Incident list; filters are the incident's own fields. |
| `GET /api/v1/incidents/{id}` | One incident. |
| `GET /api/v1/incidents/{id}/history` | Append-only event list, oldest first. |
| `GET /api/v1/incidents/{id}/deliveries` | Simulated delivery records, oldest first. |
| `GET /api/v1/incidents/inbox?role=&only_open=` | A responder role's simulated inbox, newest first (§5). |

### 4.5 Objects

**Incident**

```json
{
  "id": 5,
  "source_type": "published_alert",
  "source_id": null,
  "source_ref": "v2:pred-20260924T0000Z-india:8861892e0dfffff:6",
  "source_synthetic": false,
  "status": "on_scene",
  "responder_role": "pollution_control",
  "severity": "critical",
  "jurisdiction": "Delhi",
  "latitude": 28.61,
  "longitude": 77.21,
  "h3_cell": "8861892e0dfffff",
  "linked_prediction_run_id": "pred-20260924T0000Z-india",
  "evidence_report_ids": [3, 4],
  "assignee": "unit-12",
  "created_at": "2026-09-24T09:00:00Z",
  "updated_at": "2026-09-24T09:20:00Z",
  "resolved_at": null
}
```

**History event**

```json
{
  "id": 31,
  "incident_id": 5,
  "event_type": "transition",
  "from_status": "assigned",
  "to_status": "acknowledged",
  "role": "pollution_control",
  "actor": "unit-12",
  "actor_jurisdiction": "Delhi",
  "note": "Unit dispatched",
  "created_at": "2026-09-24T09:20:00Z"
}
```

`event_type` is `created`, `assigned`, `reassigned`, `delivered`, or
`transition`. `actor`/`actor_jurisdiction` identify *which authority* acted, not
merely which role.

**Delivery (simulated)**

```json
{
  "id": 8,
  "incident_id": 5,
  "audience_role": "pollution_control",
  "status": "simulated",
  "assignee": "unit-12",
  "simulated": true,
  "notification": "none (simulated inbox only; no email, SMS, or webhook is sent)",
  "simulated_at": "2026-09-24T09:05:00Z",
  "acknowledged_at": null
}
```

**Inbox item**

```json
{ "delivery": { "...": "as above" }, "incident": { "...": "the incident" }, "is_open": true }
```

---

## 5. The simulated responder inbox

Assignment writes an `incident_delivery` row addressed to the incident's
`responder_role` — not to whoever did the assigning. The intended role's inbox
(`GET /api/v1/incidents/inbox?role=…`) then shows it, and only that role's inbox
does: a fire-department view never lists a pollution-control assignment.

This is a **simulation, structurally**:

- No email, SMS, webhook, push, or socket is sent. There is no notification code
  path in the backend at all.
- The `incident_delivery` table has a CHECK constraint forcing `simulated =
  true`, so the database cannot record a row claiming a real dispatch, and it
  has no channel, address, or provider column to send one to.
- Every delivery in the API response carries `"simulated": true` and a
  `notification` string that begins with `"none"`, so a consumer of the API
  cannot mistake it for a real dispatch.
- `status` is `simulated` (open) or `acknowledged`; the responder's
  `acknowledged` transition closes their open items, and the DB CHECK keeps
  `status` and `acknowledged_at` consistent.

Acknowledging the incident marks the delivery `acknowledged`; the incident
status and the inbox can therefore never disagree.

---

## 6. Allowed transitions

```
reported ──assign──▶ assigned ──▶ acknowledged ──▶ en_route ──▶ on_scene ──▶ resolved
   │                    │              │               │             │
   └────────────────────┴──────────────┴───────────────┴─────────────┴──▶ cancelled
```

| From | Allowed to |
| --- | --- |
| `reported` | `assigned` (via `/assign` only), `cancelled` |
| `assigned` | `acknowledged`, `cancelled` |
| `acknowledged` | `en_route`, `cancelled` |
| `en_route` | `on_scene`, `cancelled` |
| `on_scene` | `resolved`, `cancelled` |
| `resolved` | *(terminal)* |
| `cancelled` | *(terminal)* |

- Every accepted transition stamps `updated_at`; entering a terminal state also
  stamps `resolved_at`.
- Anything not in the table is `409 invalid_transition`, including every
  transition out of a terminal state.
- A generic transition to `assigned` is `409 use_assign_endpoint`.
- Every accepted transition appends exactly one event; every assignment appends
  an `assigned`/`reassigned` event **and** a `delivered` event for the simulated
  hand-off.

---

## 7. Idempotency

| Operation | Key | Behaviour |
| --- | --- | --- |
| Create from a published alert | `(published_alert, source_ref)` | A repeat for the same alert id returns the existing incident with `200`; no second row, no second `created` event. |
| Create from an alert/report | `(alert\|report, source_id)` | Same. |
| Create with conflicting attributes | same source | `409 conflict` when severity, jurisdiction, or the linked run differ from the stored incident. The stored record is untouched. |
| Assign | — | Re-assignment updates the assignee and appends new events; never a duplicate incident. |
| Transition | — | A repeat of the same `to_status` on an incident already in that status is a no-op `200`, not an error. A *different* invalid target is `409`. |
| Acknowledge | — | Closing inbox items is idempotent by status. |

Uniqueness is enforced in the database, not only in the service: partial unique
indexes on `(source_type, source_id)` and `(source_type, source_ref)`, so a
racing duplicate create returns the existing incident instead of a storage
error.

---

## 8. Error responses

All errors use the platform-wide shape
`{"error": {"code": "...", "message": "...", "details": [...]?}}`.

| Case | Status | `code` |
| --- | --- | --- |
| Missing/incorrect simulator key | 401 | `unauthorized` |
| Missing `X-Actor-Id`, or an actor not in the registry | 401 | `unauthorized` |
| Actor's role is not the incident's role | 403 | `role_mismatch` |
| Body `role` disagrees with the actor's registered role | 403 | `role_mismatch` |
| Actor's jurisdiction does not cover the incident | 403 | `jurisdiction_mismatch` |
| Incident not found | 404 | `not_found` |
| Source not found (report, alert, or published run/cell/horizon) | 404 | `not_found` |
| Source not eligible (watch alert; published cell below the threshold) | 422 | `validation_error` |
| Malformed published-alert id, or the wrong source reference for the type | 422 | `validation_error` |
| Bad payload (types, ranges, lengths) | 422 | `validation_error` |
| Invalid state transition | 409 | `invalid_transition` |
| Using the transition route to assign | 409 | `use_assign_endpoint` |
| Same source, conflicting attributes | 409 | `conflict` |
| Simulator or actor registry not configured | 503 | `simulator_disabled` |

---

## 9. Worked sequences

Set up once:

```bash
export KEY=...                                  # SIMULATOR_API_KEY
export PC='-H "X-Simulator-Key: $KEY" -H "X-Actor-Id: control-room"'
```

### 9.1 Published alert → one incident → resolution

```bash
# 0. the alert the web is showing
curl "localhost:8000/api/v2/alerts?run_id=pred-20260924T0000Z-india"
#    -> data[0].alert_id = "v2:pred-20260924T0000Z-india:8861892e0dfffff:6"

# 1. create (201; a retry is 200 and returns the same incident)
ALERT='v2:pred-20260924T0000Z-india:8861892e0dfffff:6'
curl -X POST localhost:8000/api/v1/incidents \
  -H "X-Simulator-Key: $KEY" -H "X-Actor-Id: control-room" \
  -H 'Content-Type: application/json' \
  -d "{\"source_type\":\"published_alert\",\"source_ref\":\"$ALERT\"}"

# 2. assign -> reported=assigned, and the incident appears in the responder's inbox
curl -X POST localhost:8000/api/v1/incidents/5/assign \
  -H "X-Simulator-Key: $KEY" -H "X-Actor-Id: unit-12" \
  -H 'Content-Type: application/json' -d '{"assignee":"unit-12"}'
curl "localhost:8000/api/v1/incidents/inbox?role=pollution_control"   # simulated: true

# 3. the responder acknowledges (closes their inbox item), then progresses
for S in acknowledged en_route on_scene resolved; do
  curl -X POST localhost:8000/api/v1/incidents/5/transitions \
    -H "X-Simulator-Key: $KEY" -H "X-Actor-Id: unit-12" \
    -H 'Content-Type: application/json' -d "{\"to_status\":\"$S\"}"
done

# 4. complete history
curl localhost:8000/api/v1/incidents/5/history
```

Expected history: `created`, `assigned`, `delivered`, then one `transition` per
step, ending at `resolved` — with every event naming `unit-12` / `Delhi`.

### 9.2 Citizen report → one incident → resolution

```bash
REPORT=$(curl -sS -X POST localhost:8000/api/v1/reports \
  -H 'Content-Type: application/json' \
  -d '{"latitude":28.55,"longitude":77.20,"kind":"crop_burning",
       "smoke_intensity":4,"duration_hours":1.5}' | jq -r .data.id)

curl -X POST localhost:8000/api/v1/incidents \
  -H "X-Simulator-Key: $KEY" -H "X-Actor-Id: control-room" \
  -H 'Content-Type: application/json' \
  -d "{\"source_type\":\"report\",\"source_id\":$REPORT}"      # -> fire_department

# A fire responder assigns and drives it to resolution (X-Actor-Id: engine-7)
```

### 9.3 The three refusals

```bash
# duplicate creation: same source, identical payload -> 200, same incident
curl -X POST localhost:8000/api/v1/incidents -H "X-Simulator-Key: $KEY" \
  -H "X-Actor-Id: control-room" -H 'Content-Type: application/json' \
  -d "{\"source_type\":\"published_alert\",\"source_ref\":\"$ALERT\"}"

# duplicate creation with a different jurisdiction -> 409 conflict
curl -X POST localhost:8000/api/v1/incidents -H "X-Simulator-Key: $KEY" \
  -H "X-Actor-Id: control-room" -H 'Content-Type: application/json' \
  -d "{\"source_type\":\"published_alert\",\"source_ref\":\"$ALERT\",\"jurisdiction\":\"Mumbai\"}"

# forbidden role: engine-7 is fire_department, this incident is pollution_control
curl -X POST localhost:8000/api/v1/incidents/5/assign -H "X-Simulator-Key: $KEY" \
  -H "X-Actor-Id: engine-7" -H 'Content-Type: application/json' \
  -d '{"assignee":"engine-7"}'                                  # -> 403 role_mismatch

# forbidden role by claim: unit-12 cannot act as fire_department by saying so
curl -X POST localhost:8000/api/v1/incidents/5/assign -H "X-Simulator-Key: $KEY" \
  -H "X-Actor-Id: unit-12" -H 'Content-Type: application/json' \
  -d '{"assignee":"u1","role":"fire_department"}'               # -> 403 role_mismatch

# forbidden jurisdiction: unit-12 is Delhi-only, the incident is in Mumbai
curl -X POST localhost:8000/api/v1/incidents/5/assign -H "X-Simulator-Key: $KEY" \
  -H "X-Actor-Id: unit-12" -H 'Content-Type: application/json' \
  -d '{"assignee":"u1"}'                                        # -> 403 jurisdiction_mismatch

# invalid transition: reported -> on_scene skips two states
curl -X POST localhost:8000/api/v1/incidents/5/transitions \
  -H "X-Simulator-Key: $KEY" -H "X-Actor-Id: unit-12" \
  -H 'Content-Type: application/json' -d '{"to_status":"on_scene"}'
# -> 409 invalid_transition, and the incident is unchanged
```

---

## 10. Deliberately out of scope

- **No real notifications of any kind.** The inbox is a table, not a dispatch.
  Nothing is emailed, texted, or posted anywhere.
- **No incident creation on ingest.** Incidents are created explicitly through
  the route (automatic creation from a published alert is a possible future
  change, and would need its own deduplication policy).
- **No incident deletion.** Cancellation is a state, not a delete.
- **No per-person identity.** An actor is a registry entry (`actor_id`, role,
  jurisdiction) — not an authenticated human. There is no login, session, or
  token; the simulator key plus the actor header is the whole model.
- **No jurisdiction derivation.** A jurisdiction is whatever the creating call
  says (defaulting to the actor's); nothing geocodes a cell into an
  administrative area.
- **No reassignment approval chain, unit availability, or ETAs.** `assignee` is
  a free string.
- **Pollution-only incidents remain operational records**; they do not change the
  published prediction run.
