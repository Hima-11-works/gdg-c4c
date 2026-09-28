# air_health_flutter

A Flutter air-quality **health companion** — personal environmental exposure
awareness. It shows the local pollution level, a short-term forecast, nearby
lower-pollution areas, and personalised alerts derived from the user's
sensitivity profile.

It is a **partner app in this monorepo**, living under `partner_apps/` and
independent of the web platform (`backend/`, `frontend/`). For the design and
rationale, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

> This is an environmental exposure-awareness app. It is **not** a diagnostic,
> treatment, medication-management, or emergency medical decision system, and
> it never invents disease-specific AQI thresholds — it classifies with the
> official India **CPCB AQI** categories.

## Stack

Flutter / Dart · **Riverpod** (state & DI) · **go_router** (navigation) ·
**Dio** (HTTP) · `flutter_local_notifications` · `geolocator` +
`permission_handler` · `flutter_secure_storage` + `shared_preferences` ·
`fl_chart` · `intl` · `image_picker`.

## Citizen reports

When connected to the backend, the fire/smoke report sheet can attach one
camera or gallery photo to a report. The app asks for explicit consent before
uploading and resizes the selected image for mobile upload. The backend stores
the original privately and exposes only its metadata-stripped review derivative.
If photo upload fails after the report is saved, the sheet keeps the report id
and lets the citizen retry the photo upload without creating a duplicate report.

## Layout

```
lib/
├─ app/           MaterialApp.router, theme
├─ core/          Clock, formatters, Result
├─ domain/        Pure Dart — models, alert engine, sensitivity rules
├─ data/          PollutionDataProvider + Dummy / Remote implementations
├─ providers/     Riverpod wiring (the only DI layer)
├─ features/      home · nearby · alerts · profile · onboarding
├─ routing/       go_router + onboarding redirect
├─ theme/         tokens, CPCB colors, reusable widgets
├─ storage/       secure profile store, prefs store
├─ notifications/ local-notification wrapper
└─ mocks/         dev scenarios + simulator
```

`domain/` has no Flutter imports; screens read data only through Riverpod
providers. The data source is chosen in one place (`providers/data_providers.dart`):
`DummyPollutionDataProvider` (8 deterministic scenarios) is active today;
`RemotePollutionDataProvider` is a Dio skeleton awaiting a real API contract.

## Running

```bash
cd partner_apps/air_health_flutter
flutter pub get
flutter run
```

To point the (skeleton) remote provider at a real API, pass details via
`--dart-define` (no keys or URLs are hardcoded):

```bash
flutter run \
  --dart-define=POLLUTION_API_BASE_URL=https://... \
  --dart-define=POLLUTION_API_KEY=...
```

## Tests

```bash
flutter analyze
flutter test
```

Tests are offline — deterministic dummy scenarios, no network or Firebase.
