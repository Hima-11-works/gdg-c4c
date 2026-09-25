# Citizen reports — lifecycle, trust, and the review path

F1. The contract for `POST /api/v1/reports`, the reads that report a report's
standing, and the review path that decides whether it counts.

## 1. What changed, and why it was urgent

Before F1, a citizen report was write-once and immediately trusted. Any accepted
`POST` became a modeled point source within `FIRE_REPORT_MAX_AGE_HOURS`: the
endpoint is unauthenticated, coordinates are attacker-controlled, there was no
geofence, no rate limit, no review, and no status. One anonymous POST could move
modeled PM2.5 by up to 144 µg/m³ at the source cell (180 × intensity × kind
weight).

F1 keeps submission exactly as cheap as it was — one POST, same fields, same
response — and makes the server do the checking.

| | Before | After |
| --- | --- | --- |
| Coordinates | any lat/lon in range | must be **inside India** (ADM1 states/UTs), else `422 outside_india` |
| Volume | unbounded | per-source and platform-wide caps, else `429` + `Retry-After` |
| Duplicates | each POST a new row | same cell + kind inside the window **clusters** as corroboration |
| Standing | none | `submitted` → `under_review` → `corroborated`/`rejected`, plus `expired` |
| Effect on the model | every active report | **only `corroborated`**, and only while unexpired |
| Review | none | `POST /reports/{id}/moderation`, reviewer-key gated, every move audited |
| Citizen visibility | none | status, meaning, and expiry on every read |

## 2. The lifecycle

```
                    ┌──────────────┐
      submit ──────▶│  submitted   │  a CLAIM: cannot alter the model
                    └──┬───┬───┬───┘
          review ───────┘   │   └──────▶ rejected ──▶ under_review (appeal)
                            │                     (terminal once expired)
                            ▼
                     ┌──────────────┐
                     │ under_review │
                     └──┬────────┬──┘
              ┌─────────┘        └─────────┐
              ▼                            ▼
      ┌───────────────┐             ┌──────────────┐
      │  corroborated │◀────────────│   expired    │  (terminal; a new
      │  ONLY status  │  reopen     │  window shut │   observation is a
      │  that may act │             └──────────────┘   new report)
      └───────────────┘
```

`expired` is a **fact about the clock**, not a judgement: `expires_at` is fixed at
submission from the same `FIRE_REPORT_MAX_AGE_HOURS` the plume model uses, so the
read side and the model can never disagree about what is still active. A report
is reported as `expired` the moment its window closes, whether or not
`python -m app.cli expire-reports` has run; the sweep only makes the *stored*
status agree, which matters for the audit trail.

Legal transitions live in `app.domain.report_lifecycle.STATUS_TRANSITIONS` and are
enforced by `assert_transition`. An illegal move is `409`, never a silent no-op.

## 3. The one predicate

```python
is_model_qualified(status, expires_at=..., now=...)  # status is CORROBORATED and not expired
```

This is the single rule, and it is enforced in **two** places on purpose:

- `PlumeFireGradientModel.contributions` drops anything unqualified — the
  guarantee, at the point where the field actually changes, so a caller that
  ignores the filter still cannot move the model.
- `SqlFireReportRepository.list_active_qualified` filters in the query, so the
  ordinary path does not ship every anonymous claim into the model to discard it.

**F8 must consume this same predicate** for incidents, so "allowed to affect the
model" and "allowed to raise an authority incident" cannot diverge. On this branch
there is no incident path yet, so the requirement is recorded rather than
implemented.

## 4. API

### `POST /api/v1/reports` — unchanged contract

Request and response are **byte-identical to before**. `tests/test_api_reports.py`
asserts the ten response keys verbatim and still passes untouched. What changed is
the refusals:

| Situation | Status | `error.code` |
| --- | --- | --- |
| Outside India | 422 | `outside_india` |
| Geofence asset unreadable | 503 | `geofence_unavailable` |
| Per-source cap reached | 429 | `rate_limited_source` (+ `Retry-After`) |
| Platform cap reached | 429 | `rate_limited_platform` (+ `Retry-After`) |
| Malformed body | 422 | `validation_error` |

**A geofence asset that cannot be read is a 503, never "accept everything"** — a
missing data file must not silently turn into an open endpoint.

### `GET /api/v1/reports` — unchanged

Same ten fields per row. Note it now returns *claims* as well as corroborated
reports: the list is "what people have told us", not "what we counted".

### `GET /api/v1/reports/{id}` — new, public

Status, meaning, and timing for one report:

```json
{
  "id": 12, "h3_cell": "883da11467fffff", "kind": "crop_burning", "...": "the v1 fields",
  "status": "submitted",
  "status_meaning": "received and waiting for review; an unverified claim that does not affect the air-quality model",
  "is_verified": false,
  "affects_air_quality_model": false,
  "reported_at": "2026-09-26T10:00:00+00:00",
  "expires_at": "2026-09-26T22:00:00+00:00",
  "seconds_until_expiry": 43200,
  "last_status_change_at": "2026-09-26T10:00:00+00:00",
  "corroborating_report_count": 0,
  "cluster_id": "c-ee7eb7c2d370752c",
  "evidence_count": 0,
  "evidence_expected": false
}
```

Reviewer identity and moderation notes are **deliberately absent** — this read is
public and they name a person. They are on the reviewer-gated audit read.

### `GET /api/v2/reports` and `GET /api/v2/reports/{id}` — new, versioned

Reads whose *shape* changed get a version. The v2 list is the v1 fields plus the
status block on **every row**, so a client can render one screen without a request
per report. This is what the web map and the citizen app read.

### `GET /api/v1/reports/statuses` — new, public

The lifecycle itself, so a client can explain a status without hardcoding it.

### `POST /api/v1/reports/{id}/moderation` — new, reviewer-key gated

```bash
curl -X POST http://localhost:8000/api/v1/reports/12/moderation \
  -H 'content-type: application/json' \
  -H 'X-Reviewer-Key: <REPORTS_REVIEWER_KEY>' \
  -d '{"status":"corroborated","actor":"reviewer-7",
       "note":"matched a FIRMS detection in the same cell"}'
```

`401` without a valid key, `503` when no key is configured (**review off, not
open**), `409` on an illegal transition. `actor` is the name written to the audit
trail.

### `GET /api/v1/reports/{id}/audit` — new, reviewer-key gated

The append-only history: every submission, clustering and status change, with
actor, timestamp, from/to status and a note.

## 5. Idempotency and retries

`client_report_id` remains optional and remains the idempotency key. The ordering
inside `submit` is the security property:

1. **geofence** — cheapest and strictest rejection, before anything is counted;
2. **idempotency** — an accepted retry returns the original immediately, so a
   client retrying through a flaky network is **not** charged rate-limit budget
   twice;
3. **caps** — counted from stored rows, so a restart cannot reset a limit;
4. **clustering**.

A retry therefore cannot stack a report *or* get the citizen throttled for
retrying. The web form mints one id per open form (a `useRef`); the Flutter sheet
now does the same with a `late final` field. It previously used a **getter** that
re-ran `DateTime.now()` on every access, so every retry minted a new id and the
backend correctly stored a duplicate — the exact failure the id exists to prevent.

## 6. Abuse control

Two caps, both counted from stored rows:

| Cap | Default | Scope |
| --- | --- | --- |
| `REPORTS_RATE_LIMIT_PER_HOUR` | 5 | one /24 network prefix |
| `REPORTS_GLOBAL_LIMIT_PER_HOUR` | 500 | the platform |

The source unit is a **/24 prefix, never a full client address** — coarse enough
to blunt a flood, far less identifying to store, and never echoed in a response.
It is nullable, because a report can also arrive without an HTTP client (a CLI
seed, a test, an internal caller) and then has no source to count.

This is a floor, not a defence in depth: there is no identity, so a determined
attacker with many source addresses is only bounded by the platform cap. Real
abuse control needs accounts or a challenge; this is what an open endpoint can
honestly do.

## 7. Clustering and corroboration

A report landing in the same cell, of the same kind, within
`REPORTS_CLUSTER_WINDOW_HOURS` joins the open claim already there instead of
becoming an independent one. It increments `corroborating_report_count` on **both**
records, and both get a `clustered` audit event.

Clustering is what makes "corroborated" mean something: one person reporting twice
is not corroboration, and neither is two reports of *different* events. It is a
count, and it is a human or a corroborating signal that promotes a cluster — never
the count alone.

## 8. F2 linkage, without making images mandatory

`fire_report.evidence_count` exists so F2 can show a citizen how much evidence is
attached. It is deliberately **not** an input to qualification:

- a report with zero evidence is a first-class `submitted` claim;
- `report_detail` returns `evidence_expected: false` explicitly;
- a reviewer may corroborate a photo-less report on other grounds, and the
  lifecycle permits it.

F2 adds a `report_evidence` table keyed to `fire_report.id` (already prototyped on
the unmerged `backhima` branch) plus `consent_at`, `retention_expires_at`,
`deleted_at` and `review_state`. The linkage point designed here is the report id
plus `evidence_count`; nothing in F1 needs to change when it lands.

## 9. Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `REPORTS_REVIEWER_KEY` | unset | unset ⇒ review is **off** (503), never open |
| `REPORTS_RATE_LIMIT_PER_HOUR` | 5 | per /24 prefix |
| `REPORTS_GLOBAL_LIMIT_PER_HOUR` | 500 | platform-wide (must be ≥ the per-source cap) |
| `REPORTS_CLUSTER_WINDOW_HOURS` | 6.0 | duplicate-clustering window |
| `REPORTS_CLUSTER_CORROBORATION_THRESHOLD` | 2 | reports a cluster needs before it is worth corroborating |
| `REPORTS_REQUIRE_INDIA_GEOFENCE` | true | off only to test the fence itself |

`REPORTS_CLUSTER_CORROBORATION_THRESHOLD` is recorded for the reviewer queue F1
does not yet have; it is not used to auto-promote anything, because auto-promotion
is exactly the behaviour F1 removed.

## 10. Migration

`0016_f1_report_lifecycle` — additive only. Every column has a server default and
no existing row is rewritten except to receive `status='submitted'`, so a
deployment that upgrades **stops trusting unreviewed claims** rather than
continuing to. It also creates `fire_report_event` (append-only), and two indexes
(`status, reported_at` and `cluster_id`).

The revision id is `0016`, not the next free number on this branch (`0011`),
because the unmerged `backhima` line already uses `0011`–`0015`. Taking `0011`
would make two revisions claim the same id and turn the step-0 merge into a
rewrite. See `docs/IMPLEMENTATION_SCOPE.md` §1.4.

## 11. Limits, stated plainly

- **No identity.** The reviewer key is a shared secret, not a person. The audit
  trail records the `actor` string the caller supplies, so the history says who
  acted, but nothing *verifies* who that is. A fake identity system would be worse
  than an honest shared secret.
- **No auto-corroboration.** A cluster reaching the threshold is a queue, not a
  decision. Nothing promotes a report without a reviewer.
- **The geofence is approximate** at ~2 km (see `app/domain/india.py`): a report
  within ~2 km of a land border may be accepted or refused. It gates acceptance
  only; it never raises confidence and never contributes to the plume.
- **Abuse control is a floor**, as described in §6.
- **Nothing here attributes a source.** A corroborated report affects a modeled
  concentration near a location. It does not name an industry, a facility or a
  person, and `FireReport` has no field that could.
- **The geofence asset must ship.** It is committed under
  `app/domain/data/india_geofence.json` (~317 KB, geoBoundaries ADM1, ODC-ODbL)
  and regenerated by `scripts/build-india-geofence.py`. If it is missing, the
  endpoint returns 503 rather than accepting anything.
