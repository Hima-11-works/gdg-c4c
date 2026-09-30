# Air Health Flutter — Architecture

## What this app is

A standalone air-quality health companion for Android (iOS-compatible
architecture). It shows local pollution levels, short-term forecasts,
and personalised alerts based on the user's sensitivity profile.

**This is an environmental exposure-awareness app. It is NOT a
diagnostic, treatment, medication-management, or emergency medical
decision system.**

## What this app is NOT

- A diagnostic tool
- A treatment recommendation system
- A medication management app
- An emergency medical decision system
- A substitute for clinician-provided care

The app uses official India CPCB AQI categories for environmental
classification. It never invents disease-specific AQI thresholds.

## Privacy model

Health profile information (health context, sensitivity tier) is:

- Stored ON-DEVICE ONLY in `flutter_secure_storage`
- NEVER transmitted to any server
- NEVER logged
- NEVER included in analytics
- NEVER exposed in notification lock-screen text
- NEVER required to create an account

The user can edit, reset, or delete their profile at any time from
the Profile screen.

## Architecture

```
lib/
├─ app/              # MaterialApp.router, theme
├─ core/             # Clock, formatters, Result type
├─ domain/           # Pure Dart — models, alert engine, sensitivity rules
│  └─ models/        # Immutable value types with equality
├─ data/             # PollutionDataProvider interface + implementations
│  └─ providers/     # DummyPollutionDataProvider, RemotePollutionDataProvider
├─ providers/        # Riverpod wiring — the DI layer
├─ features/         # Screens — one folder per feature
│  ├─ home/          # AQI hero, forecast, trend, events, freshness
│  ├─ nearby/        # Nearby areas with lower pollution
│  ├─ alerts/        # Alert history with explanations
│  ├─ profile/       # Health context, sensitivity, data management
│  └─ onboarding/    # 5-step wizard
├─ routing/          # go_router + onboarding redirect
├─ theme/            # Tokens, colors, typography, reusable widgets
│  └─ widgets/       # AqiBadge, StatusChip, AppCard, etc.
├─ storage/          # SecureProfileStore, PrefsStore
├─ notifications/    # NotificationService wrapper
└─ mocks/            # Test fixtures
```

### Layer rules

- `domain/` has ZERO Flutter/plugin imports — pure Dart
- `features/` only reads data through Riverpod providers — never
  instantiates providers directly
- `data/` implements the `PollutionDataProvider` interface — swap
  Dummy for Remote by changing ONE Riverpod override
- `providers/` is the ONLY place that wires implementations to
  interfaces

### Data flow

```
PollutionDataProvider (abstract)
  ├─ DummyPollutionDataProvider (deterministic scenarios)
  └─ RemotePollutionDataProvider (future API, Dio)
        │
        ▼
  Riverpod providers (data_providers.dart, home_providers.dart)
        │
        ▼
  ConsumerWidget screens (Home, Nearby, Alerts, Profile)
        │
        ▼
  AlertEngine (pure Dart) ← UserSensitivityProfile
        │
        ▼
  AlertDecision → notification / UI display
```

## Sensitivity model

The app uses CPCB AQI categories. Sensitivity tiers shift which
category triggers attention — they do NOT invent disease-specific
thresholds.

| Tier | Current alert at | Forecast alert at | Rapid rise |
|---|---|---|---|
| Standard | Poor (201+) | Poor | 40 AQI/hr |
| Sensitive | Moderate (101+) | Moderate | 25 AQI/hr |
| High | Satisfactory (51+) | Moderate | 15 AQI/hr |
| Custom | User-defined | User-defined | User-defined |

Health context ONLY suggests a sensitivity tier — the user must always
confirm or override. The app never auto-assigns a tier based on health
context alone.

## Dummy data

Eight deterministic scenarios, anchor-relative (no `DateTime.now()`
in scenario definitions):

1. `cleanStable` — AQI 42, flat
2. `gradualRise` — 52→178 over 12h
3. `rapidSpike` — +45/hr for 3h
4. `approachingPlume` — local moderate, nearby severe event in 75min
5. `severeNow` — AQI 348
6. `recovery` — 220→80
7. `dataUnavailable` — current OK, empty forecast
8. `partialData` — null PM2.5, 6h forecast

## Backend integration

`GridApiPollutionDataProvider` (`lib/data/providers/`) is the app-side
adapter for the platform's grid API. It is selected automatically when
`POLLUTION_API_BASE_URL` is set, with the dummy provider as the fallback
otherwise; debug builds override the selected source with the scenario
simulator only when `USE_DEV_SCENARIO_SIMULATOR=true` is passed. The API
is unauthenticated and the adapter maps:

- `/api/v2/meta` once (cached for 5 minutes) → the published `run_id` every
  other read is pinned to, plus the forecast horizons that run supports
- `/api/v2/grid/current` + `/api/v2/weather`, joined on `h3_cell` (grid
  cells carry no coordinates; weather is the coordinate source), → the
  nearest cell's reading
- `/api/v2/grid/forecast?hours=` — one horizon per call, requested hourly
  up to the publication's 6-hour cap — → `ForecastPoint`s
- `/api/v2/alerts` for the user's cell → `PollutionEvent`s
- the response `generated_at` → `DataFreshness`

Fire/burning reports are the app's only other backend surface, and they are
on v1: `POST /api/v1/reports` and `GET /api/v1/reports`
(`lib/data/reports/fire_report_api.dart`). The v1 grid/weather/cells/alerts
routes are **not** used by this app any more — see the route-to-consumer
table in the repository's `docs/architecture.md`.

PM2.5 is converted to a CPCB AQI with the PM2.5 sub-index
(`lib/domain/pm25_aqi.dart`) — an approximation, since the API reports PM2.5
only. See `test/data/grid_api_provider_test.dart` (in-memory fake client, no
network) for the mapping tests.

## Testing

```
test/
├─ domain/           # Alert engine, models, CPCB classification
├─ data/             # Scenario determinism, provider contract
├─ providers/        # (future) Riverpod overriding tests
└─ features/         # Widget smoke tests
```

- `flutter analyze` — zero issues
- `flutter test` — all tests pass
- No Firebase, no network calls in tests
