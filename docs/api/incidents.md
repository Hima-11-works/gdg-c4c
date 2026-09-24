# Incident workflow API

This document is the contract for the persistent incident workflow used by the
planned fire-department simulator. It covers the full API, the allowed state
transitions, permissions, idempotency, and error responses.

An **incident** is an operational record created from an eligible fire alert or
citizen report, then progressed through response states by a responding role.
It is deliberately separate from the alert/report it came from: an alert can
exist without an incident, and an incident keeps its own lifecycle, assignment,
jurisdiction, and append-only history.

---

## 1. Concepts

| Term | Meaning |
| --- | --- |
| **Source** | The thing an incident was created from: a fire `alert` or a citizen `fire_report`. |
| **Source key** | `(source_type, source_id)` — unique across incidents, so the same alert/report can never create two incidents. |
| **Status** | The incident's current response state. |
| **Responder role** | Who may act on the incident: `fire_department` or `pollution_control`. |
| **Event** | An append-only history row. Every state change and assignment writes one; history is never edited or deleted. |
| **Jurisdiction** | The administrative area the incident falls in, recorded at creation from the reported location. |

### 1.1 What makes a source eligible

- **Fire alerts**: an `alert` whose severity is `warning` or `critical`. A fire
  incident is created for these; `watch`-level alerts are not eligible.
- **Citizen reports**: a `fire_report` with a fire `kind`
  (`building_fire`, `industrial_fire`, `forest_fire`, `crop_burning`). A report
  of `other` is pollution-only and creates a `pollution_control` incident.

The resulting incident's `responder_role` is derived from the source:

| Source | Role |
| --- | --- |
| Fire alert (warning/critical) | `fire_department` |
| Fire report (`building_fire`, `industrial_fire`, `forest_fire`, `crop_burning`) | `fire_department` |
| Report with `kind = other` | `pollution_control` |
| Pollution-only alert (see 1.2) | `pollution_control` |

### 1.2 Pollution-only incidents

A pollution-only incident is one with no fire component. It is still
assignable, and to a `pollution_control` role specifically — a role that may
**not** act on a fire-department incident. This keeps fire response and
pollution enforcement from being conflated while sharing one workflow.

---

## 2. Permissions

**Every write operation requires a simulator API key.** The public API is
otherwise open (see the README's known limitations), but the incident workflow
is an operational tool, so anonymous changes must be impossible.

- The key is supplied in the `X-Simulator-Key` header.
- It is configured server-side (`SIMULATOR_API_KEY`); when unset, **all
  incident writes are refused** with `503 simulator_disabled` — the workflow is
  off, not silently unprotected.
- Read operations (`GET`) require no key, so a simulator view can render
  without holding the write credential.

Role is asserted per operation, not inferred from the key: a request carries the
acting role in the body/header, and the service rejects an action a role may not
take.

| Operation | Key | Role check |
| --- | --- | --- |
| Create incident from source | required | none |
| Assign incident | required | assignee role must match incident `responder_role` |
| Transition incident | required | actor role must match incident `responder_role` |
| Read incident / history / list | not required | none |

---

## 3. Routes

All routes are under `/api/v1`.

### 3.1 Create incident — `POST /api/v1/incidents`

Creates an incident from an eligible source. **Idempotent on the source**: a
second request for the same source returns the existing incident (`200`) rather
than creating a duplicate.

**Body**

```json
{
  "source_type": "alert",
  "source_id": 17,
  "severity": "critical",
  "jurisdiction": "Delhi",
  "linked_prediction_run_id": "demo-winter_stagnation-20250115T1200Z",
  "evidence_report_ids": [3, 4],
  "latitude": 28.61,
  "longitude": 77.21
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `source_type` | `alert` \| `report` | yes | |
| `source_id` | integer | yes | Must exist and be eligible. |
| `severity` | `watch` \| `warning` \| `critical` | no | Defaults to the source's severity (alert) or derived from the report. |
| `jurisdiction` | string (≤120) | no | Recorded as given; may be derived from location upstream. |
| `linked_prediction_run_id` | string (≤120) | no | The published v2 run the incident is associated with. |
| `evidence_report_ids` | integer[] | no | Citizen report ids used as evidence. |
| `latitude` / `longitude` | float | no | Defaults to the source's location. |

**Success — `201 Created`** (or `200` on an idempotent retry) with the incident
object (§3.5).

**Errors**: `404` unknown source, `422` ineligible source or bad payload,
`409` conflict (same source, conflicting attributes), `503` simulator disabled.
`401`/`403` for a missing/incorrect key.

### 3.2 Assign incident — `POST /api/v1/incidents/{id}/assign`

Assigns the incident to a responder. Moves `reported` → `assigned`.

**Body**

```json
{ "role": "fire_department", "assignee": "unit-12" }
```

- `role` must equal the incident's `responder_role`, else `403 role_mismatch`.
- `assignee` is a free string (≤120) naming the unit/person.
- Re-assigning an already-assigned incident updates the assignee and appends a
  new history event (never edits the old one).

**Errors**: `404`, `409` invalid transition (e.g. already `on_scene`),
`403` role mismatch, `401`/`503` for auth.

### 3.3 Transition incident — `POST /api/v1/incidents/{id}/transitions`

Advances the incident through the response state machine.

**Body**

```json
{ "to_status": "acknowledged", "role": "fire_department", "note": "Unit dispatched" }
```

- `to_status` must be allowed from the current status (§4), else
  `409 invalid_transition`.
- `role` must match the incident's `responder_role`, else `403 role_mismatch`.
- `note` is optional (≤500), stored on the history event.

**Errors**: `404`, `409` invalid transition, `403` role mismatch, `401`/`503`.

### 3.4 Read — `GET /api/v1/incidents`, `GET /api/v1/incidents/{id}`,

`GET /api/v1/incidents/{id}/history`

- List supports `?status=` and `?role=` filters.
- `GET /{id}` returns the incident object.
- `GET /{id}/history` returns the append-only event list, oldest first.

These are public (no key).

### 3.5 Incident object

```json
{
  "id": 5,
  "source_type": "alert",
  "source_id": 17,
  "status": "on_scene",
  "responder_role": "fire_department",
  "severity": "critical",
  "jurisdiction": "Delhi",
  "latitude": 28.61,
  "longitude": 77.21,
  "h3_cell": "8861892e0dfffff",
  "linked_prediction_run_id": "demo-winter_stagnation-20250115T1200Z",
  "evidence_report_ids": [3, 4],
  "assignee": "unit-12",
  "created_at": "2026-09-23T09:00:00Z",
  "updated_at": "2026-09-23T09:20:00Z",
  "resolved_at": null
}
```

Wrapped in the standard `Envelope`:
`{"generated_at": ..., "is_demo": false, "data": {...}}`.

### 3.6 History event object

```json
{
  "id": 31,
  "incident_id": 5,
  "event_type": "transition",
  "from_status": "assigned",
  "to_status": "acknowledged",
  "role": "fire_department",
  "actor": "unit-12",
  "note": "Unit dispatched",
  "created_at": "2026-09-23T09:20:00Z"
}
```

`event_type` is one of `created`, `assigned`, `transition`, `reassigned`.

---

## 4. Allowed transitions

```
reported ──assign──▶ assigned ──▶ acknowledged ──▶ en_route ──▶ on_scene ──▶ resolved
   │                    │              │               │             │
   └────────────────────┴──────────────┴───────────────┴─────────────┴──▶ cancelled
```

| From | Allowed to |
| --- | --- |
| `reported` | `assigned`, `cancelled` |
| `assigned` | `acknowledged`, `cancelled` |
| `acknowledged` | `en_route`, `cancelled` |
| `en_route` | `on_scene`, `cancelled` |
| `on_scene` | `resolved`, `cancelled` |
| `resolved` | *(terminal)* |
| `cancelled` | *(terminal)* |

Rules:

- Every transition stamps `updated_at`; entering `resolved` (or `cancelled`)
  also stamps `resolved_at`.
- A transition that is not in the table above is rejected with
  `409 invalid_transition` — including any transition out of a terminal state.
- `assigned` may only be entered via `POST /assign`; the generic transition
  route refuses `to_status: "assigned"` with `409 use_assign_endpoint`.
- Every accepted transition appends exactly one history event.

---

## 5. Idempotency

| Operation | Key | Behaviour |
| --- | --- | --- |
| Create incident | `(source_type, source_id)` | A repeat for the same source returns the existing incident with `200`. |
| Create with conflicting attributes | same source | `409 conflict` when severity/jurisdiction/location differ from the stored incident. |
| Assign | — | Re-assign updates the assignee; never a duplicate incident. |
| Transition | — | A repeat of the *same* `to_status` on an incident already in that status is a no-op returning the current incident (`200`), not an error — a retried request must not double-apply. A *different* invalid target is `409`. |

---

## 6. Error responses

All errors use the platform-wide shape
`{"error": {"code": "...", "message": "...", "details": [...]?}}`.

| Case | Status | `code` |
| --- | --- | --- |
| Missing/incorrect simulator key | 401 | `unauthorized` |
| Key valid but role not permitted | 403 | `role_mismatch` |
| Incident not found | 404 | `not_found` |
| Source not found | 404 | `not_found` |
| Source not eligible for an incident | 422 | `validation_error` |
| Bad payload (types, ranges, lengths) | 422 | `validation_error` |
| Invalid state transition | 409 | `invalid_transition` |
| Using the transition route to assign | 409 | `use_assign_endpoint` |
| Same source, conflicting attributes | 409 | `conflict` |
| Simulator writes disabled (no key configured) | 503 | `simulator_disabled` |

---

## 7. Worked sequence

Starting from an eligible alert, this is the documented end-to-end flow.

```bash
KEY=...   # the configured SIMULATOR_API_KEY

# 1. Create from a fire alert (201; idempotent retry is 200)
curl -X POST localhost:8000/api/v1/incidents \
  -H "X-Simulator-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"source_type":"alert","source_id":17,"jurisdiction":"Delhi"}'

# 2. Assign (reported -> assigned)
curl -X POST localhost:8000/api/v1/incidents/5/assign \
  -H "X-Simulator-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"role":"fire_department","assignee":"unit-12"}'

# 3. Progress through response states
for S in acknowledged en_route on_scene resolved; do
  curl -X POST localhost:8000/api/v1/incidents/5/transitions \
    -H "X-Simulator-Key: $KEY" -H 'Content-Type: application/json' \
    -d "{\"to_status\":\"$S\",\"role\":\"fire_department\"}"
done

# 4. Retrieve the complete history (public)
curl localhost:8000/api/v1/incidents/5/history
```

Expected history: `created`, `assigned`, `transition` × 4, ending at `resolved`.

---

## 8. Out of scope

- No automatic creation on alert/report ingest in this iteration: incidents are
  created explicitly through the route.
- No notification/webhook delivery on transition.
- No per-actor identity beyond the role/assignee string; the API key gates
  access, it does not identify a user.
- No incident deletion. Cancellation is a state, not a delete.
- Pollution-only incidents remain operational records; they do not change the
  published prediction run.
