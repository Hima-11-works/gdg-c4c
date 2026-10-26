# Fast implementation plan for STRATEGY.md

Prepared: 26 October 2026. Code reviewed: `beta`, `cad3780`.
Status: implementation in progress; S00 documentation corrections and S01–S05 are pushed. S06 is in progress; backend/browser runtime checks remain pending where the local environment has no service access.

## Execution notes

- **S00 (26 October 2026):** Rewrote `STRATEGY.md` to match the implemented prototype and scoped demo, removing unsupported impact, enforcement, federation-privacy, fixed-quota, and satellite-timing claims. `docs/PITCH_DECK.md` already describes these limitations and was left unchanged. Preflight found Docker Desktop unavailable, local API requests blocked in this environment, and no `GEMINI_API_KEY` in the local `.env`; therefore no running case was opened and a Gemini smoke call could not be made. Continue only with clearly labeled demo/fallback paths until that preflight succeeds.
- **S01 (26 October 2026):** The cutover gate now blocks on absent observations/lag and unknown publication, scheduler, quota, retention, scenario, or rollback evidence. Collector health ages at read time while published-run health stays unchanged; skipped sources have no fetch timestamp, and the latest health run is selected by write order. FIRMS layer text now distinguishes returned detections, successful empty results, stale/failed checks, and a source that was not queried. Station freshness counts unique stations and lag uses only measured timestamp pairs, with the worst paired lag checked against the target. Verification: 19 focused backend tests passed, frontend production build passed, and changed-file Ruff checks passed with existing line-length/f-string violations excluded. The local browser/API path could not run because Docker is unavailable and localhost access is blocked here.
- **S02 (26 October 2026):** Added a bounded server-owned context for published alerts and a deterministic review draft. It resolves measurements from the alert's own run, uses only that run's pinned source-health rows and dataset references, and marks demo, synthetic, fallback, and stale inputs in the copy-ready plain text. Missing input quality, observation age, source checks, and dataset references are explicit; the draft does not claim event confirmation, source attribution, AQI, or GRAP status. Scope note: incident/report evidence joins are deferred until the public alert path and authorization/consent behavior are in place. Verification: 5 focused advisory tests passed, Ruff and compile checks passed. The existing alert-route TestClient case stalls when it tries to open its local socket in this sandbox; direct service resolution is exercised by the advisory tests.
- **S03 (26 October 2026):** Added reviewer-key-protected advisory generate/read endpoints; explicit consent gates server-side structured Gemini text, with a deterministic, clearly labeled template fallback for missing credentials, exhausted shared budget, provider errors, or unsafe output. The prompt is an allow-listed, bounded projection; generated evidence IDs must belong to the resolved alert context, and numbers, AQI/GRAP/stage and legal/enforcement language are rejected. Saved bundles carry run/context hashes, model and prompt/schema provenance, and a two-minute cross-worker generation lease. Gemini's application ceiling is shared with photo assessment at 2 requests/minute and 20/day. Verification: 43 focused advisory/Gemini/photo tests passed, 9 socket-bound route tests were deselected; Ruff, compileall, OpenAPI route registration, `git diff --check`, and offline Alembic SQL generation passed. No DB was available to apply migration 0037 and no live Gemini key was configured, so neither a database-backed request nor provider smoke call was run. Docker/localhost restrictions continue to block the browser/API path.
- **S04 (26 October 2026):** Added a lazy-loaded reviewer brief from warning/critical published alerts and from a hotspot only when its representative cell has a matching alert in the selected run. The panel shows the saved provider status, forecast/data provenance, facts, source references, missing evidence, and an editable local narrative; copy and PDF/print preserve source IDs, dates, and labels. Consent remains off by default and the reviewer key is held only in panel memory. A successful saved draft is not presented as a regenerate action; failed requests can be retried, while reopening reuses the backend's saved result. Verification: TypeScript/Vite production build and targeted Prettier passed; oxlint exited 0 with three existing warnings in `CellSatelliteContextPanel.tsx` and `PhotoReviewPanel.tsx`; changed-file `git diff --check` passed; the manual design detector returned no findings. The advisory chunk is lazy-loaded (8.89 kB, 2.98 kB gzip). Browser/print-preview verification was attempted, but this environment could not connect to Vite at localhost:5174. API/DB/Gemini paths remain unverified here for the S03 reasons above.
- **S05 (26 October 2026):** Added deterministic English, Hindi, and Punjabi community text using only the saved alert context, with severity, forecast time/value, model uncertainty, data mode, and synthetic/fallback/stale markers. Hindi and Punjabi are labeled unreviewed and are not represented as fluent-speaker-approved. Copy includes the source/run IDs and evidence references; print includes the selected language. Added explicit-click local speech preview, asynchronous voice-list updates, exact language matching, Stop, and cancellation on language change or panel close. Verification: frontend build and targeted Prettier passed; oxlint exited 0 with the same three existing warnings; `git diff --check` passed; an ad hoc Node harness with a mocked speech engine checked late voice updates, Hindi/Punjabi voice matching, rejection of an English voice for Punjabi, start/stop callbacks, and context-bound text in all three languages. Browser/device voice playback and fluent-speaker review could not be performed in this environment; local browser access is blocked as recorded for S04.
- **S06 (26 October 2026):** Added a forecast briefing with the selected run, forecast horizon and valid times, generation time, method/input kind, displayed wind sample and timing, plus the method assumptions already supplied by the backend. The forecast now explicitly says it is not source attribution or a validated plume-arrival estimate. The alert and cell views retain `Official GRAP stage not assessed`; a versioned NCR-only reference names CAQM Direction No. 104 (29 September 2026), review date, source link, and limits without encoding actions. Current official CAQM listings were checked; the complete linked schedule was not retrieved, so no stage or action list is embedded. Verification: TypeScript/Vite production build and targeted Prettier passed; oxlint exited 0 with the same three existing warnings; changed-file `git diff --check` passed. The design detector's side-border warning was removed by replacing the thick accent with a subtle outline. Browser/API checks remain unavailable here; the S04 localhost browser attempt timed out.
## 1. Feasibility verdict

The three proposed extensions are feasible as a reviewer-facing hackathon demo after narrowing their claims:

1. Generate an evidence-backed **draft inspection brief**.
2. Produce **English, Hindi, and Punjabi community advisory text**, with audio preview when a matching browser voice exists.
3. Explain a published pollution forecast and display **versioned GRAP reference information**.

A first useful AI brief can reasonably take **4–5 engineering hours**. Budget **8–12 hours for the complete scoped demo**, including targeted tests, browser checks, and a rehearsal, assuming the local stack and Gemini account already work. These are planning estimates for one developer, not measured completion times. Account access, missing observations, and provider outages can extend elapsed time.

The strategy's 30–50 minutes per feature is plausible only for isolated UI sketches after shared infrastructure is finished. It does not cover trustworthy evidence joins, authorization, provider budgets, saved output, failure handling, or verification. A production enforcement platform, validated predictive model, or operational interstate deployment is outside this sprint.

## 2. Changes required to the strategy

| Strategy proposal or claim | Finding | Implementable scope |
| --- | --- | --- |
| Official SDM show-cause notice citing Sections 19/31A | Existing incident records do not establish an officer's statutory delegation or a legally sufficient case. Section headings are not a substitute for an approved notice template. | A clearly marked draft inspection brief, with source references and unanswered questions. Keep statutory citations and legal orders out until an authorized reviewer supplies an approved template. |
| Automatically trigger GRAP I–IV from a cell's PM2.5 forecast | GRAP concerns Delhi/NCR AQI and authority decisions. A forecast concentration in µg/m³ is not the same quantity as city AQI. The existing cell panel already explains this limitation. | Forecast briefing plus official policy reference links. Keep official stage `not_assessed` unless independently verified authority-order information is available. |
| Free Gemini API with guaranteed 15 RPM | Google's quotas depend on model, project, and usage tier; capacity is not guaranteed. Pricing currently lists free text tiers for the repository's configurable Flash model family. | Verify the chosen model in the actual project. Add a conservative application budget, caching, and a template fallback; do not hardcode a universal provider entitlement. |
| Punjabi/Haryanvi speech in every browser | `getVoices()` returns voices available on the current device. A language tag does not install a voice. Playback also does not deliver anything to a village. | English/Hindi/Punjabi text; exact-language voice detection and local audio preview. Defer Haryanvi until translation and voice quality can be reviewed. |
| Photo verification, source attribution, and plume angle | `gemini_assessment.py` deliberately provides image-visible advisory observations and prohibits inventing source, location, AQI, or confirmation. It has no plume-angle output. | Reuse the saved visual assessment with its uncertainty and human review state. Do not add scientific plume geometry to this sprint. |
| Legally defensible calibrated uncertainty | Publication bounds are optional and depend on a selected model artifact. `citizen_calibration.py` currently applies a slope/intercept, not a demonstrated device-specific humidity calibration. | Preserve existing uncertainty fields and describe what they measure. Make no regulatory calibration claim without independent validation. |
| Federated learning solves interstate privacy/legal resistance | Existing services explicitly say privacy and real agency participation are not established. | Demonstrate parameter exchange using the existing federation workflow, with its recorded limitations. |
| Nationwide reach, guaranteed four-hour plume arrival, exact evening burning peak, 50 million protected | These outcomes are not established by the reviewed code or the strategy's uncited narrative. | Use one coherent pilot case. Show measured source times, available evidence, modeled forecasts, and missing inputs. Treat those larger claims as hypotheses requiring evidence. |

CAQM's current site lists a September 2026 GRAP revision. The implementation must verify that schedule before reproducing any action list. The strategy's examples are not a maintained policy source. The retrieved 21 October 2026 official release illustrates the use of Delhi AQI, meteorological forecasts, and a CAQM Sub-Committee decision; it is not proof of the stage currently in force on every later date. See sources below.

## 3. Reuse and missing integration

| Existing component | Reuse | Work still required |
| --- | --- | --- |
| `backend/app/services/published_alerts.py` | Resolve a stable alert ID into a specific run, cell, horizon, and values. | Build a bounded context object for AI text. Accept IDs rather than client-supplied pollution measurements. |
| `backend/app/services/incidents.py` | Resolve incident source references, report links, mode, eligibility, and jurisdiction. | Join the available evidence into the same context contract. A complete AI-ready evidence bundle is not already exposed. |
| `backend/app/services/gemini_assessment.py` | Existing SDK, schema validation, sanitized errors, consent, and saved image assessment. | Add a separate text-generation adapter. The image-only prompt and consent cannot silently cover a richer incident payload. |
| `backend/app/services/provider_budget.py` | PostgreSQL quota reservations and usage accounting. | Wire Gemini calls into it: the `gemini` policy entry exists, but the reviewed photo analyzer calls the SDK directly without that budget reservation. |
| `frontend/src/components/AlertsPanel.tsx` | Existing alert list and selected-alert interaction. | Add a draft-advisory action and shared result panel. Its suggested-action checkboxes currently do not dispatch work. |
| `frontend/src/components/HotspotEvidencePanel.tsx` | Existing hotspot inspection and evidence presentation. | Reuse the same panel for eligible incident/evidence sources after the alert path works. |
| `frontend/src/components/CellDetailPanel.tsx` | Existing GRAP limitation notice. | Add sourced reference information while retaining the distinction between a cell forecast and an official stage. |
| Existing incident simulator and webhook code | Demonstrate persisted state transitions and separately reported webhook status. | Use the simulator for the first demo. Real delivery requires a configured and verified recipient. |

Avoid a new agent framework, vector database, paid map service, separate PDF service, new cloud migration, or mobile feature work. FastAPI, the existing Google SDK, PostgreSQL, React, and browser printing are enough.

## 4. Minimal shared design

Use one server-owned `AdvisoryContext` and one `AdvisoryBundle` across the three extensions.

**Context:** source type and ID; prediction run and horizon; event/review state; location at the least precision needed; PM2.5 values and units; acquisition/observation/valid times; source references; optional saved visual assessment; freshness, coverage, and unavailable reasons; `live`/`demo` mode. Each fact gets an evidence ID. Bound the number of records and text length.

**Bundle:** short inspection summary, inspection checklist, missing evidence, community advisory text for `en`, `hi`, and `pa`, and the evidence IDs supporting each section. Policy references and numerical fact tables remain server-owned. Gemini may explain the supplied evidence but cannot set severity, confirm a source, change measurements, create legal citations, assign a GRAP stage, or issue an incident transition.

Persist generation provenance: context hash, source versions, model ID, prompt/schema versions, generation time, language, and whether the output came from Gemini or a deterministic template. Cache by the context and generation configuration. Recheck source availability and authorization before returning a saved result; a withdrawn report, removed image, or changed review decision must not leave a valid-looking cached brief.

Proposed API: reviewer-authorized `POST /api/v1/advisories` to generate or reuse a bundle, and a protected read by saved bundle ID. Resolve source-specific eligibility on the server. Apply existing actor/jurisdiction checks when an incident source requires them; an incident actor credential alone must not grant access to private photo derivatives. Do not add a second user/account system.

For the first version, send redacted structured facts rather than raw photos, reporter identity, arbitrary notes, storage URLs, or arbitrary user prompts. Obtain explicit reviewer consent for these new provider inputs. The existing photo flow remains separate. Google lists product-improvement use for free-tier content, so use public or consented, non-sensitive demonstration evidence.

## 5. Ordered implementation steps

Make one reviewable commit per completed step. Verify each step before starting the next. Do not combine feature work with a broad backend rewrite.

| Step | Deliverable | Estimate | Depends on |
| --- | --- | --- | --- |
| S00 | Correct demo claims and establish a working baseline | 30 min | — |
| S01 | Repair essential source/readiness reporting before claiming live operation | 60–90 min | S00 |
| S02 | Server-owned advisory context and deterministic template | 60 min | S00 |
| S03 | Bounded Gemini generation and saved output | 90–120 min | S02 |
| S04 | Inspection-brief UI and print/copy export | 45–60 min | S03 |
| S05 | Community text and browser audio preview | 60–90 min | S04 |
| S06 | Forecast explanation and GRAP reference card | 45–60 min | S03, S04 |
| S07 | End-to-end verification and three-minute demo rehearsal | 60 min | S01–S06 for live claims |

Allow roughly 20% contingency. S01 is required before a live-data claim, but a clearly labeled demo can reach S02–S04 without waiting for new provider observations. Source availability remains an external dependency.

### S00 — Baseline and scope agreement

- Check API/database readiness, migrations, `/api/v2/meta`, source ages, and the scheduler status. The previous audit observed a demo fallback and no initialized collector jobs; recheck rather than assuming those runtime observations still hold.
- Configure the existing `GEMINI_API_KEY` and `GEMINI_MODEL` server-side. Verify one small structured response with the actual project; check its current free-tier quota. Do not copy the strategy's older model list into code.
- Choose one pilot incident or published alert for the entire story. If real inputs are absent, create a reproducible explicitly labeled demo case. Do not use live fallback rows as evidence of an actual event.
- Keep the existing cloud deployment for the sprint. Do not make Cloud Run setup a prerequisite; an always-free hosting guarantee has not been established.

**Complete when:** the chosen case opens, its mode is visible, one Gemini call works or the key dependency is explicitly recorded, and the revised demo wording is documented in `STRATEGY.md`/the pitch script.

### S01 — Essential truthfulness fixes

Targets: `backend/app/services/shadow_run.py`, `backend/app/cli.py`, `backend/app/db/repositories/source_health.py`, `backend/app/api/routes/fires.py`, and the relevant web source-status display.

- Make readiness fail or report unknown when there are no observations, no measured lag, missing live publications, or unverified operational checks. Require evidence for the cutover gate's currently optimistic defaults.
- Age current collector health at read time. Preserve the historical status recorded for a publication's original input run separately.
- Distinguish a successful empty FIRMS collection from an unconfigured, failed, or stale collector. Display that distinction beside the layer.
- Use observation age and the selected run's input-quality fields in advisory eligibility, not merely the time the user opened the page.

**Verify:** empty database cannot pass live cutover; an old `present` collector record becomes stale; an unconfigured FIRMS collector does not claim a checked empty window. Run focused regression tests for these cases.

### S02 — Context and template first

Proposed files: `backend/app/domain/advisories.py`, `backend/app/services/advisory_context.py`, and `backend/app/services/advisories.py`.

- Implement published-alert resolution first and reuse existing resolvers and repositories. Defer incident/report source resolution until an explicit, safe reviewer authorization and consent path is available.
- Add deterministic fact, missing-evidence, and draft-inspection sections. Render numerical values and timestamps from this structure.
- Carry demo/fallback/stale markers into every draft and export. An unreviewed report can produce a triage draft but cannot be described as confirmed.
- Only include evidence that actually exists; older satellite observations are dated context, not independent confirmation of a new evening fire.

**Verify:** client-supplied values cannot override stored facts; unavailable evidence remains absent; sources from different runs are not accidentally combined; draft labels survive copying and printing.

### S03 — One Gemini integration for all text

Proposed files: `backend/app/services/gemini_advisories.py`, `backend/app/api/schemas_advisories.py`, `backend/app/api/routes/advisories.py`, a repository, and the next Alembic revision.

- Add strict structured output with bounded summary/checklist/community-text fields and evidence-reference validation.
- Treat report text and saved AI assessments as untrusted content. Reject references outside the supplied context and any generated statutory citation or stage assignment. Render plain text in React.
- Use the shared PostgreSQL budget before each outbound attempt, including existing photo generation. A policy constant alone is insufficient. Coalesce duplicate generation across workers.
- Start with conservative configurable limits such as 2 requests/minute and 20/day across this deployment, reduced further if the project's actual quota is lower. Account for input size and token allowance as well as request count. Avoid automatic provider/model failover that could incur charges.
- Use one provider attempt and a bounded timeout, initially around 20–25 seconds, within the API's 60-second limit. Store and reuse successful bundles; apply cooldowns to regeneration. Size the text output limit separately from the photo service's current 512-token default.
- On missing key, rejected schema, timeout, or exhausted budget, return the deterministic template with a clear provider status. Do not label it AI-generated.

**Verify:** authorization/consent failures, missing sources, repeated clicks, concurrent identical requests, malformed JSON, invented evidence IDs, injection-like notes, 429 responses, and API timeout. With a configured key, make one controlled smoke call using a public/demo context.

### S04 — Draft inspection brief

Proposed files: `frontend/src/components/AdvisoryPanel.tsx`, API/types additions, and entry points in `AlertsPanel.tsx` and `HotspotEvidencePanel.tsx`.

- Add `Generate draft brief`, a loading state, provider/fallback status, and retry only when appropriate.
- Show facts, source dates, missing evidence, and a short editable narrative. Preserve the generated original separately from any local edits.
- Add Copy and browser Print/Save as PDF. Print styles keep the draft label, mode, evidence references, and timestamps.
- Leave recipient/signature details for manual completion. Keep generated drafts separate from incident assignment and delivery controls.

**Verify in browser:** a successful request, unavailable provider, source switched during generation, stale/demo case, repeat click, keyboard access, narrow viewport, and a readable print preview.

### S05 — Community advisory and audio preview

Targets: shared advisory bundle/panel plus a small `frontend/src/lib/speech.ts` helper.

- Use the same grounded bundle for short English, Hindi, and Punjabi text. Keep a deterministic translated template fallback, reviewed by a fluent speaker before claiming translation quality.
- Preserve warning level, time window, source uncertainty, and factual placeholders across languages. Do not invent CRM machinery availability, phone numbers, official orders, or local medical services.
- Detect `speechSynthesis` and available voices, including asynchronous `voiceschanged`. Select a matching `hi`/`pa` voice where present; otherwise keep text/copy available and explain the missing voice. Never silently read Punjabi text with an English voice.
- Label the action `Play advisory preview`; playback starts only after a user click. Provide Stop, cancel previous speech, and cancel playback when the source or language changes or the panel closes.

**Verify:** installed and missing language voices, initial empty voice list, unsupported browser, rapid language changes, playback cancellation, and one native-speaker text review. Local speaker playback is the delivery demonstrated in this sprint.

### S06 — Forecast briefing and policy references

Targets: shared context/generation service, a small versioned policy-reference module, `AlertsPanel.tsx`, and `CellDetailPanel.tsx`.

- Explain the selected run's forecast interval and supplied wind information. State when the model is deterministic dispersion and when causal attribution or a plume arrival time has not been validated.
- Keep PM2.5 units explicit. If an illustrative PM2.5 sub-index is ever shown, label it as a sub-index; do not use it to activate GRAP.
- Verify the latest CAQM schedule, then store its official URL, revision date, scope, and review date with any referenced measures. Do not let Gemini create the action list or citations.
- Retain `Official GRAP stage not assessed` unless a separately verified official order is supplied with jurisdiction and effective time. If the complete current schedule cannot be obtained, ship the official link and limitation notice rather than an inferred policy badge.

**Verify:** out-of-NCR cases, unknown jurisdiction, missing AQI, forecast-only input, stale source/order, absent policy reference, and demo data. None may create an official stage or legal action.

### S07 — Verification and presentation

- Run focused backend tests for changed services, migration checks against an isolated database, frontend build/lint, and the relevant existing photo/incident/publication regressions. Broaden the suite if shared behavior changed or failures require it.
- Use the browser to follow one case from source evidence through the brief, language/audio preview, forecast reference, and simulator acknowledgement.
- Confirm the same source/run IDs, numerical facts, and data-mode labels appear throughout. Verify a failed Gemini call still leaves useful dated evidence visible.
- Rehearse a three-minute sequence using saved, versioned outputs. When the API is unavailable during the pitch, label those saved results and their generation time. Test at the actual venue/device if possible.
- Update `STRATEGY.md`, README, and the demo script to match what was actually verified. Report evidence-backed capabilities instead of completion percentages or unsupported impact numbers.

**Complete when:** every shipped feature has a successful browser path and a failure path, outputs remain tied to evidence, quotas are bounded, and the pitch accurately describes live versus demonstrated functionality.

## 6. Scope cuts when time is short

First ship **one alert -> one draft inspection brief -> Hindi/English text -> available local voice preview**. Add incident evidence, Punjabi text, and GRAP reference detail in that order. A missing Punjabi voice does not block Punjabi text.

Keep automated legal issuance, fines/FIR generation, statutory stage activation, real village broadcast delivery, Haryanvi speech, additional satellite vendors, formal privacy mechanisms, model retraining, sensor hardware integration, and deployment migration out of this sprint. Existing federation and responder workflows can be shown after their own setup succeeds; no new core capability is needed for those demonstrations.

## 7. Sources and verification limits

External sources checked on 26 October 2026:

- [Google Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing): current free-tier model availability and content-use distinctions. Free API access does not establish free hosting or unlimited capacity.
- [Google Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits): project/tier-dependent limits and capacity caveats; check the actual project in AI Studio.
- [MDN: SpeechSynthesis.getVoices](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis/getVoices): device-specific voice availability and the `voiceschanged` pattern.
- [CAQM official site](https://caqm.nic.in/landingpage/pages/home): lists the September 2026 revised GRAP schedule and Direction 104. The dynamically linked complete schedule was not retrieved in this review; action-level implementation remains gated on obtaining it.
- [Official PIB/Environment Ministry GRAP release, 21 October 2026](https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2326133&lang=1&reg=3): Delhi AQI, meteorological forecasts, and CAQM Sub-Committee decision process. This dated release is context, not a continuously current stage feed.
- [India Code: Air Act reference](https://www.indiacode.nic.in/bitstream/123456789/7781/1/air_act-1981.pdf): the official text was identified but full retrieval timed out. Current amendments, delegations, and notice requirements were not verified; statutory issuance is excluded from the sprint.

This planning review did not inspect private Gemini quotas, enable billing, send incident evidence to a model, change deployment configuration, or rerun the previous application test suite. The code observations above were rechecked against the reviewed commit; prior runtime findings must be revalidated in S00.
