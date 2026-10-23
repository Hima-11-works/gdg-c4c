# Air Health: A Climate Intelligence Prototype
## 12-slide hackathon pitch and speaker walkthrough

**Track:** Clean Air and Climate Resilience
**Theme:** Sustainability
**Project:** GDG Code for Communities (C4C)

### Brief description

Air Health is a working prototype for exploring hyper-local air-quality signals in Indian cities. It combines available ground, citizen, satellite, and weather inputs on an H3 grid, provides forecasts and review tools, and demonstrates citizen and responder workflows. Feed coverage varies; forecasts, hotspot candidates, and federated learning remain experimental and are not production authority services.

---

## Slide 1: Air Health

### A prototype for local air-quality awareness and climate response

- Web map: React, MapLibre, H3, FastAPI, PostgreSQL/PostGIS.
- Citizen and incident-simulator apps: Flutter.
- Gemini photo interpretation is advisory and subject to human review.
- Data adapters include OpenAQ, Copernicus Data Space Ecosystem, NASA FIRMS, and Open-Meteo; availability depends on configuration and source cadence.

**Speaker notes:** Air Health is a working prototype for bringing several kinds of air-quality evidence into one review workflow. It is designed around the challenge of local events that broad monitoring may miss. Today, source coverage and model validation vary, so we make those limits visible in the product.

---

## Slide 2: The gap

### City-wide readings do not explain every local event

- Official monitoring provides essential ambient measurements, but a sparse network can miss conditions between stations.
- Photos and community sensor readings can add local context, with uneven coverage and reliability.
- Satellite products observe different atmospheric signals and cannot be treated as surface PM2.5 measurements.
- A useful response needs source, time, coverage, and uncertainty alongside each estimate.

**Speaker notes:** The challenge is not that existing monitors are unhelpful. The gap is that no one source gives a complete picture at every place and time. Air Health explores how to bring complementary observations together without implying they are interchangeable.

---

## Slide 3: What the prototype does

### From available observations to a reviewable map

```text
Configured data feeds + citizen reports
                 ↓
       H3 spatial aggregation
                 ↓
 Nowcast, forecast, and candidate review
                 ↓
 Web dashboard + citizen app + incident simulator
```

- Source freshness and coverage depend on configured feeds.
- The map distinguishes measured inputs, model estimates, and illustrative geometry.
- Candidate events remain unverified until a reviewer acts.
- The responder app demonstrates a workflow; it does not itself contact emergency services.

**Speaker notes:** The software has a web map, backend, citizen app, and simulator that can be run together. The interface makes the data path visible, and labels scenario data so a demo cannot be mistaken for a live city feed.

---

## Slide 4: AI, data, and engineering

| Component | Current role | Important limit |
|---|---|---|
| Gemini multimodal model | Produces advisory structured observations from a submitted image | It cannot determine AQI, pollutant concentration, or a confirmed source from a photo; a person reviews reports |
| Copernicus and NASA feeds | Provide satellite-derived context when configured | Column products and thermal detections are not direct surface PM2.5 readings |
| OpenAQ and Open-Meteo | Ground and meteorological inputs when available | Freshness, station coverage, and product availability vary |
| H3, FastAPI, PostGIS, MapLibre | Spatial indexing, API, storage, and map display | H3 is a spatial framework, not a measurement source |
| Flutter | Citizen reporting and simulated incident workflow | Apps are prototype clients, not certified public-health or emergency systems |

**Speaker notes:** Gemini is used narrowly for image observations. It does not diagnose pollution or decide whether a report is true. We also use Copernicus Data Space Ecosystem integrations rather than claiming Google Earth Engine.

---

## Slide 5: Hotspot review

### Candidate screening, followed by human review

1. Available satellite, fire, citizen, and sensor signals can be assembled into a candidate evidence view.
2. The dashboard shows the candidate's evidence and confidence information when present.
3. A reviewer can record a decision and inspect the source data.

A candidate is **not** a confirmed emission event. Satellite indicators may be missing, stale, or affected by conditions such as clouds. The system does not promise zero false alarms or automatically dispatch responders.

**Speaker notes:** This is a triage aid. We present possible signals for review and preserve the distinction between a signal and a verified source. A reviewer, not the model, records the decision.

---

## Slide 6: Corridor forecasts

### Inspect a published model run along configured corridor geometry

- The dashboard can display the latest backend-published forecasts for a configured corridor when a run is available.
- Corridor geometry is illustrative and does not represent an authority-approved logistics route.
- The detail panel reports the run, method, issue time, source references, and observed-data evaluation status.
- A run with insufficient observed labels does not establish real-world accuracy.

**This prototype does not recommend freight departure times, predict visibility, estimate travel delays, or provide driver medical guidance.**

**Speaker notes:** The corridor view is a way to inspect the model output spatially. We show the actual published run and its evaluation limits. It is not a route scheduler, traffic model, or operational logistics recommendation.

---

## Slide 7: Citizen companion

### Contribute evidence and compare nearby estimates

- Submit smoke or fire reports with optional photos; backend media processing removes image metadata before review.
- Choose a sensitivity preference that changes app exposure-alert thresholds. These alerts are general information, not clinical advice.
- Compare nearby current and short-horizon AQI estimates where data exists.
- Nearby estimates may be incomplete or stale. They are not safety ratings or recommendations to relocate.
- Offline place names are resolved when the gazetteer has a match; an unresolved cell may show a shortened cell label.

**Speaker notes:** The citizen app supports reporting and environmental awareness. It does not recommend a safe destination, diagnose a condition, or replace public-health guidance.

---

## Slide 8: Incident simulator

### Demonstrate an auditable incident workflow

The Flutter simulator can read incidents from a configured backend and record permitted status changes, such as assignment, acknowledgement, en-route, on-scene, and resolution.

- Status changes persist in the configured backend.
- The app labels itself as a simulation.
- It does not call emergency services, send public alerts, or dispatch physical units.
- Optional authority webhook delivery is a separate backend configuration and must not be represented as active unless an endpoint is configured and verified.

**Speaker notes:** This is a backend-connected simulator, not a live dispatch console. It demonstrates how an incident record may move through states while making clear that no responder outside the demo is being contacted.

---

## Slide 9: Federated-learning experiment

### A local workflow prototype, not a network of Indian agencies

```text
Local demo client A ─┐
                     ├── model updates → local demo aggregator
Local demo client B ─┘
```

- The repository includes a single-process partition demo and a separate client/aggregator workflow.
- Demo runs can use synthetic or test data; participant identities are not independently verified as agencies.
- Model updates and evaluation summaries are exchanged according to the implemented protocol.
- Exchanging model parameters is **not** a privacy guarantee. Differential privacy, secure aggregation, and a production security review are not established by this prototype.

**Speaker notes:** We have a runnable experiment for model exchange, not state pollution boards operating production nodes. The implementation reports its data provenance and limitations, and we do not claim differential privacy or national predictive accuracy.

---

## Slide 10: What is implemented and what remains uncertain

### Working prototype; no production deployment claim

**Implemented:** web dashboard, backend APIs, PostgreSQL/PostGIS data model, ingestion and forecast jobs, citizen reporting client, incident simulator, Gemini advisory integration, CI checks, and local Docker development workflow.

**Run-dependent:** live feed freshness, geographic coverage, available forecasts, satellite layers, and corridor evaluation results.

**Not established:** a live city deployment, agency participation, forecast accuracy at national scale, certified health guidance, or real emergency dispatch.

**Speaker notes:** The project is a working software prototype that can be run and evaluated. We distinguish shipped application workflows from outcomes that would require current feeds, independent validation, or partner deployment.

---

## Slide 11: Responsible path to a pilot

### Validate before expanding

1. Work with a city partner to define a limited pilot area and authorized data sources.
2. Publish per-feed freshness, coverage, missingness, and data-quality indicators.
3. Evaluate forecasts on time-separated, independently held-out station data and report baselines.
4. Review privacy, security, model-update leakage, and consent before multi-party federation.
5. Connect authority workflows only after the receiving endpoint, ownership, and response process are verified.

**Speaker notes:** Scaling should follow evidence. The next milestone is not a cloud migration plan; it is a bounded pilot with agreed data, measurable accuracy, privacy review, and a verified response owner.

---

## Slide 12: Hackathon fit and live demo

### A concrete starting point for coordinated climate action

- **Problem fit:** combines local reports with environmental context on a shared spatial grid.
- **AI integration:** Gemini image observations are structured, advisory, and human-reviewed.
- **Working software:** show the dashboard, citizen report flow, published-run corridor detail, and simulated incident status change.
- **Interoperability:** show the local federation workflow and explicitly state that it is experimental.
- **Next evidence:** fresh partner data, independent model evaluation, and a verified authority pilot.

**Demo sequence:** open the map and source-status panels; review a candidate; open a published corridor run if one exists; submit a test citizen report; then advance a clearly labeled simulator incident.

**Source repository:** [github.com/Hima-11-works/gdg-c4c](https://github.com/Hima-11-works/gdg-c4c)

**Speaker notes:** Air Health is a working prototype that turns the hackathon problem into a set of reviewable software flows. We will show what is implemented, call out what is illustrative, and use the next steps to describe how the prototype could be validated with city partners.

---

## Submission checklist

- Use a public repository link and a runnable demo package.
- Include a short walkthrough with the exact demo sequence above.
- Show configured source availability and label any synthetic or scenario data.
- Do not claim deployment, agency participation, privacy guarantees, validated clinical guidance, or predictive accuracy without evidence.
