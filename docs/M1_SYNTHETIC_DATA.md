# M1 synthetic data and storage

M1 supplies an offline data foundation for the environmental feature work. It
does not train a model or claim that synthetic values are real observations.

## Generate and replay

Run from `backend/`:

```text
python -m app.cli demo-generate --profile tiny-ci --out work/tiny.json
python -m app.cli demo-generate --profile regional-demo --scenario rush_hour --replay-hour 12 --out work/rush.json
python -m app.cli demo-replay --profile regional-demo --at 2025-01-15T12:00:00Z --out work/replay.json
```

`--at` and `--replay-hour` advance the same injected scenario clock; they do
not rebuild a new random fixture. The exporter skips the write when the target
already contains identical bytes. Profiles use valid H3 resolution-8 cells and
the committed manifest counts: tiny (12 cells/6 stations), regional (256/32),
and seasonal smoke (256/32, with annual history metadata).

## Snapshot contents

Each snapshot includes PM2.5 station readings, weather sequences, population,
road and land-cover features, road-linked traffic, optional VIIRS-like fire
events, dataset references, and a deterministic `run_id`/`checksum_sha256`.
`dataset_version` and `ingestion_run` records make the same provenance
available to a PostgreSQL-backed pipeline. Both repositories use PostgreSQL
upserts keyed by the stable id, so replaying a run is idempotent.

The generated values are fictional and intentionally bounded for software
tests. They must be excluded from live training and model-promotion datasets;
M2 adds the feature builder and M3 adds explicit evaluation separation.
