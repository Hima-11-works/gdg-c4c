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
| 3 | Permissions in onboarding/Settings (P0-3) | pending |
| 4 | Fix simulator time model (P0-4) | pending |
| 5 | Quiet hours + custom rules + lead time (P1-6/7/8) | pending |
| 6 | Backend integration decision + adapter (P1-9) | pending |
| 7 | iOS platform (P1-10, if in scope) | pending |
| 8 | P2/P3 cleanup | pending |

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
