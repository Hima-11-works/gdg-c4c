# Citizen intake: photo and local sensor evidence

The contract for the two-step citizen intake flow:

1. the citizen creates a fire report (`POST /api/v1/reports` — **unchanged**);
2. the client attaches an optional photo and/or a local sensor reading
   (`POST /api/v1/reports/{report_id}/evidence`).

Evidence is a **sub-resource keyed by the report id**, so the existing report
routes keep their exact fields, status codes, and response keys
(`backend/tests/test_api_reports.py` asserts the response key set verbatim).
Nothing about the first step changed; this document is about the second step,
its failure modes, and what a deployment must provide.

---

## 1. Concepts

| Term | Meaning |
| --- | --- |
| **Report** | An existing `POST /api/v1/reports` citizen fire/burning report, identified by its integer `id`. |
| **Evidence** | A photo and/or a local sensor reading attached to a report. One report has at most one evidence record. |
| **Citizen sensor reading** | A pollutant value from a citizen device. **Never** a trusted station observation. |
| **Verification status** | Moderation state of the record. Always starts at `unverified`. |
| **Media store** | The replaceable `MediaStore` backend that persists photo bytes and returns an opaque, content-addressed key. |

### 1.1 Citizen readings are not station inputs

A citizen sensor value is stored **only** on the evidence record. It is never
written to `sensor_reading` (the trusted station table) and is never read by
`IDWPollutionEstimator` or the grid computation. The pollution model continues
to consume only trusted station observations; a citizen reading is displayed as
*unverified evidence*, never as a measurement driving an estimate.

The separation is structural, not conventional: the evidence table and the
`sensor_reading` table are distinct, `CitizenSensorReading` is a different type
from the domain `SensorReading`, and
`tests/test_citizen_intake_hardening.py::test_citizen_reading_never_becomes_a_trusted_observation`
plus `::test_intake_modules_do_not_touch_trusted_observations` fail if the
intake path ever reaches the models/db layer or imports the trusted type.

---

## 2. The exact request sequence

`BASE` is the API root, e.g. `http://localhost:8000`. Photo examples assume
durable storage is configured (section 6); without it the photo is refused with
`503 media_unavailable` and the sensor-only path still works.

### Step 1 — create the report

```bash
curl -sS -X POST "$BASE/api/v1/reports" \
  -H 'content-type: application/json' \
  -d '{
        "latitude": 28.55,
        "longitude": 77.20,
        "kind": "crop_burning",
        "smoke_intensity": 4,
        "duration_hours": 1.5,
        "notes": "stubble burning near the canal",
        "client_report_id": "fire-android-8f21c0"
      }'
```

```json
{"generated_at":"...","is_demo":false,"data":{"id":7,"h3_cell":"...","latitude":28.55,
 "longitude":77.2,"kind":"crop_burning","smoke_intensity":4,"duration_hours":1.5,
 "notes":"stubble burning near the canal","client_report_id":"fire-android-8f21c0",
 "reported_at":"..."}}
```

Keep `data.id` — every step below uses it.

### Step 2 — attach a photo and/or a sensor reading

One `multipart/form-data` request. Supply the photo, the sensor fields, or
both. `client_report_id` is the idempotency key for the evidence record: reuse
the **same value** on every retry of the same upload.

```bash
curl -sS -X POST "$BASE/api/v1/reports/7/evidence" \
  -F 'photo=@fire.jpg;type=image/jpeg' \
  -F 'client_report_id=ev-8f21c0' \
  -F 'sensor_pollutant=pm25' \
  -F 'sensor_value=87.5' \
  -F 'sensor_unit=ug/m3' \
  -F 'sensor_measured_at=2026-09-24T09:10:00Z' \
  -F 'sensor_latitude=28.55' \
  -F 'sensor_longitude=77.20' \
  -F 'notes=sensor clipped to the balcony railing'
```

`201 Created` (first time) — or `200 OK` for an idempotent retry (section 4):

```json
{"generated_at":"...","is_demo":false,"data":{
  "id":42,"report_id":7,"client_report_id":"ev-8f21c0",
  "verification_status":"unverified",
  "media":{"content_type":"image/jpeg","byte_size":184320,"sha256":"b1c2…",
           "url":"/api/v1/reports/7/evidence/photo","is_placeholder":false},
  "sensor":{"pollutant":"pm25","value":87.5,"unit":"ug/m3",
            "measured_at":"2026-09-24T09:10:00Z","latitude":28.55,"longitude":77.2,
            "source":"citizen","verified":false},
  "notes":"sensor clipped to the balcony railing","submitted_at":"..."}}
```

- `media` is `null` with no photo; `sensor` is `null` with no reading.
- `verification_status` is always present and starts at `unverified`.
- `sensor.verified` is `false` for every non-verified state, so a client can
  never mistake a citizen value for trusted data.

### Step 3 — retrieve both

```bash
curl -sS "$BASE/api/v1/reports/7/evidence"            # the evidence record
curl -sS "$BASE/api/v1/reports/7/evidence/photo" \
     -o retrieved-fire.jpg -D -                    # the photo bytes
```

`GET …/evidence` → `200` with the same `data` shape as step 2, or `404`
`not_found` when the report has no evidence. `GET …/evidence/photo` → `200`
with the stored `Content-Type`, or `404` when the record carries no photo.

### Step 4 — retry an interrupted upload

Re-send **the identical request** (same report id, same `client_report_id`,
same bytes, same sensor values). The response is `200 OK` with the original
record; no second record and no second blob are created:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/api/v1/reports/7/evidence" \
  -F 'photo=@fire.jpg;type=image/jpeg' \
  -F 'client_report_id=ev-8f21c0' \
  -F 'sensor_pollutant=pm25' -F 'sensor_value=87.5' -F 'sensor_unit=ug/m3' \
  -F 'sensor_measured_at=2026-09-24T09:10:00Z'
# 201 on the first attempt, 200 on every retry
```

If the photo bytes are gone from storage, the same retry **restores them** (the
key is derived from the content). If the payload differs from what is already
stored, the request is a genuine conflict: `409 conflict`.

---

## 3. Photo validation: the bytes decide

A photo is accepted only when the **content** is a complete image of an
allow-listed format. The declared `Content-Type` is a claim; the bytes are the
evidence.

| Check | Rule | Failure |
| --- | --- | --- |
| Declared type | Must be in `CITIZEN_MEDIA_ALLOWED_TYPES`, unless it is a generic label (`application/octet-stream`, `binary/octet-stream`, `*/*`, empty) | `415 unsupported_media_type` |
| Size | ≤ `CITIZEN_MEDIA_MAX_BYTES`; the API reads one byte past the cap and refuses without buffering the whole body | `413 media_too_large` |
| Format | Signature must be JPEG, PNG, or WebP | `415 unrecognized_media_content` |
| Completeness | JPEG needs its EOI marker, PNG its `IEND` trailer, WebP a RIFF size that matches the body | `415 media_content_invalid` |
| Declared vs actual | A specific declared image type must match the sniffed type | `415 media_content_mismatch` |

Consequences worth knowing:

- **A truncated upload is refused.** A file that starts correctly and stops
  early (the shape of an interrupted transfer) is `media_content_invalid`, not
  a stored photo whose URL renders nothing.
- **A generic declared type is normalized, not punished.** A PNG uploaded as
  `application/octet-stream` is stored as `image/png`.
- **Format detection is structural, not a decode.** No image library is
  involved; a file with a valid header/trailer but corrupt scan data passes
  this gate and remains subject to moderation (section 7).

### Sensor validation

| Field | Rule | Failure |
| --- | --- | --- |
| `sensor_pollutant` | In `CITIZEN_SENSOR_POLLUTANTS` (`pm25`, `pm10`) | `422 validation_error` |
| `sensor_value` | Finite, `≥ 0` | `422 validation_error` |
| `sensor_unit` | Non-empty | `422 validation_error` |
| `sensor_measured_at` | RFC 3339, timezone-aware, not more than `CITIZEN_SENSOR_MAX_FUTURE_SKEW_SECONDS` in the future, not older than `CITIZEN_SENSOR_MAX_AGE_HOURS` | `422 validation_error` |
| `sensor_latitude` / `sensor_longitude` | `-90..90` / `-180..180`; default to the report's own position when omitted | `422 validation_error` |
| all four of pollutant/value/unit/time | Required together | `422 validation_error` |

A reading is **not calibrated**: it is recorded with its unit and provenance
`source: "citizen"` and stays unverified. Nothing in this flow converts a
citizen value into a trusted observation.

---

## 4. Retry and idempotency rules

| Situation | Result |
| --- | --- |
| First submission for the report | `201 Created`, one record |
| Same report, same `client_report_id`, identical payload | `200 OK`, the original record, no duplicate |
| Same report, bytes lost from storage, identical payload | `200 OK`, the photo bytes are rewritten under the same content-addressed key |
| A concurrent identical request won the insert race | `200 OK` (never `201` for an existing record) |
| Same report, same `client_report_id`, **different** payload | `409 conflict`; the stored record is untouched |
| Photo bytes written but the request failed before the row was committed | The retry records the report once; the blob is reused, not duplicated |

The `client_report_id` column is unique per report
(`uq_report_evidence_report_client_id`), and the report itself is unique on
`report_id` (`uq_report_evidence_report_id`), so the database is the final
arbiter under concurrency.

---

## 5. Error responses

All errors use the platform-wide shape. Because one status can mean several
intake conditions, the response body always carries the exact machine code
(the route pins it through the platform's `X-Error-Code` mechanism rather than
letting the status decide):

```json
{"error": {"code": "media_content_mismatch", "message": "photo declared as image/png but the bytes are image/jpeg"}}
```

| Case | Status | `code` |
| --- | --- | --- |
| Report id does not exist | 404 | `not_found` |
| No evidence record for the report | 404 | `not_found` |
| No photo on the record | 404 | `not_found` |
| Bad path parameter / malformed multipart | 422 | `validation_error` |
| No photo and no sensor fields | 422 | `validation_error` |
| Declared photo type outside the allow-list | 415 | `unsupported_media_type` |
| Photo content is not an accepted image format | 415 | `unrecognized_media_content` |
| Photo is a truncated/incomplete image | 415 | `media_content_invalid` |
| Declared type contradicts the actual bytes | 415 | `media_content_mismatch` |
| Photo larger than `CITIZEN_MEDIA_MAX_BYTES` | 413 | `media_too_large` |
| No media backend configured, or the directory is unusable | 503 | `media_unavailable` |
| Store accepted bytes but could not read them back | 503 | `media_not_durable` |
| Photo recorded in the database but its bytes are missing | 503 | `media_unavailable` |
| Sensor value/unit/time/coordinate out of contract | 422 | `validation_error` |
| Same `client_report_id` reused with different content | 409 | `conflict` |

The last 503 is deliberate: a photo whose bytes vanished is a **storage
failure, not a missing resource**. Answering `404` would tell the citizen their
upload never happened; `503` says "recorded, retry later".

Demonstrating rejection:

```bash
# a PDF renamed to .jpg: declared type is refused first
curl -sS -X POST "$BASE/api/v1/reports/7/evidence" \
  -F 'photo=@notes.pdf;type=application/pdf'
# 415 {"error":{"code":"unsupported_media_type",…}}

# a JPEG announced as PNG
curl -sS -X POST "$BASE/api/v1/reports/7/evidence" \
  -F 'photo=@fire.jpg;type=image/png'
# 415 {"error":{"code":"media_content_mismatch",…}}

# a half-uploaded JPEG (head -c 200 of a real photo)
curl -sS -X POST "$BASE/api/v1/reports/7/evidence" \
  -F 'photo=@truncated.jpg;type=image/jpeg'
# 415 {"error":{"code":"media_content_invalid",…}}

# an unsupported pollutant
curl -sS -X POST "$BASE/api/v1/reports/7/evidence" \
  -F 'sensor_pollutant=co2' -F 'sensor_value=400' -F 'sensor_unit=ppm' \
  -F 'sensor_measured_at=2026-09-24T09:10:00Z'
# 422 {"error":{"code":"validation_error",…}}

# a negative concentration
curl -sS -X POST "$BASE/api/v1/reports/7/evidence" \
  -F 'sensor_pollutant=pm25' -F 'sensor_value=-3' -F 'sensor_unit=ug/m3' \
  -F 'sensor_measured_at=2026-09-24T09:10:00Z'
# 422 {"error":{"code":"validation_error",…}}

# no durable storage configured
curl -sS -X POST "$BASE/api/v1/reports/7/evidence" -F 'photo=@fire.jpg;type=image/jpeg'
# 503 {"error":{"code":"media_unavailable",…}}
```

---

## 6. Deployment requirements for photos

Photo storage is **off by default**. A deployment that has not configured a
durable store answers `503 media_unavailable` for photos while sensor-only
intake keeps working — the API never writes bytes into a directory nobody
promised to keep.

| Variable | Default | Meaning |
| --- | --- | --- |
| `CITIZEN_MEDIA_STORAGE` | `disabled` | `disabled` or `filesystem`. |
| `CITIZEN_MEDIA_DIR` | *(empty)* | Directory for the filesystem backend. Required when the backend is `filesystem`; selecting `filesystem` without it is a startup configuration error. |
| `CITIZEN_MEDIA_MAX_BYTES` | `5242880` (5 MiB) | Maximum accepted photo size. |
| `CITIZEN_MEDIA_ALLOWED_TYPES` | `image/jpeg,image/png,image/webp` | Accepted photo formats. |
| `CITIZEN_SENSOR_MAX_AGE_HOURS` | `72` | Oldest accepted sensor `measured_at`. |
| `CITIZEN_SENSOR_MAX_FUTURE_SKEW_SECONDS` | `300` | Allowed clock skew into the future. |
| `CITIZEN_SENSOR_POLLUTANTS` | `pm25,pm10` | Accepted `sensor_pollutant` values. |

**Enable photos**

```bash
export CITIZEN_MEDIA_STORAGE=filesystem
export CITIZEN_MEDIA_DIR=/var/lib/air-health/citizen-media   # persistent volume
python -m app.cli verify-media-storage                        # must exit 0
```

`verify-media-storage` performs a real write / read-back / delete round trip in
the configured location and prints the resolved path. Exit codes:

| Exit | Meaning |
| --- | --- |
| `0` | Photos can be stored on this host. |
| `1` | Photos would be refused (`disabled`, an unusable directory, or a store that cannot read back). Use it as a deploy/pipeline gate. |

**What "durable" means here**

- The filesystem backend fsyncs each blob, renames it into place atomically,
  fsyncs the directory, and then reads the file back and compares it. A store
  that accepts bytes it cannot return raises `media_not_durable` (503) instead
  of returning `201` for a photo that does not exist.
- `verify-media-storage` additionally proves the path is writable *on the host
  that will serve the request*. It cannot detect a platform that discards the
  filesystem on scale-to-zero.
- **Serverless platforms (Vercel, Cloud Run without a volume, Heroku dynos with
  an ephemeral filesystem): keep `CITIZEN_MEDIA_STORAGE=disabled`.** Those
  filesystems do not survive a restart or a new instance, so photos would be
  accepted and then vanish. Either accept sensor-only intake, or mount a
  persistent volume (`CITIZEN_MEDIA_STORAGE=filesystem` + `CITIZEN_MEDIA_DIR`),
  or implement an object-store `MediaStore` and select it in
  `app/services/media_storage.py::build_media_store` — the public URL and the
  API contract do not change.
- Single-instance only: the filesystem backend is not shared. Multiple API
  replicas need shared storage (volume or object store).

Photo bytes are content-addressed (`<sha256>.<ext>`, two-level fan-out), so
retries never duplicate a blob. Deleting a report cascades the evidence row
(`ON DELETE CASCADE`); the blob itself is not garbage-collected — a retention
job is out of scope (section 7).

---

## 7. Verification states and deliberate limits

| State | Meaning | `sensor.verified` |
| --- | --- | --- |
| `unverified` | Default. Stored, shown as unverified citizen evidence. | `false` |
| `pending` | Queued for moderation. | `false` |
| `verified` | A moderator confirmed the evidence. | `true` |
| `rejected` | A moderator rejected the evidence. | `false` |

No automatic transition to `verified` exists; nothing in the intake path marks
its own evidence as trusted.

Out of scope, on purpose:

- **No image analysis, EXIF stripping, or malware scanning.** The format gate
  proves the bytes are a complete image of an accepted type; the moderation
  status is the only quality gate. EXIF (which can carry GPS coordinates) is
  stored as-is and is **not** stripped — treat the media directory as
  sensitive.
- **No authentication.** Intake is open, like the rest of the MVP.
- **No calibration** of citizen readings, and no promotion of a reading into
  trusted observations.
- **No blob garbage collection** for deleted evidence.
- **No bulk listing** of evidence; evidence is addressed by report id.
