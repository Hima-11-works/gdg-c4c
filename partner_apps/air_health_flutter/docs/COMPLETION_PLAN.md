# Flutter App — Completion Plan

Action plan for finishing `air_health_flutter` and fixing the parts that are
wired only in appearance. Derived from a read-through of `lib/` and `test/`.

**Current state:** the app compiles and runs on the deterministic dummy
provider. Layering (`domain` pure / `data` providers / `providers` DI /
`features` screens) is clean, and the alert engine + models are complete and
tested. All gaps below are wiring/integration, not half-written domain logic.

> Verification note: this environment has **no Dart/Flutter SDK**, so
> `flutter analyze` / `flutter test` could not be run here. Those are the
> first gate on any machine that has Flutter.

---

## P0 — Broken (exists but never runs)

1. **Alert pipeline never invoked.** `alertEvaluationProvider`
   (`lib/providers/alert_providers.dart`) has no consumer. So
   `alertHistoryProvider` stays empty (Alerts screen always empty) and
   `AlertNotificationDispatcher` is never called (no notifications ever
   fire). `AlertHistoryNotifier.prune()` is dead. Also a Riverpod
   anti-pattern: side effects inside a `FutureProvider`.
   *Fix:* trigger evaluation at app scope (an app-level coordinator /
   `ref.listen`), after each data refresh and on a cadence; call `prune()`.
2. **No refresh cadence.** Providers are one-shot `FutureProvider`s; the only
   refresh is pull-to-refresh/retry. Alerts would only evaluate on user
   action. *Fix:* periodic refresh + re-evaluate (e.g. every 15 min and on
   resume). Background notifications while the app is closed need
   `WorkManager` — separate scope decision.
3. **Permissions never requested.** `NotificationService.requestPermissions()`
   has no caller; onboarding's Location step never calls
   `requestAndLocate()`. *Fix:* request in onboarding + Settings.
4. **Dev simulator can't advance the scenario.** `simulatorDataProvider`
   sets `anchor = simulatorNow`; scenario data is anchor-relative, so
   stepping time rebuilds the same curve. Data providers also `ref.read`
   the provider and only `watch currentLocationProvider`, so scenario/time
   changes never invalidate them. *Fix:* fixed anchor + simulated "now";
   make providers `watch` simulator state.
5. **Settings toggles are no-ops; preferences not persisted.**
   `profile_screen.dart` hardcodes `true` and uses `onChanged: (v) {}` /
   `onTap: () {}`; `UserProfile` has no `preferences` field; and
   `alertEvaluationProvider` hardcodes `const UserAlertPreferences()`.
   *Fix:* persist preferences on `UserProfile` and wire the switches.

## P1 — Unimplemented / stubs

6. **Quiet hours** — stub (`profile_screen.dart` "coming soon"). Model fields
   exist (`quietHoursStart/End`); needs picker, persistence, suppression.
7. **Custom sensitivity editor** — advertised ("Set your own thresholds"),
   `CustomSensitivityRules` + `SensitivityRules.forProfile` support it, no UI.
8. **Forecast warning lead time** — no-op tile; maps to
   `SensitivityRules.leadTimePreference`.
9. **Real backend integration (largest).** `RemotePollutionDataProvider`
   targets `/air-quality/current`, `/air-quality/forecast?hours=`,
   `/nearby`, `/events`, `/data-freshness` with `aqi`/`category` DTOs. The
   backend serves `/api/v1/grid/current`, `/api/v1/grid/forecast?minutes=`,
   `/api/v1/weather`, `/api/v1/alerts` (PM2.5/PDI/confidence, unauthenticated).
   *Decide:* (a) backend "partner" adapter matching this
   `PollutionDataProvider` shape, or (b) app-side adapter mapping the grid
   API (lat/lon → H3 cell, PM2.5 → CPCB AQI, nearby from the 1-ring cells,
   events from `/alerts`, freshness from `generated_at`).
10. **iOS** has no platform folder (Android + web only). Add if in scope.

## P2 — Data / UX bugs

- **Nearby uses the local forecast for every area** (`scenario_data`), so
  "Forecast avg" / "Cleaner soon" / sorting are misleading.
- **Alerts screen bucket gap:** unresolved records older than 24h match none
  of Active/Recent/Resolved and silently disappear.
- **Scenario inconsistencies:** `cleanStable` area AQI 38 vs forecast 40;
  `gradualRise` comment vs actual category-crossing hour.
- **Chart event marker** uses wall-clock `DateTime.now()` not the injected
  clock, so it misplaces under the simulator.

## P3 — Cleanup

- Dead providers: `activeScenarioProvider`, `simulatorAutoAdvanceProvider`,
  unused `prune()`.
- Unused deps: `riverpod_annotation`, `riverpod_generator`, `build_runner`
  (no codegen used).
- Onboarding doc comment says "6 steps" vs `_totalSteps = 5`.
- `PrefsStore` `units`/`darkMode`/`reducedMotion` have no UI.

---

## Execution order & progress

| # | Step | Status |
|---|---|---|
| 1 | Preferences plumbing (`UserProfile.preferences`, persistence, notifier, engine) + wire Settings toggles (P0-5) | **done** |
| 2 | Alert coordinator + refresh cadence + `prune()` (P0-1, P0-2) | **done** |
| 3 | Permissions in onboarding/Settings (P0-3) | **done** |
| 4 | Fix simulator time model (P0-4) | **done** |
| 5 | Quiet hours + custom rules + lead time (P1-6/7/8) | **done** |
| 6 | Backend integration decision + adapter (P1-9) | **done** |
| 7 | iOS platform (P1-10, if in scope) | pending |
| 8 | P2/P3 cleanup | **done** |

**Verification per step:** `flutter analyze` + `flutter test` (15 test files,
incl. `test/acceptance/acceptance_test.dart`). Add tests for each new piece
(coordinator, preference persistence, quiet-hours suppression).

### Step 2 notes

- `AlertCoordinator` (`lib/providers/alert_providers.dart`) refreshes the data
  providers, runs the engine, dispatches notifications, updates alert history,
  and calls `prune()`.
- `AirHealthApp` (`lib/app/app.dart`) is now stateful and drives the cadence:
  once at startup, on app resume, and every `alertRefreshInterval` (15 min).
- The old side-effecting, unconsumed `alertEvaluationProvider` was removed.
- Test: `test/providers/alert_coordinator_test.dart` covers the no-profile
  guard (returns null, no side effects). A full evaluate-and-dispatch test
  still needs mock data providers (follow-up).

### Step 3 notes

- Onboarding: the Location step now has an **Allow location** button
  (`LocationService.requestAndLocate()`, with a result message and
  `currentLocationProvider` invalidation on success); the Notifications step
  has an **Enable notifications** button
  (`NotificationService.requestPermissions()`). Both steps stay skippable.
- Settings: the **Location permission** tile now checks status and either
  requests, opens app settings (permanently denied) or opens location
  services (disabled); the **Notification permission** tile requests
  notifications permission. The **Current location** tile shows the resolved
  location label instead of a hardcoded one.
- Remaining no-op tile: **Forecast warning lead time** (P1-8, deferred).

### Step 4 notes

- Root cause: `simulatorDataProvider` set the scenario's `anchor` to the
  simulated clock, so every scenario was always read "as of t0" and time
  never advanced the situation. Separately, the data providers used
  `ref.read(pollutionDataProvider)`, so a scenario/time change never
  invalidated them.
- `scenario_data.dart`: added `buildScenarioSnapshot(scenario, anchor, now)`
  + `_ScenarioTimeline`. A scenario's reading is treated as t0 and its hourly
  forecast as the timeline; the current reading is sampled at `now` and the
  forecast is re-anchored to `now`. Values interpolate (pm25/confidence too)
  and clamp past the last authored hour; `now == anchor` returns
  `buildScenario` unchanged (so existing tests keep authored values).
- `dummy_pollution_data_provider.dart`: new optional `now` (defaults to
  `anchor`, then `DateTime.now()`); builds its `ScenarioData` from
  `buildScenarioSnapshot`, and filters the forecast by `f.at - now <= horizon`.
- `dev_scenario_simulator.dart`: new fixed `simulatorAnchorProvider`
  (`2026-09-17 10:00`); `simulatorNowProvider` = anchor + offset;
  `simulatorDataProvider` passes both.
- `home_providers.dart`: all five providers now `ref.watch` the data provider
  (were `ref.read`), so sim changes refetch.
- `dev_simulator_panel.dart`: the demo-sequence button now sets the playing
  flag so the play/pause icon is accurate.
- Tests: `test/data/dummy_scenarios_test.dart` gains a `time-shifted snapshot`
  group (advances the reading along the timeline; `now == anchor` is
  unchanged; provider reports the advanced value). Existing dummy-provider
  tests pass `anchor` without `now`, so they keep authored values.
- NOTE: no Flutter/Dart SDK here — changes are statically verified only
  (brace/paren balance, call-site grep). Run `flutter analyze && flutter test`
  on a Flutter machine before relying on them.

### Step 5 notes

- **Quiet hours (P1-6):** the `_TimeRangeTile` is now wired to
  `UserAlertPreferences.quietHoursStart/End`; tapping it opens
  `_QuietHoursSheet` (enable switch + from/to `showTimePicker`s, Save).
  `UserAlertPreferences.isWithinQuietHours(time)` compares time-of-day and
  handles windows that wrap past midnight. `copyWith(clearQuietHours: true)`
  turns them off.
  - *Semantics:* quiet hours suppress non-urgent alerts only — `urgent`
    (Very Poor / Severe) air quality still gets through. The tile subtitle
    says so.
  - Suppression is applied to candidates **before** dedup, so a suppressed
    alert is not recorded as sent and can fire once the window closes.
- **Minimum severity:** the previously-unused `minimumSeverity` preference is
  now enforced by the engine (same pre-dedup filter).
- **Custom sensitivity (P1-7):** choosing *Custom* in the sensitivity sheet
  now opens `_CustomRulesSheet` — sliders for current-AQI warning,
  forecast-AQI warning and rapid-rise, each previewing its CPCB category —
  and saves `CustomSensitivityRules` alongside `sensitivity: custom`.
  `SecureProfileStore` now actually persists `customRules` (it previously
  dropped them).
- **Lead time (P1-8):** `UserAlertPreferences.leadTime` (nullable) overrides
  `SensitivityRules.leadTimePreference`; the Settings tile shows the effective
  value and opens `_LeadTimePicker` (1/2/3/6/12 h). Persisted as
  `leadTimeMinutes`.
- Tests: `profile_models_test.dart` (quiet-hours windows, clears, lead time),
  `alert_engine_test.dart` (quiet-hours suppression incl. urgent pass-through
  and no-record, minimum severity, lead-time override), and new
  `test/storage/secure_profile_store_test.dart` (round-trip + legacy payload).
- Same static-verification caveat as step 4 (no SDK here).

### Step 6 notes

- **Decision:** app-side adapter, unauthenticated (chosen over a backend
  partner endpoint). The merged backend is untouched.
- `lib/data/grid/grid_api.dart`: DTOs + `GridApiClient` interface +
  `DioGridApiClient` for `/api/v1/grid/current`, `/grid/forecast`,
  `/weather`, `/alerts`, plus `GeoBounds`.
- `lib/domain/pm25_aqi.dart`: PM2.5 → CPCB AQI via the CPCB PM2.5
  sub-index (piecewise-linear, clamped to 0–500). Approximation only.
- `lib/data/providers/grid_api_pollution_data_provider.dart`: implements
  `PollutionDataProvider`. Grid cells have no coordinates, so it joins
  `/weather` (lat/lon) with `/grid/current` (PM2.5) on `h3_cell` and takes
  the nearest cell; forecast series are assembled from hourly
  `/grid/forecast` calls (one horizon per call, capped at 360 min); nearby
  areas are the nearest other cells in a larger box; events are `/alerts`
  for the user's cell; freshness comes from `generated_at`.
- `lib/providers/data_providers.dart`: `pollutionDataProvider` uses the
  adapter when `POLLUTION_API_BASE_URL` is set, else the dummy provider;
  debug's `devProviderOverrides` still swaps in the simulator. `ApiConfig`
  no longer requires an API key; `createPollutionDio` omits the
  Authorization header when none is set.
- Tests: `test/domain/pm25_aqi_test.dart`, `test/data/grid_api_provider_test.dart`
  (in-memory fake client — no network).
- Same static-verification caveat (no SDK here); the mapping is unit-tested
  but no live-backend round-trip was run.
- Note: `RemotePollutionDataProvider` + `lib/data/dto/dto.dart` now have no
  callers (the old bespoke contract) — candidate for P3 removal.

### Step 8 notes

P2:
- Nearby areas no longer reuse the local forecast: `cleanStable` (Cuttack) and
  `approachingPlume` (Industrial Belt) each build their own series consistent
  with their `aqiNow`.
- Alerts screen bucket gap closed: `AlertRecord.isStale` (unresolved, >24h)
  plus an "Older" section, so those records no longer disappear.
- `gradualRise` comment corrected (52 → ~170, crossing Moderate at ~6h).
- The forecast chart's event marker (and the event card's lead-time chip) now
  take a `now` reference — the current reading's `recordedAt` — instead of
  the wall clock, so they stay correct under the simulator.

P3:
- Removed dead `activeScenarioProvider` and `simulatorAutoAdvanceProvider`.
- `prune()` is NOT dead — it's called by `AlertCoordinator`; left in place.
- Removed unused codegen deps (`riverpod_annotation`, `riverpod_generator`,
  `build_runner`); there are no `.g.dart` files.
- Onboarding doc comment now lists 5 steps (matching `_totalSteps`).
- Removed the unused `PrefsStore` UI-pref accessors (units/dark mode/reduced
  motion); `resetAll()` still clears those legacy keys defensively.
- Tests updated/added: `prefs_store_test.dart`, `alert_record_test.dart`,
  `dummy_scenarios_test.dart` (nearby-forecast regression).
- Same static-verification caveat (no SDK here).
