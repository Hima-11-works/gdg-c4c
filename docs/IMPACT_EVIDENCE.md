# Pilot impact evidence

Use the backend's aggregate report to show what the working prototype did
during a defined pilot window. Run it against the pilot database, with
`DEMO_MODE=false`:

```bash
cd backend
python -m app.cli impact-report --days 30 --out var/reports/impact-30d.json
```

The output reports geofence-verified citizen submissions and their review
outcomes, non-synthetic incident workflow counts and acknowledgment/resolution
times, and signed authority webhook receipts. It includes aggregate counts and
median durations only. It excludes report and incident identifiers,
coordinates, responder names, network prefixes, and free-text notes.

`evidence_scope` is `demo_only` when the application is running in demo mode;
otherwise it is `workflow_activity_only`. Workflow statistics are evidence of
recorded activity, not proof that the platform reduced pollution or improved
health. Do not turn them into causal or population-wide claims. Describe the
pilot window, covered jurisdictions, operational setup, exclusions, and data
source alongside any reported metric.

Measure forecast performance separately for every evaluation window with
`python -m app.cli corridor-evaluate --corridor delhi-kanpur --out
var/reports/corridor.json`. Only a sufficient evaluation against withheld,
real station observations is usable as model-quality evidence. Synthetic
federation results and insufficient-label corridor runs are useful demonstrations
of execution and data gaps, but are not accuracy claims. See
[`docs/api/corridor-evaluation.md`](api/corridor-evaluation.md) for label
provenance, minimum sample requirements, and limitations.

For a hackathon demo, show the actual workflow end to end: citizen report,
human review, hotspot or forecast context, authority handoff, and recorded
receipt. Clearly label seeded or synthetic records on screen. Pair the demo
with the generated aggregate report and one real-label corridor report if the
pilot data supports it; otherwise show the explicit insufficient-data result
and identify the next data collection step.
