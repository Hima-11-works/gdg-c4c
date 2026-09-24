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

## Reporting a fire, and its evidence

The report sheet is a two-step flow against two endpoints, matching
[`docs/api/citizen-intake.md`](../../../docs/api/citizen-intake.md):

1. `POST /api/v1/reports` creates the report. One client-generated idempotency
   id per sheet, so a retry cannot stack duplicates.
2. `POST /api/v1/reports/{id}/evidence` attaches the selected photo and/or the
   resident's own sensor reading. It is keyed by the id step 1 returned, and
   that id is held for the rest of the sheet's life.

**The sheet stays open when the evidence fails.** That is deliberate: closing it
would throw away the report id, and with it the only way to retry without
filing a second report. The retry button resends the *same* evidence
idempotency key, so an identical payload comes back as `200` with the stored
record instead of a second one. A transport failure is presented as retryable; a
refusal (`415`, `422`, `409`) is not, because it would fail identically next
time — so the sheet says what to change instead.

What the sheet will not imply: a citizen reading is stored **only** on the
evidence record, is never a station observation, and never feeds the pollution
model. Every record starts `unverified` and the sheet shows the status the
server returned.

### Photo picking

Photos are picked through a `MethodChannel` (`air_health/evidence_photo`) to
the host platform, implemented in `MainActivity.kt`, rather than through a
picker plugin — the app resolves its packages from `pubspec.lock`, and a new
plugin could not be fetched and locked on the machine this was built on. The
host checks the file size *before* reading the bytes and takes the content type
from the content resolver, not the file extension. On a platform with no host
implementation the sheet says choosing a photo is unavailable, and a sensor
reading can still be attached on its own.

## Stack

Flutter / Dart · **Riverpod** (state & DI) · **go_router** (navigation) ·
**Dio** (HTTP) · `flutter_local_notifications` · `geolocator` +
`permission_handler` · `flutter_secure_storage` + `shared_preferences` ·
`fl_chart` · `intl`.

## Layout

```
lib/
├─ app/           MaterialApp.router, theme
├─ core/          Clock, formatters, Result
├─ domain/        Pure Dart — models, alert engine, sensitivity rules
├─ data/          PollutionDataProvider + Dummy / Remote implementations
├─ providers/     Riverpod wiring (the only DI layer)
├─ features/      home · nearby · alerts · profile · onboarding · reports
├─ routing/       go_router + onboarding redirect
├─ services/      evidence photo picker (platform channel)
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

> **Unrun.** There is no Dart/Flutter SDK on the machine this was built on, so
> `flutter analyze` and `flutter test` have not been executed and the app has
> never been compiled. The evidence flow is covered by
> `test/domain/report_evidence_test.dart` (bounds, wire parsing, error
> classification) and `test/features/report_fire_sheet_test.dart` (the two-step
> flow, the preserved report id, retry, and the refusal path), but those tests
> are unverified.
