# Web dashboard demo: one published run, two measures

This describes the dashboard's published-run demo: what it shows, where each
number comes from, and how to reproduce the states it has to handle honestly
(a run with population, a run without it, and a stale run).

## Running it

```bash
# backend + database
docker compose up -d

# frontend (expects the API on http://localhost:8000)
cd frontend && npm install && npm run dev
```

The dashboard reads `GET /api/v2/*` only. Every map, timeline and drawer read
is pinned to **one published run**: `lib/api.ts` resolves the run id once from
`GET /api/v2/meta` (`latest_run_id`), caches it for five minutes, and sends it
as `run_id=` on every subsequent read. The current-conditions grid, the
forecast frames, the weather, the alerts and the cell drawer therefore all
describe the same publication — the status panel and the map cannot disagree
about which run is on screen.

## What the dashboard states about the run

The **Published run** panel (top right, collapsible) reports five facts, all
read off the same response the map is drawn from — no client-side inference:

| Fact | Field |
| --- | --- |
| Run time | envelope `generated_at` (absolute + relative) |
| Demo / live status | envelope `mode`, plus `is_demo` for the demo warning |
| Source age | `metadata.quality.max_observation_age_hours` (oldest across cells) |
| Coverage | envelope `coverage` (covered fraction, returned/requested cells, resolution) |
| Prediction method | `metadata.prediction_method` (+ `model_version`, `input_kind`) |

It also lists how many cells carry a population-weighted value, the residents
covered, and the datasets the backend attributed the run to.

`lib/runFacts.ts` derives all of this in one pure function so the panel, the
empty states and the banners can't drift apart.

## The two measures

The **Measure** switcher (bottom-left panel) selects what each cell is coloured
by. Both come from the same run and the same cells; only the value changes.

- **PM2.5** — the area-averaged concentration (`GridCurrentV2Out.pm25` /
  `ForecastV2Out.predicted_pm25`), on the CPCB ramp.
- **Population-weighted PM2.5** — the backend's exposure field
  (`ExposureOut.population_weighted_pm25`), on its own cool cyan → violet ramp
  over the same µg/m³ bands. A crowded cell dominates; an empty one counts for
  nothing. It is a population-weighted *concentration*, not a medical dose
  estimate.

The two layers deliberately use different colour families, so a layer that is
not the plain area-average can never be mistaken for it. Because the value is
still a concentration, the band boundaries are identical — the legend says so.

The measure applies to every rendering path: hex cells, the smooth raster, and
the contrast-mode range contours. Forecast playback switches the value too, so
an exposure animation is the same population weighting applied to each
published forecast anchor.

## Empty states

Both are shown next to the map, not swallowed into a colour — a blank exposure
layer with no explanation would read as "no pollution here", which is the one
thing it must never mean.

- **No population to weight by.** Shown in exposure mode when no cell in the
  read carries a population-weighted value. The message distinguishes the two
  causes: the run has no population estimates at all, or population is known
  but no cell in this frame has both a population and a prediction.
- **Stale data.** Shown when the run's `generated_at` is more than 24h old, or
  when the newest observation behind it is more than 24h old (either alone is
  enough — they fail separately). The notice names the age rather than only
  asserting staleness, and the status panel carries a matching badge.

Thresholds live in `lib/format.ts` (`STALE_RUN_HOURS`, `STALE_SOURCE_HOURS`)
and are stated in the UI next to the numbers they qualify.

## Reproducing the states

The dashboard shows whatever the latest published run is. The three states can
be produced with the backend CLI (these write to the database, not the repo):

```bash
# 1. A population-backed demo run (the manifest's regional-demo scenario)
docker exec gdg-c4c-api-1 sh -c "python -m app.cli demo-features \
  --profile regional-demo --replay-hour 24 --history-hours 6 \
  --out /tmp/demo-features.json"

docker exec gdg-c4c-api-1 sh -c "python -m app.cli prediction-publish \
  --input /tmp/demo-features.json --feature-run-id demo-pop \
  --run-id demo-population-1 --mode demo --region india \
  --scenario-id winter_stagnation --generated-at 2026-01-01T00:00:00Z"

# 2. A stale run: the same features published with an old generated_at
#    (--generated-at 30 days ago), then a fresh run published after it.

# 3. A missing-population run: any run id the backend serves from its
#    no-database fallback, e.g. demo-fallback-<YYYYMMDDTHH>Z
```

To view a specific run without republishing, point the app at it by pinning
the run id — the API accepts `run_id=` on every v2 read, and the frontend takes
its run id from `/api/v2/meta`, so a request rewrite of `latest_run_id` is
enough (this is how the missing-population and stale states were verified
against real runs rather than mocked data).

## Honest limitations

- **The demo run is regional.** `regional-demo` is a Delhi-NCR scenario (256
  H3 res-8 cells), published under `region: india`. At the country tier it
  covers one H3 res-3 cell, so the nationwide view shows a small cluster over
  Delhi and nothing elsewhere. That is the run's real coverage, and the panel's
  coverage figure says so.
- **Source age often reads "Not reported".** The published demo features carry
  `max_observation_age_hours: 0.0`, and the backend's aggregation maps a zero
  age to `null`. The dashboard shows "Not reported" rather than claiming
  freshness it wasn't told about.
- **Exposure is per-cell, not a summary.** The map colours each cell by its own
  `population_weighted_pm25`. The run-wide aggregate lives on
  `GET /api/v2/exposure`, which this dashboard does not currently call.
- **The population figure is only as good as the run.** `covered_population`
  and `population_dataset_version` are the backend's; the frontend never
  estimates a population.
