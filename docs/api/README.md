# API v2 contract examples

[`v2-contract-examples.json`](v2-contract-examples.json) is the Milestone 0
wire contract for the environmental model. It contains `meta`, `current`, and
`forecast` envelopes. The later `/api/v2` routes must serialize these shapes;
the existing `/api/v1` routes remain unchanged during migration.

The v2 envelope makes five facts visible together: the published run, whether
the response is live/demo/mixed, data attribution, spatial coverage, and the
per-value model/feature quality. `is_demo` is retained for compatibility but
does not replace `mode`, `run_id`, or the dataset references.

The example intentionally has no calibrated interval for the demo forecast.
`lower_pm25` and `upper_pm25` are nullable until a model has passed held-out
interval calibration. Exposure is population-weighted concentration plus
threshold counts; it is not a medical dose estimate.
