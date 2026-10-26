# Reviewer advisories

Reviewer-only draft generation is available at `POST /api/v1/advisories` and
`GET /api/v1/advisories/{bundle_id}`. Both require the configured
`X-Reviewer-Key`. If `REPORTS_REVIEWER_KEY` is unset, the routes are disabled
with `503`; an invalid key receives `403`.

The first supported source is a published v2 alert. The caller sends its stable
`alert_id`, never a PM2.5 value, location, source claim, or report narrative:

```json
{
  "source_type": "published_alert",
  "source_id": "v2:<run_id>:<h3_cell>:<forecast_minutes>",
  "consent": false,
  "reanalyze": false
}
```

With `consent: false` (the default), the server saves and returns a deterministic
template without contacting Gemini. With explicit consent, the server may send
only bounded alert/run facts and evidence IDs to the configured Gemini model;
it does not send citizen photos, report text, reporter identity, arbitrary
prompts, or storage URLs. Consent is recorded only when an outbound model
attempt is made.

Every response remains a draft for human review. It includes the originating
run, source-health records pinned to that run, versioned dataset references,
missing-evidence notes, and clear demo/synthetic/fallback/stale flags. It does
not confirm an event or source, issue a notice, diagnose health exposure, or
assign an official AQI/GRAP status. The AI-generated narrative is discarded in
favor of the deterministic template if output validation fails or the shared
Gemini budget/provider is unavailable. `provider_status` is `gemini`,
`template`, or `template_fallback`.

Successful and fallback bundles are saved under a content hash and reused for
identical evidence and generation settings. A database lease prevents duplicate
model calls across workers. `reanalyze: true` can replace a saved result after
the configured cooldown. The `gemini` provider budget is an application-level
ceiling of 2 requests/minute and 20/day shared by text and photo advisory calls;
the model project's active quota can be lower. The read route re-resolves the
alert before returning a saved draft and refuses it if the evidence context has
changed or disappeared. Responses use `Cache-Control: private, no-store`.

The web dashboard exposes the same workflow from warning/critical alert rows.
The hotspot panel exposes it only when the selected event's representative
cell has a matching published alert in the selected run. The reviewer key is
held only in the open panel's memory. Its consent checkbox is unchecked by
default. The returned narrative can be edited locally; edits are not sent back
to the API. Copy and Print/Save as PDF include the draft label, generation/data
mode, timestamps, facts, missing evidence, source references, and generated-text
evidence IDs. After a saved result is displayed, the panel does not offer a
regenerate action: reopening it reuses the server's saved result. A failed
request can be retried from the still-open panel.

Apply Alembic migrations through `0037_advisory_bundles` before enabling these
routes in a deployed API.
