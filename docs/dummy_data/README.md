# Deterministic dummy-data manifests

These manifests are the Milestone 0 contract for dummy data. The generator
and providers are added in Milestone 1. Until then they are executable design
fixtures: paths, counts, timestamps, scenario names and expected behavior are
stable inputs to implementation and review.

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
