# Air Health: Evidence-Led Hackathon Strategy

**Track:** Clean Air and Climate Resilience · **Project:** GDG Code for Communities

## Purpose

Air Health is a working prototype for reviewing hyper-local air-quality signals in Indian cities. It combines configured citizen, ground, satellite, and meteorological inputs on an H3 grid, with forecast inspection, evidence review, and a simulated responder workflow. Feed availability, freshness, and geographic coverage depend on configuration and source cadence. This strategy describes the implemented software without claiming a live city deployment, validated national predictions, regulatory authority, or measured public-health impact.

The judging rubric rewards problem fit (20%), AI execution (25%), reach (20%), deployability (20%), and impact potential (15%). The strongest demonstration is a complete, reproducible workflow with visible provenance and clear limitations, rather than unverified scale or impact claims.

## Implemented prototype capabilities

| Capability | What the repository supports | Limit to state in the demo |
| --- | --- | --- |
| Citizen reports | Flutter flow for smoke/fire observations and optional photos; backend strips image metadata before review. | Reports are observations, not confirmed emissions or public-health measurements. |
| Environmental inputs | Adapters for OpenAQ, Open-Meteo, NASA FIRMS, and Copernicus Sentinel-5P. | A configured adapter does not prove that its latest collection succeeded. Show its actual status, observation time, and coverage. Satellite products are not surface PM2.5. |
| Spatial dashboard | H3 map, source layers, current/forecast views, and source/run details. | Displayed fallbacks and illustrative geometry are not live observations. |
| Forecasts and corridor view | Inspect published model runs and configured corridor geometry where available. | Forecasts are experimental. Corridor geometry is illustrative; do not claim a validated plume arrival, transport cause, or route recommendation. |
| Hotspot evidence review | Assemble available signals into candidate evidence for human review. | A candidate is not a confirmed pollution source. Do not imply every source is independently corroborated. |
| Gemini photo assessment | Optional structured visual observations for consented photos, when the server key/provider is configured. | The model cannot infer AQI, concentration, exact location/time, or a confirmed source from the image. A reviewer remains responsible. |
| Reviewer briefs and community text | Dashboard drafts for eligible published alerts, with saved-run evidence, missing-input notes, editable narrative, English/Hindi/Punjabi text, and optional local speech preview. Gemini use requires a configured reviewer key, server key, and explicit consent. | Drafts are advisory and reviewer-only. Hindi and Punjabi text is not fluent-speaker reviewed; speech depends on an exact matching device voice. No alert is broadcast. |
| Forecast interpretation and policy reference | Selected-run forecast details include horizon, times, method, input mode, and displayed wind sample; a dated NCR-only CAQM reference is linked. | This is not source attribution or validated plume arrival. Official GRAP stage remains not assessed; the card does not encode actions. |
| Incident workflow | Backend incident records, role-scoped transitions, and a Flutter simulator. | The simulator does not contact emergency services or dispatch physical units. A webhook is active only after its destination is configured and verified. |
| Federation experiment | Local client/aggregator demonstrations that exchange model updates. | Participants are not verified agencies; parameter exchange alone does not guarantee privacy or establish inter-state operations. |

## Implemented scoped demo extensions

The three scoped extensions below are implemented in the repository. The focused checks and remaining end-to-end verification are recorded in [the implementation plan](docs/STRATEGY_IMPLEMENTATION_PLAN.md); do not imply a live browser, database, or Gemini path was verified until that check is completed.

1. **Evidence-backed draft inspection brief.** The dashboard supports eligible warning/critical published alerts and hotspot events with a matching alert. It shows server-resolved run facts, source IDs, times, missing evidence, and a locally editable narrative. It does not generate or issue statutory notices, fines, or legal findings.
2. **Community advisory text and local audio preview.** The saved alert context produces English, Hindi, and Punjabi text. Hindi and Punjabi require fluent-speaker review before public use. Audio plays only after a user click and only when the device offers a matching language voice; it is not a broadcast service.
3. **Forecast explanation and policy references.** The selected published forecast is explained with its run, horizon, times, method, input mode, and displayed wind sample. A dated NCR-only CAQM reference is available, while official stage remains `not assessed`. A cell PM2.5 concentration is not the official city AQI.

Gemini availability and free-tier capacity vary by project and model. Check the configured project quota before a live demo. Apply the application's own shared budget and deterministic fallback; never promise zero cost or a universal request limit. Keep provider credentials on the server. Do not send private reports, reporter identity, or raw images to a new text-generation path without explicit consent and data minimization.

## Claims to avoid

- A fixed evening burning peak or a satellite blind spot at a particular hour without a cited, local observation analysis.
- Guaranteed source attribution, legal defensibility, calibrated regulatory uncertainty, or causal plume tracking.
- An exact Punjab-to-Delhi travel time or a promised future AQI spike without a validated model and independent evaluation.
- Automatic GRAP triggers inferred from PM2.5 concentration, legal notices, fines, FIRs, or physical dispatch.
- Verified state-agency participation, guaranteed privacy from federated learning, nationwide live coverage, or a specific number of people protected.
- Universal Punjabi/Haryanvi speech support or delivery to a village from browser speech synthesis.
- "100% free", unlimited Gemini access, or guaranteed hosting capacity.

## Three-minute demonstration

Use one case from start to finish; do not join observations from different runs to make a stronger story. Before presenting, check the API and database health, publication mode, source ages/coverage, and scheduler status. If a live publication is unavailable, choose a reproducible seeded case and visibly label every derived result as demo/fallback.

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:25 | Map, selected area, and source-status panel. | “The labels show which inputs are live, stale, missing, or illustrative.” |
| 0:25–0:55 | One eligible published alert and its saved-run evidence timestamps. | “This is a candidate for human review. The brief uses the selected alert's own run and shows missing evidence.” |
| 0:55–1:35 | Draft brief, provider status, source references, then English/Hindi/Punjabi text. Play local speech only if an exact matching voice is available. | “Gemini is optional and requires explicit consent; otherwise the deterministic template is used. Hindi and Punjabi need fluent-speaker review, and speech is local only.” |
| 1:35–2:20 | Selected forecast briefing and NCR-only CAQM reference. | “This explains the published model run and its limits. Official GRAP stage remains not assessed.” |
| 2:20–3:00 | Simulator incident acknowledgement/status transition. | “This is an auditable software simulation. No emergency responder is being contacted.” |

Do not improvise a live-data, model, or authority claim if the corresponding source/status is not visible. If Gemini is not configured, show the template status without making a provider call. A demo is ready only after the exact case and its mode have been checked in the running application; this script has not been rehearsed against a running end-to-end deployment.

## How to present the scoring fit

- **Problem fit (20%):** demonstrate how complementary local observations can be reviewed together, and name the missing coverage that remains.
- **AI execution (25%):** show a configured, bounded Gemini photo assessment or generated draft with source references, uncertainty, and human review. Do not describe ordinary threshold rules as generative AI.
- **Reach (20%):** show the spatial design and language support that is actually available. Avoid implying nationwide deployment or agency adoption.
- **Deployability (20%):** run the documented Docker/local workflow, show API health and reproducible setup, and report required configuration.
- **Impact potential (15%):** explain the plausible response workflow and what a city pilot would need to measure; do not state unmeasured outcomes as achieved impact.

**Submission materials:** public repository, runnable prototype, concise walkthrough, documented limitations, and screenshots/recording from the tested build. Keep the README, pitch, and demo narration aligned with the code that was actually verified.
