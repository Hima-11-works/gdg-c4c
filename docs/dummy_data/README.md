# Deterministic dummy-data manifests

These manifests are the Milestone 0 contract for dummy data and the inputs to
the Milestone 1 generator in `backend/app/ingestion/demo_scenarios.py`. The
generator emits deterministic, offline snapshots for the same profiles without
network or database access. Paths, counts, timestamps, scenario names and
expected behavior remain stable inputs to implementation and review.

All profiles use `seed: 42`, UTC timestamps, fictional observations and the
`environmental-v1` feature schema. Synthetic observations must never enter a
live training export or a promoted model artifact.

Profiles:

- `tiny-ci.json`: fast provider and contract tests.
- `regional-demo.json`: coherent Delhi-NCR dashboard/mobile demo.
- `seasonal-training-smoke.json`: annual smoke test for feature export and
  training orchestration; generated artifacts stay outside Git.

The scenario names are intentionally shared across backend, web and Flutter.
Every consumer should key its cache and fixture selection by `scenario_id`,
`generator_version`, `seed`, and `anchor_utc`.

Generate a snapshot from the backend root with:

```text
python -m app.cli demo-generate --profile tiny-ci --out work/demo-snapshot.json
python -m app.cli demo-replay --profile regional-demo --at 2025-01-15T12:00:00Z --out work/regional.json
```

The JSON contains H3 cells, PM2.5 stations, weather, static population/road/
land-cover features, traffic segments, optional fire events, provenance, an
ingestion run id, and a SHA-256 checksum. Repeating a command with the same
profile, scenario, seed, anchor, and replay time produces byte-identical JSON.
