# M5: FIRMS fire detections and sampled traffic

M5 adds retained source observations and quality accounting. It does **not**
connect either source to live prediction features. New inputs remain out of
model inference until an as-of historical join and an observed-label
incremental evaluation have been reviewed.

## NASA FIRMS

`FirmsProvider` fetches a bounded VIIRS near-real-time area CSV. NASA's Area API
requires a free MAP_KEY, takes `west,south,east,north` bounds and supports 1-5
UTC calendar-day windows; the default of 2 includes today and yesterday for a
recent-history buffer. Global VIIRS data are normally available within a few
hours. See
the official [Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) and
[API usage guide](https://firms.modaps.eosdis.nasa.gov/content/academy/data_api/firms_api_use.html).
The default source is `VIIRS_NOAA21_NRT`; NOAA-20 and S-NPP NRT sources can be
selected in configuration. The key is only read by the backend, placed in the
request path required by FIRMS, and never included in logs or error messages.

```powershell
# In the repository-root .env, set FIRMS_MAP_KEY to the key from NASA.
cd backend
python -m app.cli ingest-fires --days 2 --region delhi-ncr
```

Each detection retains acquisition/availability times, H3 cell, source and
processing version, satellite/instrument, confidence class/raw confidence,
FRP (MW), pixel size, brightness temperatures, day/night marker, dataset and
ingestion-run IDs. Stable content-derived IDs make repeated rolling-window
polls idempotent while a changed product version remains distinguishable.

Rows older than `FIRMS_STALE_AFTER_HOURS` are retained but marked stale. A
successful zero-detection result is recorded as known-empty only when the
request succeeds, the CSV has the required columns, and every returned row
passes validation. A timeout, rejected key, malformed row, out-of-area point,
or future timestamp is never converted into zero fires. Incomplete feeds can
retain their individually valid rows, but the run is marked failed/incomplete.
FIRMS hotspots are satellite thermal detections, not ground-confirmed fires;
confidence categories are kept categorical rather than represented as
probabilities.

## Optional sampled traffic import

There is no traffic vendor wired into the app. A live India traffic API can
carry licensing, quota, and cost constraints, so M5 accepts an operator- or
provider-prepared JSONL feed only after its source, attribution, and license
are supplied. This keeps the storage contract useful without silently
selecting a paid or non-redistributable provider.

```powershell
python -m app.cli ingest-traffic `
  --input sampled-traffic.jsonl `
  --source approved-provider `
  --product corridor-speed-samples `
  --version 2026-09 `
  --region delhi-ncr `
  --attribution "Provider name" `
  --license "Contract/license identifier"
```

Each line is one sample with `road_id`, configured-resolution `h3_cell`,
`observed_at`, `available_at`, `observed_speed_kph`,
`free_flow_speed_kph`, `sampled_road_coverage_fraction`, and optional
`confidence` in `[0,1]`. Imports are all-or-nothing for parse/quality errors;
empty feeds are rejected as unknown coverage. Historical rows are retained
and idempotent by source/dataset/road/time. The modeled field convention is
`observed_speed / free_flow_speed` (lower means slower); a measured standstill
is a valid zero. Stale samples, low corridor coverage, and missing confidence
are explicit quality flags. Sampled road coverage is never described as full
regional traffic coverage.

The `traffic_congestion_ratio` sign convention is corrected to match the
implementation plan (observed/free-flow, rather than its prior inverse), so
the shared feature schema is now `environmental-v2`; `environmental-v1`
artifacts are intentionally incompatible and must be retrained/evaluated.

## Incremental evaluation gate

After preparing a live, observed-PM2.5 training dataset with a correctly
time-joined fire or traffic feature group, compare the candidate and control
on identical deterministic temporal splits:

```powershell
python -m app.cli evaluate-feature-group `
  --dataset live-training.json `
  --group fires `
  --as-of-verified `
  --out fire-ablation.json
```

`--group` is `fires` or `traffic`. The explicit `--as-of-verified` assertion
means source availability was checked against every issue time; the command
also refuses demo/synthetic labels, reports candidate/control test metrics
and deltas, and does not promote either artifact. A source group is only
eligible for manual review when the candidate passes the existing evaluation
blockers and improves held-out MAE over the otherwise identical control.
Synthetic smoke tests are useful for validating parsing and storage, never
for establishing feature value.

M5 migrations add `fire_hotspot`, `traffic_observation`, and quality metrics
on `ingestion_run`. Query methods use `available_by` cutoffs so later
integrations can build leakage-safe historical feature joins.
