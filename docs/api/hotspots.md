# Candidate hotspot detection

This is the contract for the **candidate-hotspot detector**
(`app.services.hotspot_detection`, `python -m app.cli hotspot-scan`,
`GET /api/v1/hotspots`).

## 1. What a hotspot candidate is, and what it is not

A **candidate hotspot** is a *georeferenced place a person should look*. It is
produced from satellite imagery and is deliberately weak on its own.

| This is | This is not |
| --- | --- |
| A cell-level location derived from a versioned, georeferenced imagery index, with its acquisition time, H3 cell, WGS84 coordinates, detector version, confidence, supporting sources and review status recorded. | **Not a PM2.5 value.** No concentration is ever reported. `pm25_ugm3` exists on a candidate and is typed `None`; the domain type cannot hold a float, and the response schema refuses a non-null one. |
| A **candidate for human review**, emitted as `review_status: pending_human_review`. | **Not automatically confirmed.** Nothing in this feature confirms, dismisses, grades, or notifies anyone about a candidate. |
| An anomaly *somewhere*, optionally corroborated by a FIRMS thermal detection and/or a verified ground-station PM2.5 reading. | **Not an identified industrial source.** `source_attribution` is always `unattributed`. Imagery cannot say who or what caused an anomaly, and this feature never guesses. |
| A triage signal for a reviewer, with a bounded, explainable confidence. | **Not a calibrated probability.** Confidence is a fixed sum of contributions (`confidence_basis` spells out every term), not a likelihood that a hotspot is real. |

Every scan and every API response repeats these limits in a `limitations` block,
so they cannot be lost between the detector and a reader.

## 2. The detector

One detector, version **`hotspot-candidate-v1`**, recorded on every candidate.

**Imagery is the trigger.** A cell becomes a candidate when a declared imagery
index value reaches the trigger threshold, the cell is not cloud-masked, and the
imagery is fresh. Nothing else can create a candidate.

**FIRMS and station evidence are supporting signals.** They raise a candidate's
confidence and are recorded as evidence, but a fire detection or a high station
reading in a cell whose imagery is below the trigger produces *no* candidate —
this is tested, and demonstrated by the negative fixture.

**The index is produced upstream.** The detector consumes a declared index
product (its name, version, licence and scale travel with the artifact). It does
not derive smoke or aerosol physics from raw pixels. A high index can be cloud,
haze, dust, or biomass burning, and the evaluation below exists precisely because
that is not free.

**Georeferencing is verified, not trusted.** Every tile, fire and station carries
an H3 cell *and* coordinates; the detector recomputes the cell from the
coordinates at the artifact's declared resolution. A mismatch is refused
(`HotspotInputError`), not warned about: an unlocated pixel is not evidence.

**Nothing is used before it was available.** Imagery, fires and stations each
carry `available_at`, checked against the scan time. A small clock skew
(`HOTSPOT_MAX_FUTURE_SKEW_SECONDS`, default 300 s) is tolerated; anything further
ahead is refused as lookahead. A scan cannot be helped by data that did not exist
when it claims to have run.

**It is bounded.** `HOTSPOT_MAX_TILES` (default 20 000) tiles per scan — more is
an error. `HOTSPOT_MAX_CANDIDATES` (default 500) candidates — excess is dropped
worst-first, with the count recorded in `truncated_candidates` and a reason on the
scan. At most 5 evidences per supporting source are listed per candidate, and the
rest are counted in the candidate's notes.

### Confidence

| Contribution | Points | Condition |
| --- | --- | --- |
| imagery trigger | +0.40 | index >= `HOTSPOT_SMOKE_INDEX_THRESHOLD` (default 0.55) |
| strong imagery | +0.15 | index >= `HOTSPOT_STRONG_INDEX_THRESHOLD` (default 0.75) |
| FIRMS support | +0.20 | a usable detection in the same cell with FRP >= `HOTSPOT_FIRE_SUPPORT_FRP_MW` (default 1 MW) |
| station support | +0.15 | a usable, verified, non-excluded reading in the same cell with PM2.5 >= `HOTSPOT_STATION_SUPPORT_PM25_UGM3` (default 60 µg/m³) |

Ceiling **0.90**. Bands: `low` < 0.50 <= `medium` < 0.75 <= `high`.

Imagery plus a station reading tops out at `medium` (0.70) on purpose: one ground
reading is weaker corroboration than a thermal anomaly, so reaching `high` wants a
FIRMS detection too.

A station reading is only corroboration when it is **verified** and its source is
not synthetic/scenario/demo/authored-fixture/citizen
(`DEFAULT_EXCLUDED_STATION_SOURCES`). The shipped fixtures use
`authored-fixture` stations on purpose: a hand-written reading must not make a
demo look better corroborated than any real run could be. Every supplied signal
lands in exactly one bucket in the scan's `signal_counts`, so what was used *and
what was dropped, and why* is visible.

## 3. Inputs and their provenance

The detector takes one required input and two optional ones.

**Imagery artifact (required).** `artifact_id`, `source`, `product`,
`product_version`, `index_name`, `license`, `h3_resolution`, `acquired_at`,
`available_at`, `synthetic`, and `tiles`. Each tile: `tile_id`, `h3_cell`,
`latitude`, `longitude`, `acquired_at`, `index_value` (0–1), `cloud_fraction`
(0–1). The recorded provenance digest (`imagery_digest`) is **computed from the
tiles the detector read**, not copied from a claimed value, so the record cannot be
contradicted by its own input.

**FIRMS signals (optional).** `detection_id`, `h3_cell`, `latitude`, `longitude`,
`acquired_at`, `available_at`, `frp_mw`, `confidence_class`, `satellite`, `source`,
`product_version`. The platform's stored FIRMS detections
(`app.domain.environmental_observations.FireHotspot`) map onto these with
`fire_signal_from_hotspot`; **no live adapter is wired yet** — the committed
fixtures are the only callers today.

**Station signals (optional).** `station_id`, `h3_cell`, `latitude`, `longitude`,
`measured_at`, `available_at`, `pm25_ugm3`, `source`, `verified`.

**Authored labels (optional, evaluation only).** `h3_cell`, `kind`
(`authored_hotspot` | `authored_clear`), `label_source`, `note`.

Case fixtures are parsed strictly: **unknown keys are refused**, not ignored, so a
typo cannot silently produce a different scan.

## 4. Insufficient evidence

`verdict: insufficient_evidence` means the detector could not look. It is
deliberately different from "the inputs were present and nothing qualified"
(`verdict: candidates` with an empty candidate list). It is produced when:

| Situation | Verdict | Reason on the scan |
| --- | --- | --- |
| No imagery artifact | `insufficient_evidence` | "no georeferenced satellite imagery was supplied; a candidate cannot be produced from FIRMS or station data alone" |
| Imagery not yet available at the scan time | `insufficient_evidence` | "imagery was not available until …, after the scan time …" |
| No tile is fresh, or every tile is cloud-masked | `insufficient_evidence` | "no imagery tile was eligible: N stale, N cloud-masked, N not yet available …" |

An insufficient scan carries no candidates, and its evaluation is `no_inputs` with
`sufficient: false` and `precision`/`recall` `null` — never `0.0`, and never a
perfect score. The `signal_counts` block still reports the FIRMS and station
signals that were supplied, because "we had signals but no imagery" is the useful
statement.

## 5. Evaluation against labelled fixtures

`evaluation` is computed for every scan that ran, from four kept-apart buckets:

| Bucket | Meaning |
| --- | --- |
| matched | predicted **and** labelled a hotspot (true positive) |
| false positives | predicted **and** labelled clear — a *demonstrated* false positive |
| missed | labelled a hotspot and not predicted (false negative) |
| unlabelled | predicted with no label either way — **not scored** |

An unlabelled prediction is neither an error nor credit: nothing authored says the
cell was clear, so counting it as an error would understate the detector and
counting it as correct would overstate it. The count and the cells are reported.

`precision` is `tp / (tp + fp)` and `recall` is `tp / labelled_positives`, both
`null` when undefined (no labelled positives, no scored predictions). The offending
cells are listed in `false_positive_cells` and `missed_cells`, so the numbers can be
audited rather than trusted.

**No evaluation here is real-world evidence.** `usable_as_real_world_evidence` is
always `false` and `label_provenance` records what the labels were. The shipped
labels are authored fixtures: they measure this detector against a known answer, and
say nothing about how it performs on a real day. With no labels at all the status is
`insufficient_labels` with `sufficient: false`.

## 6. The command

```bash
cd backend

# one small fixture:
python -m app.cli hotspot-scan --fixture tests/fixtures/hotspots/positive_hotspot.json

# where the scan is recorded (default: HOTSPOT_SCAN_DIR, i.e. var/hotspots):
python -m app.cli hotspot-scan --fixture tests/fixtures/hotspots/positive_hotspot.json \
    --out-dir var/hotspots

# every shipped case at once, plus the full report JSON:
python -m app.cli hotspot-scan --fixture-dir tests/fixtures/hotspots \
    --out-dir var/hotspots --out /tmp/hotspot-report.json

# override the scan time (defaults to the fixture's own scan_time, so a case is
# reproducible on any machine on any day):
python -m app.cli hotspot-scan --fixture tests/fixtures/hotspots/positive_hotspot.json \
    --evaluated-at 2025-11-08T05:30:00Z
```

Exit codes, so a script can tell the three situations apart:

| Code | Meaning |
| --- | --- |
| `0` | the detector ran — **including "it ran and found nothing"** (empty candidate list, `candidates` verdict) |
| `2` | insufficient evidence: no usable imagery, so nothing was detected. Not a failure, never a silent zero |
| `1` | the command could not run: unreadable fixture, bad georeferencing, bad `--evaluated-at`, or a store that could not be written |

With `--fixture-dir`, any insufficient case makes the command exit `2`.

Recorded scans are written durably (fsync, then read back and compared) to
`<out-dir>/<scan_id>.json`, where `scan_id` is
`<case-id>-<YYYYMMDDThhmmssZ>-<imagery-digest[:8]>` — deterministic, value-free
and filesystem-safe.

## 7. The API

Read-only. A scan is produced by the command and served from
`HOTSPOT_SCAN_DIR`; the API never re-runs a detector on request and never invents
a scan.

### `GET /api/v1/hotspots`

The detector contract and the recorded scans: `detector_version`, `trigger`,
`supporting_signals`, `required_inputs`, `outputs`, `bounds` (every threshold in
force), one row per recorded scan (`scan_id`, `case_id`, `evaluated_at`,
`verdict`, `candidate_count`, `evaluation_status`, `false_positives`,
`false_negatives`, `precision`, `recall`, `synthetic_input`, `reasons`), and
`limitations`.

`is_demo` is `true` when any recorded scan has authored imagery or an
`insufficient_evidence` verdict.

### `GET /api/v1/hotspots/{scan_id}`

The full recorded scan. `404` for an unknown id; `500 internal_error` for a
recorded scan that does not satisfy the contract (for example a tampered record
carrying a `pm25_ugm3`, which the response schema refuses).

Candidate fields, all present on every candidate:

```json
{
  "candidate_id": "hotspot:authored-positive-delhi-ncr-20251108T053000Z-a3eb07bd:883da11467fffff",
  "h3_cell": "883da11467fffff",
  "latitude": 28.61,
  "longitude": 77.2,
  "acquired_at": "2025-11-08T04:30:00+00:00",
  "detector_version": "hotspot-candidate-v1",
  "confidence": "high",
  "confidence_score": 0.75,
  "confidence_basis": [
    "imagery index 0.82 >= trigger threshold 0.55 (+0.4)",
    "index 0.82 >= strong threshold 0.75 (+0.15)",
    "1 FIRMS detection(s) at or above 1 MW in the same cell (+0.2)"
  ],
  "supporting_sources": ["satellite_imagery", "firms"],
  "evidence": [
    {
      "source": "satellite_imagery",
      "observed_at": "2025-11-08T04:30:00+00:00",
      "detail": "0.82 on the declared imagery index, cloud fraction 0.08",
      "index_value": 0.82
    },
    {
      "source": "firms",
      "observed_at": "2025-11-08T04:45:00+00:00",
      "detail": "12.4 MW, confidence high, VIIRS-NOAA20 - a thermal anomaly, not an attribution",
      "frp_mw": 12.4,
      "detection_id": "fixture-fire-1"
    }
  ],
  "index_value": 0.82,
  "pm25_ugm3": null,
  "value_semantics": "candidate_location_for_human_review",
  "source_attribution": "unattributed",
  "review_status": "pending_human_review",
  "reviewed_by": null,
  "reviewed_at": null,
  "notes": "candidate_location_for_human_review: this record does not report a PM2.5 value and does not attribute a source; it is emitted as unattributed for human review."
}
```

The scan wraps that with `verdict`, `reasons`, `imagery` (the full artifact
provenance), `imagery_digest`, `tile_counts`, `signal_counts`, `truncated_candidates`,
`evaluation`, `config` and `limitations`.

`review_status` has three values (`pending_human_review`,
`reviewed_not_confirmed`, `reviewed_confirmed_by_reviewer`) so the review
lifecycle is a stable contract; **only the first is ever produced today**, and a
candidate that is not pending must name a reviewer, so the state cannot be forged
by flipping a string.

## 8. The shipped cases

`backend/tests/fixtures/hotspots/` — all three are **authored test data on a real
H3 resolution-8 grid, not observations of a real event**. The area is an arbitrary
Delhi-NCR test grid; the labels describe what the fixture contains, not what is
burning there.

| Fixture | Case | What it shows | Result |
| --- | --- | --- | --- |
| `positive_hotspot.json` | `authored-positive-delhi-ncr` | one authored hotspot, high imagery index, a FIRMS detection in the same cell, plus an authored station reading that is *excluded* from confidence | 1 candidate, `high` (0.75), sources `satellite_imagery+firms`; `tp=1 fp=0 fn=0`, precision 1.00, recall 1.00 |
| `negative_distractor.json` | `authored-negative-distractor-delhi-ncr` | a haze distractor labelled clear, a real hotspot hidden by cloud, an unlabelled prediction, a strong authored reading and a weak fire in cells that do not trigger | 2 candidates, both `low` (0.40); `tp=0 fp=1 fn=1 unlabelled=1`, precision 0.00, recall 0.00 |
| `unavailable_no_imagery.json` | `authored-unavailable-no-imagery` | a FIRMS detection and a strong station reading with **no imagery** | `insufficient_evidence`, 0 candidates, evaluation `no_inputs`; command exits 2 |

Reading the negative case is the point: the detector's errors are visible and named
(the haze distractor is a false positive; the cloud-masked cell is a miss), and the
supporting signals that might have "explained" either cell created nothing.

## 9. Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `HOTSPOT_SCAN_DIR` | `var/hotspots` | where scans are recorded and read from |
| `HOTSPOT_SMOKE_INDEX_THRESHOLD` | `0.55` | index at which a cell becomes a candidate |
| `HOTSPOT_STRONG_INDEX_THRESHOLD` | `0.75` | index that adds a confidence contribution (must exceed the trigger; the app refuses to start otherwise) |
| `HOTSPOT_MAX_CLOUD_FRACTION` | `0.35` | tiles above this cloud fraction are masked out |
| `HOTSPOT_IMAGERY_MAX_AGE_HOURS` | `6.0` | older imagery is not used |
| `HOTSPOT_SIGNAL_MAX_AGE_HOURS` | `6.0` | same for FIRMS and station support |
| `HOTSPOT_FIRE_SUPPORT_FRP_MW` | `1.0` | minimum FRP for FIRMS support (a triage choice, not a scientific threshold) |
| `HOTSPOT_STATION_SUPPORT_PM25_UGM3` | `60.0` | minimum verified station PM2.5 for support (a triage choice) |
| `HOTSPOT_MAX_FUTURE_SKEW_SECONDS` | `300` | tolerated clock skew into the future |
| `HOTSPOT_MAX_TILES` | `20000` | tiles per scan; more is refused |
| `HOTSPOT_MAX_CANDIDATES` | `500` | candidates per scan; excess dropped worst-first and recorded |

## 10. Limits, and what is not wired

- **No live imagery provider.** The detector consumes a declared index artifact;
  nothing in this repository downloads imagery, derives an index, or schedules a
  scan. The committed fixtures are the only callers.
- **No live FIRMS/station adapter.** `fire_signal_from_hotspot` maps the stored
  FIRMS rows, but no pipeline stage calls it yet, and no station repository is read
  here.
- **No review path, no notification, no alerting.** A candidate is recorded with
  `pending_human_review` and nothing else happens to it. There is no UI, no
  reviewer queue, and no integration with the incident workflow.
- **No attribution and no enforcement.** Nothing here identifies an industrial
  source, and nothing consumes a candidate as one.
- **The thresholds are triage choices.** They are documented as such and are not
  tuned against any real dataset.
- **The evaluation is against authored labels.** Precision and recall on these
  fixtures describe this detector on this fixture. They are not real-world
  performance, and no figure here may be quoted as such.
- **A high index is not a cause.** Cloud, haze, dust and biomass burning all raise
  an aerosol index; separating them needs a source-attribution analysis this
  detector does not perform.
