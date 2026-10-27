# Air Health: 5-Minute Pitch Deck & Live Feature Demo Script
**Track:** Clean Air and Climate Resilience | **Theme:** Sustainability  
**Project:** GDG Code for Communities (C4C)  
**Core Google Technologies:** Google Gemini 2.0 Flash Multimodal Vision & Structured Outputs, Google Flutter SDK, Google Maps Platform Roadmap  
**Live Deployed Demo:** [https://air-health.vercel.app](https://air-health.vercel.app) | **API Base:** [https://air-health-api.vercel.app](https://air-health-api.vercel.app)

---

## ⏱️ Executive Pitch Structure
- **Total Pitch Time:** Exactly 5:00 minutes (300 seconds).
- **Spoken Delivery:** ~15 seconds per slide (~35 words) = ~3:00 minutes total.
- **Live Demo Feature Showcase:** ~10–15 seconds per slide = ~2:00 minutes total.
- **Key Focus:** Honest representation of merged production code, highlighting the critical **satellite revisit cadence gap vs real-time fires**, and showing why **Google Gemini AI & Flutter** make this a winning, sellable civic product.

---

## 📊 Slide Timing & Demo Choreography Matrix

| Slide # | Slide Title | Word Count | Talk Time | Live Demo Screen Action | Demo Time |
|:---:|:---|:---:|:---:|:---|:---:|
| **01** | **Title:** Local signals. Coordinated clean-air action. | 35 | 15s | Full-screen map landing view with H3 hex cells | 10s |
| **02** | **The Gap:** Satellite pass intervals vs anytime fires | 37 | 15s | Zoom into adjacent cells showing local station blind spots | 10s |
| **03** | **Solution:** One unified H3 hexagonal operating picture | 36 | 15s | Click an active cell to slide open Cell Detail Panel | 10s |
| **04** | **Google Gemini AI:** Grounded Multimodal Vision & Advisories | 38 | 15s | Open Gemini Photo Assessment & Punjabi/Hindi tabs | 15s |
| **05** | **Hotspot Review:** Candidate screening before dispatch | 35 | 15s | Filter by 'Hotspots'; inspect FIRMS + Sentinel badges | 10s |
| **06** | **Corridor Forecasting:** Predictive dispersion modeling | 34 | 15s | Open Corridor View; slide 1h, 3h, 6h forecast horizons | 10s |
| **07** | **Tri-Persona:** One shared, trusted operating picture | 34 | 15s | Highlight multi-persona matrix (Resident, City, Crew) | 10s |
| **08** | **Citizen Companion:** Flutter app & Gemini consent toggle | 36 | 15s | Show Flutter mobile app with photo consent checkbox | 10s |
| **09** | **Responder Console:** 16MB Flutter APK & state machine | 36 | 15s | Advance incident state on Android simulator app | 10s |
| **10** | **Federation PoC:** Early cross-state prototype | 19 | 8s | Briefly point to the Federation status pill | 5s |
| **11** | **Production Stack:** FastAPI, PostGIS & Google Maps | 35 | 15s | Show live latency pill & Google Maps enterprise roadmap | 10s |
| **12** | **Pilot Roadmap:** From signal to safer action | 34 | 15s | Return to macro corridor view; pitch closing KPIs | 10s |
| **TOTAL** | | **423** | **~3:00** | | **~2:00** |

---

## 🎬 15-Second Slide Scripts & Live Demo Showcase

---

### Slide 01: Local signals. Coordinated clean-air action.
* **Live Demo Action (10s):** Display `air-health.vercel.app` full-screen with the Northern India H3 hexagonal grid loaded.
* **Google Tech:** Google Gemini 2.0 API & Google Flutter SDK.

> **🎤 15-Second Speaker Script:**  
> *"Citywide AQI averages blind us to local toxic fires. We built **Air Health**—a civic intelligence platform powered by **Google Gemini** and **Google Flutter** that turns ground and satellite signals into coordinated, neighborhood-scale clean air action."*

---

### Slide 02: The pollution signal is local. The response is fragmented.
* **Live Demo Action (10s):** Zoom in on adjacent H3 cells with sharply differing values to illustrate local variation between stations.
* **Core Differentiator:** Satellites pass only in fixed windows, but fires ignite at any time!

> **🎤 15-Second Speaker Script:**  
> *"Fixed monitors leave 20-kilometer blind spots. Worse, **satellites only pass in fixed time windows once or twice a day—yet fires ignite at any moment and require immediate action**. Without unified local reporting, emergency response is too slow."*

---

### Slide 03: One location grid turns separate signals into a reviewable picture.
* **Live Demo Action (10s):** Click any populated H3 cell on the map to slide open the real-time **Cell Detail Panel**.
* **Google Tech:** Multi-source geospatial indexing fueling Gemini context bundles.

> **🎤 15-Second Speaker Script:**  
> *"Air Health indexes satellite anomalies, weather, and citizen reports into discrete **H3 hexagonal cells**. Our engine ranks candidate hotspots, keeping a human in the loop to review the consolidated evidence bundle before any escalation occurs."*

---

### Slide 04: Gemini adds visual context. Measurements stay with their source.
* **Live Demo Action (15s):** Open the **Photo Review Panel** / **Advisory Panel**. Show Gemini's structured JSON output (uncertainty score + missing info) and click the **Hindi/Punjabi** tabs.
* **Google Tech:** **Google Gemini 2.0 Flash Multimodal Vision & Structured JSON Schema.**

> **🎤 15-Second Speaker Script:**  
> *"Here is **Google Gemini** at work: it analyzes citizen smoke photos using **strict JSON Schemas**, flags visual uncertainty, and crucially, **identifies missing evidence**. Gemini also drafts localized response checklists in **English, Hindi, and Punjabi** without hallucinating numbers."*

---

### Slide 05: A hotspot is a candidate first — not a confirmed fire.
* **Live Demo Action (10s):** Filter by the **Hotspots** tab. Click a candidate cell to show the **Hotspot Evidence Panel** with Sentinel-5P UVAI and NASA FIRMS badges.
* **Core Value:** Triage eliminates costly false dispatches.

> **🎤 15-Second Speaker Script:**  
> *"To eliminate false alarms, Sentinel-5P aerosol anomalies are automatically corroborated with NASA FIRMS thermal pixels and citizen reports within that exact H3 cell. Controllers review a single verified evidence dossier instead of wasting crews on unconfirmed noise."*

---

### Slide 06: Show where conditions may worsen — before the next hand-off.
* **Live Demo Action (10s):** Switch to **Corridor View**. Drag the horizon slider between **+1h, +3h, and +6h** to show the estimated dispersion plume.
* **Feature:** Predictive wind vector dispersion along the Punjab ➔ Haryana ➔ Delhi corridor.

> **🎤 15-Second Speaker Script:**  
> *"Pollution crosses borders before teams can react. Pairing our H3 grid with Open-Meteo wind and boundary-layer physics, Air Health projects corridor dispersion across **one, three, and six-hour horizons**, giving teams valuable early-warning decision support."*

---

### Slide 07: Different people need one shared, trusted view.
* **Live Demo Action (10s):** Point out the multi-persona header on the dashboard linking citizens, municipal analysts, and responders.
* **Core Value:** Tri-persona coordination on a shared API.

> **🎤 15-Second Speaker Script:**  
> *"Air Health unifies three stakeholders on one API: **residents** receive neighborhood exposure alerts, **municipal analysts** receive policy-aligned evidence packages, and **responders** receive tactical coordinates. One shared location context bridges the entire civic response."*

---

### Slide 08: A simple report should lead to a visible next step.
* **Live Demo Action (10s):** Show the **Air Health Flutter Citizen App** on screen or emulator, demonstrating the camera report flow and the consent toggle.
* **Google Tech:** **Google Flutter Framework** & privacy-first **Gemini Consent Gating**.

> **🎤 15-Second Speaker Script:**  
> *"Our **Flutter-powered citizen companion** brings air awareness to every phone. Citizens report smoke with photos, and we enforce **strict consent before AI**—photos are only processed by Gemini after explicit user authorization, guaranteeing privacy and trust."*

---

### Slide 09: Make the hand-off testable before connecting real dispatch.
* **Live Demo Action (10s):** Open the **Fire Department Simulator APK**. Tap an incident card to advance its status from `ASSIGNED` to `EN ROUTE` and `ON SCENE`.
* **Google Tech:** Native **Flutter release APK**, optimized to **16.3 MB** with `--split-per-abi`.

> **🎤 15-Second Speaker Script:**  
> *"For first responders, we built a dedicated **Flutter Fire Department Simulator**, compiled into an optimized **16 MB APK**. It demonstrates an auditable, server-enforced state progression from Assigned to On-Scene with built-in API diagnostics and offline resilience."*

---

### Slide 10: Federation is a working proof of concept — with governance ahead.
* **Live Demo Action (3s):** Briefly point to the Federation status pill on the dashboard header.
* **Note:** Early exploration of model-update exchange between regional nodes.

> **🎤 Short Speaker Script (~8s / 19 words):**  
> *"For cross-state collaboration, we have an early prototype exploring how regional nodes could exchange model updates without centralizing raw data."*

---

### Slide 11: Start with one partner corridor. Expand when the evidence is ready.
* **Live Demo Action (10s):** Show the system health pill and PostGIS database query latency in the footer.
* **Google Tech:** Live FastAPI + PostGIS stack with a clear migration roadmap to **Google Maps Platform**.

> **🎤 15-Second Speaker Script:**  
> *"Our stack is live today on Vercel, Neon Serverless PostGIS, Google Gemini, and Flutter. Our architectural roadmap integrates the **Google Maps Platform** for high-resolution vector tiles and Places autocomplete as we scale city by city."*

---

### Slide 12: Let's pilot Air Health with one Indian corridor.
* **Live Demo Action (10s):** Return to the full corridor view on `air-health.vercel.app` with the call-to-action slide in view.
* **Call to Action:** Measurable civic outcomes (lead time, review turnaround, verified follow-ups).

> **🎤 15-Second Speaker Script:**  
> *"Air Health is ready for a focused Indian corridor pilot. By fusing **Google Gemini's multimodal vision**, **Flutter's speed**, and an auditable H3 grid, we turn local smoke signals into immediate clean-air action. Thank you!"*

---

## 📱 Pre-Built Native Android APK Packages

Ready to install for the pitch and live demonstration:

| Package | File Location | Size | Target Architecture |
|:---|:---|:---:|:---|
| **Fire Dept Simulator (Recommended)** | [`apks/fire_dept_simulator-arm64.apk`](../apks/fire_dept_simulator-arm64.apk) | **16.3 MB** | Modern 64-bit Android smartphones |
| **Fire Dept Simulator (Legacy)** | [`apks/fire_dept_simulator-arm32.apk`](../apks/fire_dept_simulator-arm32.apk) | **13.7 MB** | Older 32-bit Android devices |
| **Fire Dept Simulator (Universal)** | [`apks/fire_dept_simulator-release.apk`](../apks/fire_dept_simulator-release.apk) | **46.2 MB** | Universal multi-ABI release APK |
| **Air Health Citizen Companion** | [`apks/air_health_flutter-arm64.apk`](../apks/air_health_flutter-arm64.apk) | **19.1 MB** | Modern 64-bit Android smartphones |

---

## 💡 Pitch Delivery Tips for the Presenter
1. **Pace**: 35 words at conversational speed takes exactly 14–15 seconds. You don't need to speak rapidly.
2. **Seamless Demo Transitions**: Speak the script while your hands naturally click the corresponding feature on `air-health.vercel.app` or tap the button on the simulator app.
3. **Judges Love the Slide 2 Insight**: Highlighting that polar satellites only photograph an area once a day while fires can start anytime establishes immediate technical authority and justifies your citizen reporting + Gemini triage model.
