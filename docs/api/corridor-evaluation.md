# Corridor / interstate pollution events, and how their forecasts are scored

This is the contract for a **named Indian corridor case** and the **honest
evaluation** of the forecast published for it.

The rule this document exists to enforce: **a forecast may only be reported as
accurate against real, withheld station observations. When those observations
are missing, the answer is an explicit insufficient-data result naming the gap —
never a synthetic number presented as real accuracy.**

---

## 1. The named case

| Field | Value |
| --- | --- |
| `corridor_id` | `delhi-kanpur` |
| Name | **Delhi–Kanpur interstate corridor** |
| Kind | `interstate` |
| Region | `india` |
| Endpoints | Delhi (28.6139, 77.2090) → Kanpur (26.4499, 80.3319) |
| Geometry | 24 H3 resolution-7 cells sampled along a **straight line** between those points |
| `geometry_source` | **`illustrative`** |

Why this case: the Indo-Gangetic plain stretch along this axis is where
stubble-burning smoke is transported north-east into the Delhi NCR airshed, and
where a winter inversion can trap it. It is the transport question the project
exists to address, and it is a real place with real stations — which is also why
the evaluation below is a real question rather than a formality.

### 1.1 The geometry is illustrative, and says so

The shipped corridor cells are a **straight-line sampling between two published
city coordinates**. They are **not** a road route and must not be read as the
NH-48/NH-44 alignment. Every response that describes the corridor repeats
`geometry_source: "illustrative"` and this note:

> ILLUSTRATIVE geometry: a straight-line Delhi–Kanpur axis, not a road route. It
> must not be read as the NH-48/NH-44 alignment. Replacing it requires a sourced
> route dataset (OSM, GraphHopper, or the Indian highways shapefile), after which
> `geometry_source` becomes `routed`.

`GeometrySource.ROUTED` exists in the domain so a sourced route can be
introduced without changing the contract — and consumers are expected to keep
treating the two differently until then.

---

## 2. The event

A **corridor event** is a published v2 run evaluated over the corridor's cells
at a set of horizons.

| Field | Meaning |
| --- | --- |
| `event_id` | `corridor:<corridor_id>:<run_id>:h<digest>` — a pure function of corridor, run, and horizon set |
| `run_id` | The published v2 run being evaluated |
| `run_mode` / `run_synthetic` | `live`/`demo` and whether any result row is synthetic |
| `issued_at` | The run's generation time — the forecast's issue time |
| `horizons[]` | Per horizon: `horizon_hours`, `issued_at`, `valid_at` |
| `cells[]` | The corridor cells, in order from the first endpoint to the last |
| `peak_predicted_ugm3` | Highest forecast PM2.5 in the corridor, and the horizon it occurred at |
| `label_count` / `label_sources` | How many station observations were found, and from where |

The id is stable: the same run at the same horizons always names the same event,
and a different run or a different horizon set is a different event.

### 2.1 Source times

| Time | What it is |
| --- | --- |
| `issued_at` (per horizon) | The forecast's issue time = the run's `generated_at` |
| `valid_at` (per horizon) | The hour the forecast predicts |
| Label window | `valid_at ± 1 hour` — the window a station reading must fall in to score that horizon |

A label necessarily **post-dates** the forecast's issue time, so the forecaster
could not have seen it. That is what "withheld" means here, and it is enforced
by construction: labels are only read from the target window, never from before
the run.

---

## 3. What is measured

Per **horizon** and per **geography**, always with the sample count attached:

| Metric | Definition |
| --- | --- |
| `mae_ugm3` | Mean absolute error of forecast − observed |
| `rmse_ugm3` | Root mean squared error |
| `bias_ugm3` | Mean error (forecast − observed). Positive = over-forecasting |
| `high_pollution_recall` | Of labels ≥ threshold, the fraction the forecast also put ≥ threshold |
| `high_pollution_precision` | Of forecasts ≥ threshold, the fraction that were |
| `coverage` | Corridor cells, cells with a forecast, cells with labels, cells scored, horizons scored, and both fractions |

- The threshold defaults to `ALERT_WARNING_THRESHOLD_UGM3` (90 µg/m³) and is
  overridable per request (`--threshold`).
- **Recall is `null`, not `0`, when no observation crossed the threshold.**
  Reporting 0% recall when there were no positives would be a fabricated claim.
- Geography slices: the whole corridor, plus `delhi_end`, `midway`, and
  `kanpur_end`.
- Every slice carries `sufficient`. A slice with fewer than `--min-labels`
  (default 5) scored pairs reports `sufficient: false` with its metrics **null**,
  so a number computed from three points can never be quoted.

### 3.1 When the result is not an evaluation

`verdict` is one of two values:

| Verdict | Meaning |
| --- | --- |
| `evaluated` | Every requested horizon × geography slice had enough **observed** labels |
| `insufficient_data` | Anything else — and `reasons[]` says exactly what |

`insufficient_data` is returned when:

1. the run is synthetic/demo (`run_synthetic`), because its error and recall are
   properties of the scenario, not of the world;
2. **no** station observation fell in any target window (with the count of
   candidate readings checked);
3. any requested slice is below the minimum label count;
4. `--require-unused-stations` was requested. That mode would need a run to
   record which stations influenced which cell, which it does not; it is reported
   as unavailable rather than approximated.

Sources listed in `SYNTHETIC_LABEL_SOURCES` (default `scenario,demo,demo-scenario`)
are counted as *available* but never scored, so a synthetic station cannot
contribute a metric.

Only `verdict: "evaluated"` sets `usable_as_real_world_evidence: true`.

---

## 4. The command

```bash
cd backend

# Score the newest published run for the corridor
python -m app.cli corridor-evaluate --corridor delhi-kanpur

# A specific run and horizon set, with the JSON report written out
python -m app.cli corridor-evaluate \
  --corridor delhi-kanpur \
  --run-id prediction-features-20260924T0600Z \
  --horizons 1,3,6 \
  --min-labels 5 \
  --threshold 90 \
  --out var/reports/corridor-delhi-kanpur.json
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Evaluated against enough real labels; the metrics are quotable |
| `1` | Could not evaluate at all (unknown corridor, no published run, unreachable database) |
| `2` | **The event exists but the real labels do not** — an insufficient-data result, printed in full |

Output shape (from the tests, and identical in shape for a real run):

```
Corridor: Delhi–Kanpur interstate corridor (delhi-kanpur)
  geometry: illustrative — ILLUSTRATIVE geometry: a straight-line Delhi–Kanpur axis, …
  event: corridor:delhi-kanpur:prediction-features-20260924T0600Z:h1a2b3c4d
  run: prediction-features-20260924T0600Z (mode=live, synthetic=False)
  issued_at=2026-09-24T06:00:00+00:00 horizons=[1.0, 3.0, 6.0] peak=144.0 µg/m³ at +3h
  verdict: insufficient_data (usable as real-world evidence: False)
    - 24 of 72 horizon x geography slice(s) are below the 5-label minimum (e.g. delhi_end at +1h: 0 scored pair(s), no station observation in this slice's target window)
    - no pm2.5 station observation fell in the target window of any of the 3 horizon(s) for the 24 corridor cell(s) (0 candidate reading(s) checked)
  labels: 0 observation(s) from no source
  coverage:
    corridor_cells=24
    cells_with_forecast=24
    cells_with_labels=0
    cells_scored=0
    requested_horizons=3
    horizons_scored=0
    fraction_cells_scored=0.0
    fraction_horizons_scored=0.0
  metrics: none — no slice had enough real labels
```

---

## 5. The API

### 5.1 The catalog

```bash
curl localhost:8000/api/v1/corridors
```

```json
{"generated_at":"…","is_demo":false,"data":[{
  "corridor_id":"delhi-kanpur",
  "name":"Delhi–Kanpur interstate corridor",
  "kind":"interstate",
  "region":"india",
  "geometry_source":"illustrative",
  "geometry_note":"ILLUSTRATIVE geometry: a straight-line Delhi–Kanpur axis, not a road route. …",
  "geometry_description":"24 H3 resolution-7 cells sampled along a straight line between Delhi (28.6139, 77.2090), Kanpur (26.4499, 80.3319). ILLUSTRATIVE geometry: …",
  "h3_resolution":7,
  "cell_count":24,
  "endpoints":[{"label":"Delhi","latitude":28.6139,"longitude":77.2090},
               {"label":"Kanpur","latitude":26.4499,"longitude":80.3319}],
  "notes":"The Indo-Gangetic plain stretch where stubble-burning smoke is transported …"
}]}
```

### 5.2 The event and its evidence

```bash
curl "localhost:8000/api/v1/corridors/delhi-kanpur/events/corridor:delhi-kanpur:prediction-features-20260924T0600Z:h1a2b3c4d?min_labels=5"
```

```json
{"generated_at":"…","is_demo":true,"data":{
  "event":{
    "event_id":"corridor:delhi-kanpur:prediction-features-20260924T0600Z:h1a2b3c4d",
    "corridor_id":"delhi-kanpur",
    "corridor_name":"Delhi–Kanpur interstate corridor",
    "run_id":"prediction-features-20260924T0600Z",
    "run_mode":"live",
    "run_synthetic":false,
    "issued_at":"2026-09-24T06:00:00Z",
    "horizons":[
      {"horizon_hours":1.0,"issued_at":"2026-09-24T06:00:00Z","valid_at":"2026-09-24T07:00:00Z"},
      {"horizon_hours":3.0,"issued_at":"2026-09-24T06:00:00Z","valid_at":"2026-09-24T09:00:00Z"},
      {"horizon_hours":6.0,"issued_at":"2026-09-24T06:00:00Z","valid_at":"2026-09-24T12:00:00Z"}
    ],
    "cell_count":24,
    "cells":["…"],
    "peak_predicted_ugm3":144.0,
    "peak_horizon_hours":3.0,
    "label_count":0,
    "label_sources":[]
  },
  "evaluation":{
    "verdict":"insufficient_data",
    "usable_as_real_world_evidence":false,
    "label_provenance":"none",
    "reasons":[
      "no pm2.5 station observation fell in the target window of any of the 3 horizon(s) for the 24 corridor cell(s) (0 candidate reading(s) checked)",
      "24 of 72 horizon x geography slice(s) are below the 5-label minimum (e.g. delhi_end at +1h: no station observation in this slice's target window)"
    ],
    "min_labels":5,
    "high_pollution_threshold_ugm3":90.0,
    "coverage":{"corridor_cells":24,"cells_with_forecast":24,"cells_with_labels":0,
                "cells_scored":0,"requested_horizons":3,"horizons_scored":0,
                "fraction_cells_scored":0.0,"fraction_horizons_scored":0.0},
    "slices":[{"horizon_hours":1.0,"geography":"corridor","pairs":0,"station_count":0,
               "mae_ugm3":null,"rmse_ugm3":null,"bias_ugm3":null,
               "high_pollution_threshold_ugm3":90.0,"high_pollution_observed":0,
               "high_pollution_recall":null,"high_pollution_precision":null,
               "sufficient":false,
               "note":"no station observation in this slice's target window"}],
    "evidence":[{"horizon_hours":1.0,"geography":"corridor","pairs":0,
                 "station_count":0,"sufficient":false,
                 "note":"no station observation in this slice's target window"}]
  }}}
```

Note `is_demo: true` on the envelope: with no real evaluation there is nothing
to assert, and the envelope says so.

Query parameters: `run_id`, `min_labels`, `high_pollution_threshold_ugm3`,
`require_unused_stations`.

---

## 6. The insufficient-data fixture

`backend/tests/fixtures/corridors/insufficient_labels.json` records the shipped
live deployment's actual case — a live run with **no** station readings in the
corridor's forecast windows:

```json
{
  "corridor_id": "delhi-kanpur",
  "run": {"run_id": "prediction-features-20260924T0600Z", "mode": "live", "corridor_cells": 24},
  "labels": {
    "observations_in_corridor_windows": 0,
    "candidate_readings_checked": 0,
    "nearest_station_distance_km": 61.4
  },
  "expected": {"verdict": "insufficient_data", "usable_as_real_world_evidence": false, "exit_code": 2}
}
```

`tests/test_corridor_evaluation.py::test_insufficient_data_fixture_is_honoured`
asserts the service returns that verdict, with both reasons, with **every** metric
`null`, and with coverage quantified (`cells_scored: 0`).

---

## 7. The exact data gap

Right now, the gap is **labels, not code**:

| Missing | What it blocks | How to close it |
| --- | --- | --- |
| **PM2.5 station readings inside the corridor cells** (24 cells on a Delhi–Kanpur axis) | Every metric. The nearest station that could serve this axis is ~61 km away at the configured H3 resolution, so no observation lands in a target window | Ingest a station network that covers the axis, or widen `LABEL_WINDOW`/corridor definition with a documented justification. Do **not** substitute the scenario's stations |
| **A run recording which stations influenced which cell** | `--require-unused-stations` (a strict input holdout, as opposed to the time holdout used now) | Persist per-cell station influence (or the interpolation weights) with the run |

Until the first is closed, the correct statement about this corridor is the
insufficient-data result above — **not** a number.

---

## 8. Deliberately out of scope

- **No claimed accuracy.** No metric in this document is a real-world
  measurement, because no real labels were available for this corridor. The
  metric arithmetic is unit-tested with hand-made numbers and clearly labelled as
  such in the test module.
- **No sourced route geometry.** The corridor is a straight-line sampling until
  a route dataset is supplied.
- **No per-station label publication.** The API returns aggregates and counts,
  not the individual station readings it scored.
- **No retrospective re-scoring of demo runs.** A demo run's own accuracy is a
  property of the scenario, so it is refused rather than reported.
