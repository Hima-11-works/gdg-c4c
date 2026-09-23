# Citizen intake: photo and local sensor evidence

This document is the contract for the citizen-intake evidence extension. It
covers the **exact routes, payloads, media access, error responses, and
verification states** before the implementation.

The existing fire-report API (`POST /api/v1/reports`, `GET /api/v1/reports`) is
**unchanged**. Every field, status code, and response key those routes already
return stays exactly as it is, because a deployed client depends on them
(`backend/tests/test_api_reports.py` asserts the response key set verbatim).
New evidence is attached through a **separate sub-resource** keyed by the
existing report id.

---

## 1. Concepts

| Term | Meaning |
| --- | --- |
| **Report** | An existing `POST /api/v1/reports` citizen fire/burning report. Identified by its integer `id`. |
| **Evidence** | Media and/or a local sensor reading attached to a report. One report has at most one evidence record. |
| **Citizen sensor reading** | A pollutant value submitted by a citizen device. **Never** a trusted station observation. |
| **Verification status** | The moderation/verification state of an evidence record. Defaults to `unverified`. |
| **Storage interface** | A replaceable `MediaStore` implementation that persists photo bytes and returns an opaque key. |

### 1.1 Citizen readings are not station observations

A citizen sensor value is stored **only** on the evidence record. It is never
written to `sensor_reading` (the trusted station table) and is never read by
`IDWPollutionEstimator` or the grid computation. The pollution model continues
to consume only trusted station observations. A citizen reading is displayed as
*unverified evidence*, never as a measurement driving an estimate.

This separation is structural, not conventional: the evidence store and the
`sensor_reading` table are distinct, and no code path copies one into the other.

---

## 2. Routes

All routes are under the existing `/api/v1` prefix.

### 2.1 Attach evidence — `POST /api/v1/reports/{report_id}/evidence`

`multipart/form-data`. Creates the report's evidence record, or, when
`client_report_id` identifies an evidence record already stored for that
report, returns the original unchanged (idempotent retry).

**Path parameter**

| Name | Type | Notes |
| --- | --- | --- |
| `report_id` | integer | The report's `id`. `404` if no such report exists. |

**Form fields** (all optional individually, but at least one of `photo` or a sensor field must be present)

| Field | Type | Required | Validation |
| --- | --- | --- | --- |
| `photo` | file | no | Content type in the allow-list; size ≤ `CITIZEN_MEDIA_MAX_BYTES`. |
| `client_report_id` | string (1–64) | no | Idempotency key for the evidence record. |
| `sensor_pollutant` | string (1–20) | no | Required if any sensor field is given. |
| `sensor_value` | float | no | Required with `sensor_pollutant`; must be finite and `≥ 0`. |
| `sensor_unit` | string (1–20) | no | Required with a value; e.g. `ug/m3`, `ppb`. |
| `sensor_measured_at` | RFC 3339 datetime | no | Required with a value; must be timezone-aware, not in the future by more than `CITIZEN_SENSOR_MAX_FUTURE_SKEW_SECONDS`, and not older than `CITIZEN_SENSOR_MAX_AGE_HOURS`. |
| `sensor_latitude` | float | no | Required with a value; `-90..90`. Defaults to the report's latitude when omitted. |
| `sensor_longitude` | float | no | Required with a value; `-180..180`. Defaults to the report's longitude when omitted. |
| `notes` | string (≤ 280) | no | Free text kept with the evidence. |

**Success — `201 Created`**

```json
{
  "generated_at": "2026-09-23T09:15:04Z",
  "is_demo": false,
  "data": {
    "id": 42,
    "report_id": 7,
    "client_report_id": "client-evidence-1",
    "verification_status": "unverified",
    "media": {
      "content_type": "image/jpeg",
      "byte_size": 184320,
      "sha256": "b1c2...",
      "url": "/api/v1/reports/7/evidence/photo",
      "is_placeholder": false
    },
    "sensor": {
      "pollutant": "pm25",
      "value": 87.5,
      "unit": "ug/m3",
      "measured_at": "2026-09-23T09:10:00Z",
      "latitude": 28.55,
      "longitude": 77.2,
      "source": "citizen",
      "verified": false
    },
    "notes": "Sensor clipped to balcony railing",
    "submitted_at": "2026-09-23T09:15:04Z"
  }
}
```

- `media` is `null` when no photo was submitted.
- `sensor` is `null` when no sensor value was submitted.
- `verification_status` is always present; it starts at `unverified`.
- `sensor.verified` mirrors the `verified`/`rejected` states — **always `false`
  for any non-verified state**, so a client can never mistake a citizen value
  for trusted data.

**Idempotent retry** — the same `client_report_id` for the same report returns
`200 OK` with the original record and an identical body (the server does not
create a second evidence row).

### 2.2 Retrieve evidence — `GET /api/v1/reports/{report_id}/evidence`

Returns the report's evidence record.

- `200 OK` with the same `data` shape as above.
- `404` if the report has no evidence record (or the report does not exist) —
  body `{"error": {"code": "not_found", ...}}`.

### 2.3 Access media — `GET /api/v1/reports/{report_id}/evidence/photo`

Streams the stored photo bytes through the storage interface.

- `200 OK` with the stored `Content-Type` when a photo exists.
- `404` when the report has no evidence or no photo.

Media is served **only** through this route (by report id), never as a raw
filesystem path, so the storage backend stays replaceable without changing the
public URL.

---

## 3. Storage interface

```python
class MediaStore(Protocol):
    def put(self, *, key: str, content: bytes, content_type: str) -> None: ...
    def get(self, *, key: str) -> tuple[bytes, str] | None: ...
```

- The **key** is a content-derived or id-derived opaque string; callers never
  build filesystem paths.
- Implementations:
  - `FilesystemMediaStore` — writes under a configured directory (default
    development backend).
  - `DisabledMediaStore` — accepts nothing; the photo field is rejected with
    `503` `media_unavailable` when no store is configured. This keeps a
    deployment without media storage honest instead of silently dropping bytes.
- Tests use an in-memory store.
- A future object-store (S3/GCS) implementation drops in without touching
  routes or services.

---

## 4. Error responses

All errors use the platform-wide shape:

```json
{"error": {"code": "...", "message": "...", "details": [...]?}}
```

| Case | Status | `code` |
| --- | --- | --- |
| Report id does not exist | 404 | `not_found` |
| No evidence record for the report | 404 | `not_found` |
| No photo on the record | 404 | `not_found` |
| Unknown report id type / bad path | 422 | `validation_error` |
| No photo and no sensor fields | 422 | `validation_error` |
| Unsupported photo content type | 415 | `unsupported_media_type` |
| Photo larger than `CITIZEN_MEDIA_MAX_BYTES` | 413 | `media_too_large` |
| Media storage not configured | 503 | `media_unavailable` |
| Sensor value out of range / non-finite | 422 | `validation_error` |
| Sensor coordinates out of range | 422 | `validation_error` |
| Sensor timestamp in the future / too old / naive | 422 | `validation_error` |
| Sensor unit empty or unknown shape | 422 | `validation_error` |
| Duplicate idempotency key with conflicting content | 409 | `conflict` |

`409 conflict` is only returned when the same `client_report_id` is reused with
**different** content; an identical retry is `200` idempotent success, never a
conflict.

---

## 5. Verification states

| State | Meaning | `sensor.verified` |
| --- | --- | --- |
| `unverified` | Default. Stored, shown as unverified citizen evidence. | `false` |
| `pending` | Queued for moderation/review. | `false` |
| `verified` | A moderator confirmed the evidence. | `true` |
| `rejected` | A moderator rejected the evidence. | `false` |

The status is stored on the evidence record and returned by both the POST and
GET routes. No automatic transition to `verified` exists; nothing in the intake
path marks its own evidence as trusted.

---

## 6. Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `CITIZEN_MEDIA_MAX_BYTES` | `5242880` (5 MiB) | Max accepted photo size. |
| `CITIZEN_MEDIA_ALLOWED_TYPES` | `image/jpeg,image/png,image/webp` | Accepted photo content types. |
| `CITIZEN_MEDIA_DIR` | `var/citizen_media` | Filesystem store directory, when used. |
| `CITIZEN_SENSOR_MAX_AGE_HOURS` | `72` | Oldest accepted sensor `measured_at`. |
| `CITIZEN_SENSOR_MAX_FUTURE_SKEW_SECONDS` | `300` | Allowed clock skew into the future. |
| `CITIZEN_SENSOR_POLLUTANTS` | `pm25,pm10` | Accepted `sensor_pollutant` values. |

---

## 7. What is deliberately out of scope

- No image content analysis, virus scanning, or EXIF stripping in this
  iteration — the store persists bytes, and the moderation status is the
  only quality gate.
- No authentication: like the rest of the MVP, intake is open (see the
  README's known limitations).
- Citizen sensor values never reach the pollution model. Promoting a verified
  reading into trusted observations is a future, explicitly separate step.
- No bulk listing of evidence; evidence is always addressed by report id.
