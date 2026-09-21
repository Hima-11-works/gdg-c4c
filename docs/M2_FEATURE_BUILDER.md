# M2 environmental feature builder

M2 turns the M1 observations into the typed, leakage-safe feature snapshots
used by later training and inference work. It does not fit a model and it does
not call live providers.

## Offline export

From `backend/`:

```text
python -m app.cli demo-features --profile tiny-ci --replay-hour 12 --history-hours 24 --out work/features.json
python -m app.cli demo-features --profile regional-demo --scenario monsoon_washout --replay-hour 12 --out work/monsoon-features.json
```

The command replays the same deterministic scenario for the requested history
window, then builds one `FeatureSnapshot` per H3 cell. Repeating the command
with the same inputs produces identical JSON and does not rewrite an unchanged
file.

## Feature semantics

`FeatureBuilder` applies an issue-time cutoff to station, weather, traffic, and
fire inputs. Forecast weather is usable only when its issue time is available
by the cutoff; future observations cannot leak into current or forecast
features. It derives PM2.5 lags/rolling summaries, rainfall windows and
time-since-rain, meteorological wind vectors, local-calendar sin/cos fields,
population/road/land-cover values, traffic congestion, and optional fire
summaries.

Missing inputs remain `null` and are listed in `quality.missing_fields`.
Observed zero rainfall, zero population, and zero traffic congestion are
valid values and are not rewritten as missing. A feature snapshot also carries
coverage, station support, freshness warnings, schema version, and dataset
references. `cell_feature_snapshot` stores that typed JSON projection under an
idempotent `(run_id, h3_cell, horizon_hours)` key.

`app/ingestion/static_features.py` is the offline import boundary for
preprocessed WorldPop/OSM/land-cover artifacts. It validates the typed static
rows and provenance metadata before the builder sees them; it intentionally
does not fetch large external rasters during a request.

The static values in M1 are fictional smoke-test data. Population is an input
to later exposure summaries, never a direct additive PM2.5 term.
