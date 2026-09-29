# Google Stack Integration Plan

**Target:** integrate Gemini, Firebase Hosting, Cloud Functions for Firebase, Firebase Storage, and Google Maps while preserving the app's existing visual style and core pollution-reporting workflow.

**Time budget:** 175 minutes of implementation and verification, leaving five minutes of contingency. This schedule assumes Firebase access, Blaze billing, credentials, and the existing database are ready. The backend adaptation is an additional risk; these time slots are targets, not a guarantee of full compatibility within three hours.

**Deployment approach:** manage the application through Firebase Console and the Firebase CLI. Firebase still uses an underlying Google Cloud project and infrastructure; Functions and Storage require billing. Google Maps retains its separate Maps Platform API setup. Keep the existing PostgreSQL/PostGIS database and Gemini integration.

## Integration choices

| Service | Use in this project |
|---|---|
| Gemini API | Advisory assessment of citizen photo evidence, with structured output for human reviewers. |
| Firebase Hosting | Host the React frontend and route API requests to Cloud Functions for Firebase. |
| Firebase Storage | Private, durable photo storage accessed through the backend. |
| Cloud Functions for Firebase | Serve the existing API through a Python HTTPS function, adapting the HTTP entry point while reusing backend services. |
| PostgreSQL/PostGIS | Keep the existing database for reports, spatial data, forecasts, and incidents. |
| Google Maps JavaScript API + deck.gl | Google basemap with the existing H3, hotspot, report, and boundary overlays. |

The Google Maps migration is the largest risk: `MapView` is a large MapLibre-specific component. Preserve the existing React panels, controls, API hooks, and H3 calculations; replace the map renderer in stages. Aim first for the dark basemap, H3 cells, cell selection, search, alerts, and timeline. Defer wind animation, smooth-field rendering, contours, and optional raster overlays if time runs short.

## Step-by-step schedule

### 1. Prepare Firebase and API credentials — 0–15 minutes

1. Create or choose one project in Firebase Console and enable the Blaze plan.
2. Register a web app and initialize Firebase Hosting, Storage, and Python Functions through the Firebase CLI. Let Firebase provision the required deployment services; no separately managed Cloud Run service is planned.
3. Enable Maps JavaScript API for the same underlying project using Google Maps Platform. This step remains necessary for the Google Maps migration.
4. Create a Gemini API key in Google AI Studio.
5. Create a separate Maps browser key. Restrict it to the Maps JavaScript API and allowed localhost/deployed web referrers. Keep the Gemini key server-side.

### 2. Verify and deploy the backend — 15–35 minutes

1. Fix the current API 500s before adding features. Check database connectivity, migrations, and the metadata/report endpoints.
2. Add a Python HTTPS function named `api`. Firebase's Python HTTP handler uses Flask request/response semantics; FastAPI is ASGI, so the existing container is not a drop-in deployment. Prove a compatible adapter in the emulator, including async handling, authorization, query strings, multipart photos, binary responses, and error codes. If necessary, write thin HTTP handlers that call the existing services while preserving API contracts.
3. Keep PostgreSQL/PostGIS and run its migrations once as a deployment step, never during individual function invocations. Verify database connectivity from Functions and cap instances/database pools to the existing database's connection capacity. Run long ingestion/forecast work outside user HTTP requests; preserve the current pipeline runner for this deadline.
4. Configure database, Gemini, and provider secrets with `firebase functions:secrets:set SECRET_NAME`, and bind them explicitly to the function. Firebase manages the underlying secret service. Configure region, memory, timeout, and maximum instances in the function configuration.
5. Deploy with `firebase deploy --only functions` and verify:

   - `/health`
   - `/health/ready`
   - `/api/v2/meta`
   - `/api/v2/reports`

**Gate:** these routes and a protected photo request return valid responses from the deployed function before continuing. Validate existing endpoint contracts; do not treat a working health check alone as a completed backend migration.

### 3. Connect Firebase Hosting to Cloud Functions — 35–45 minutes

1. Configure Firebase Hosting to serve the frontend's `dist` directory.
2. Add rewrites before the React fallback:

   ```json
   {
     "source": "/api/**",
     "function": { "functionId": "api", "region": "YOUR_REGION" }
   }
   ```

   Add a matching `/reports/**` rewrite because photo routes use that prefix, plus `/health` and `/health/**` for the health routes. Preserve the original request paths in the function adapter. Keep the final `/**` rewrite pointed at `/index.html` for React routing.
3. Update frontend API configuration to use relative URLs in production. The current fallback to `localhost` must not be used in a deployed build.
4. Deploy with `firebase deploy --only hosting` and confirm browser API calls reach the HTTPS function. Keep requests within Firebase Hosting's 60-second timeout; longer work needs an asynchronous job/status flow.

### 4. Replace MapLibre rendering with Google Maps — 45–95 minutes

1. Create a Google Maps JavaScript map ID and associate a dark, muted style with it. Match land, water, roads, and labels; hide unnecessary POIs. MapLibre style JSON is not directly reusable.
2. Integrate Google Maps JavaScript API with deck.gl's `GoogleMapsOverlay`. Start with a flat map and `interleaved: false` to reduce integration risk.
3. Port overlays in this order:

   - H3 cell polygons and current color scales with `GeoJsonLayer`.
   - Selected-cell white outline.
   - Citizen reports and hotspot points.
   - State/district boundaries and selected-area dimming.
   - Cell click picking wired to the existing detail panel.
   - Search and alert navigation wired to Google Maps camera movement.
   - Existing resolution limits, loading progress, and request cancellation.

4. Check initial rendering, zoom/resolution changes, search, alert selection, and cell details before moving on. Keep a rollback path to the current map implementation until this passes.

**Defer if needed:** wind trails, smooth raster view, contours, and satellite raster overlays. Google basemap styling will be close to the existing look, but Google labels and attribution prevent an exact copy.

### 5. Store photos in Firebase Storage — 95–110 minutes

1. Add a Firebase Storage adapter using the Firebase Admin SDK to the existing backend `MediaStore` interface.
2. Give the Functions runtime service account access to the private Firebase Storage bucket. Admin SDK access uses IAM; enforce user authorization in the API because Firebase client Security Rules do not protect server SDK operations.
3. Keep uploads and reads behind the API. Preserve image validation, EXIF removal, reviewer authorization, retention, and deletion.
4. Test upload, authorized read, deletion, and persistence after a service restart. Confirm unauthenticated direct bucket access is denied.

### 6. Add Gemini photo assessment — 110–140 minutes

1. Add **Analyze with Gemini** to the existing citizen-photo review dialog.
2. Send the sanitized photo derivative and available report/environmental context to Gemini from the backend.
3. Validate structured output with these fields:

   ```text
   visible_observations
   possible_event_type
   supporting_evidence
   missing_information
   uncertainty
   reviewer_summary
   ```

4. Store the assessment with the photo/report reference, model identifier, and generation time. Reuse the saved assessment when the reviewer reopens it.
5. Add a bounded timeout and a retry/manual-review state for quota, network, or validation errors.
6. Keep Gemini advisory: it cannot invent pollutant readings, approve reports, or dispatch responders.

### 7. Connect reviewed evidence to authority workflow — 140–150 minutes

1. Attach the saved assessment to the existing incident or link to it from the incident.
2. Point the Flutter authority simulator to the Firebase Hosting API URL backed by the HTTPS function.
3. Verify an incident appears in its queue and can be acknowledged and resolved.
4. Use the existing in-app inbox for this deadline; describe it as simulated/polling delivery, not a real emergency notification.

### 8. Verify and rehearse — 150–175 minutes

- Google map renders on first load with colored H3 cells.
- Search, resolution limits, alerts, selection outline, and timeline work.
- Moving the map cancels obsolete cell requests.
- A smoke photo receives a Gemini assessment; an unrelated image produces an uncertain assessment.
- Gemini failure leaves manual review available.
- Private photos require authorization.
- A reviewed incident reaches the authority app and can be acknowledged.
- Production requests contain no localhost URLs or server secrets.
- Deploy final backend and frontend, then rehearse the complete report-to-authority workflow.

## Defer beyond the three-hour window

- Firestore or Cloud SQL migration.
- Firebase Authentication migration across both Flutter apps.
- Firebase Cloud Messaging push notifications.
- New forecasting models or nationwide data expansion.
- Full parity for every MapLibre animation and raster overlay.

Keep forecasts and the federation prototype visible only with accurate descriptions of their current coverage and status.

## Official references

- [Firebase Hosting with Cloud Functions](https://firebase.google.com/docs/hosting/functions)
- [Firebase Python HTTP functions](https://firebase.google.com/docs/functions/http-events)
- [Firebase Functions configuration and secrets](https://firebase.google.com/docs/functions/config-env)
- [Firebase Storage Admin SDK](https://firebase.google.com/docs/storage/admin/start)
- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Google Maps cloud styling](https://developers.google.com/maps/documentation/javascript/cloud-customization/map-styles)
- [Google Maps API key security](https://developers.google.com/maps/api-security-best-practices)
- [deck.gl Google Maps integration](https://deck.gl/docs/developer-guide/base-maps/using-with-google-maps)
- [Firebase Functions deployment and runtime settings](https://firebase.google.com/docs/functions/manage-functions)
