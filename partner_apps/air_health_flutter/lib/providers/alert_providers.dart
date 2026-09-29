import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../domain/alert_engine.dart';
import '../domain/alert_message_service.dart';
import '../domain/models/models.dart';
import '../notifications/alert_notification_dispatcher.dart';
import '../notifications/forecast_alarm_scheduler.dart';
import '../notifications/notification_service.dart';
import '../notifications/scheduled_alarm.dart';
import '../storage/forecast_alarm_store.dart';
import 'alert_history_provider.dart';
import 'home_providers.dart';
import 'profile_providers.dart';

/// NotificationService instance.
final notificationServiceProvider = Provider<NotificationService>((ref) {
  return NotificationService();
});

/// AlertMessageService instance (pure Dart, no dependencies).
final alertMessageServiceProvider = Provider<AlertMessageService>((ref) {
  return const AlertMessageService();
});

/// AlertEngine instance (pure Dart, no dependencies).
final alertEngineProvider = Provider<AlertEngine>((ref) {
  return const AlertEngine();
});

/// AlertNotificationDispatcher — wires engine → messages → notifications.
final alertNotificationDispatcherProvider =
    Provider<AlertNotificationDispatcher>((ref) {
  return AlertNotificationDispatcher(
    notificationService: ref.read(notificationServiceProvider),
    messageService: ref.read(alertMessageServiceProvider),
  );
});

/// Forecast alarm store (low-level secure storage for alarm metadata).
final forecastAlarmStoreProvider = Provider<ForecastAlarmStore>((ref) {
  return ForecastAlarmStore();
});

/// ForecastAlarmScheduler — registers/cancels OS-level forecast alarms.
final forecastAlarmSchedulerProvider = Provider<ForecastAlarmScheduler>((ref) {
  return ForecastAlarmScheduler(
    notificationService: ref.read(notificationServiceProvider),
    store: ref.read(forecastAlarmStoreProvider),
  );
});

/// Whether the OS currently allows exact alarms (Android 12+ user grant).
///
/// [FutureProvider] so the Settings row reflects the live system state and
/// can be invalidated after the user grants it.
final exactAlarmsProvider = FutureProvider<bool>((ref) {
  return ref.read(notificationServiceProvider).canScheduleExactAlarms();
});

/// The forecast alarms currently scheduled with the OS, for the Settings
/// "Upcoming alarms" row. Read directly from the metadata store — the OS
/// holds the alarms themselves, this is what *we* remember scheduling.
final upcomingAlarmsProvider = FutureProvider<List<ScheduledAlarm>>((ref) {
  return ref.read(forecastAlarmStoreProvider).readAll();
});

/// Dedup state for the alert engine — persisted across evaluations.
final alertDedupStateProvider = StateProvider<List<DedupEntry>>((ref) {
  return const [];
});

/// Orchestrates one refresh + alert-evaluation cycle.
///
/// This replaces the previous side-effecting `alertEvaluationProvider`: a
/// provider build must be pure, and nothing consumed it anyway, so the engine
/// (and therefore notifications and alert history) never ran. The coordinator
/// is invoked by the app shell on a timer, on app resume, and once at startup.
class AlertCoordinator {
  AlertCoordinator(this._ref);

  final Ref _ref;

  /// Refresh the data the engine depends on, then run the engine and act on
  /// its decisions. Returns the engine result, or null when there is no
  /// profile yet (e.g. before onboarding) or nothing to evaluate.
  Future<AlertEngineResult?> refreshAndEvaluate() async {
    // Force fresh reads so a periodic tick actually pulls new data. Riverpod
    // keeps the previous value visible during the refresh, so screens don't
    // flash a loading state.
    _ref.invalidate(currentAirQualityProvider);
    _ref.invalidate(forecastProvider);
    _ref.invalidate(pollutionEventsProvider);
    _ref.invalidate(dataFreshnessProvider);
    _ref.invalidate(nearbyAreasProvider);

    final profile = await _ref.read(userProfileProvider.future);
    if (profile == null) {
      // A missing profile means onboarding or a reset: nothing here can be
      // trusted to represent the user, so any OS-level alarms left over from
      // a previous state must not keep ringing.
      await _ref.read(forecastAlarmSchedulerProvider).cancelAll();
      return null;
    }

    final current = await _ref.read(currentAirQualityProvider.future);
    final forecast = await _ref.read(forecastProvider.future);
    final events = await _ref.read(pollutionEventsProvider.future);
    final freshness = await _ref.read(dataFreshnessProvider.future);

    // Build the sensitivity profile for the engine, carrying the user's
    // persisted alert preferences (master switch, quiet hours, severity floor,
    // recovery alerts) rather than defaults.
    final sensitivityProfile = UserSensitivityProfile(
      healthContext: profile.healthContext,
      sensitivity: profile.sensitivity,
      customRules: profile.customRules,
      preferences: profile.preferences,
    );

    final engine = _ref.read(alertEngineProvider);
    final priorAlerts = _ref.read(alertDedupStateProvider);

    final result = engine.evaluate(
      profile: sensitivityProfile,
      current: current,
      forecast: forecast,
      events: events,
      freshness: freshness,
      priorAlerts: priorAlerts,
    );

    // Persist dedup state for the next run.
    _ref.read(alertDedupStateProvider.notifier).state = result.dedupState;

    if (result.decisions.isNotEmpty) {
      // Dispatch notifications (lock-screen-safe messages only).
      await _ref.read(alertNotificationDispatcherProvider).dispatch(
            decisions: result.decisions,
            sensitivity: profile.sensitivity,
          );

      // Record for the Alerts screen, and resolve any recovery decisions.
      final history = _ref.read(alertHistoryProvider.notifier);
      history.addFromDecisions(result.decisions);
      for (final decision in result.decisions) {
        if (decision.trigger == AlertTrigger.recovery) {
          history.resolveByKey(decision.dedupKey);
        }
      }
    }

    // Drop stale resolved/old records regardless of new decisions.
    _ref.read(alertHistoryProvider.notifier).prune();

    // Keep the OS-level forecast alarms in step with the latest forecast.
    // These ring even when the app is closed, so they are reconciled on
    // every cycle — not only when a decision fires — because a forecast
    // revision, a preference change, or a reboot all need this sync.
    try {
      await _ref.read(forecastAlarmSchedulerProvider).reconcile(
            forecast: forecast,
            rules: SensitivityRules.forProfile(sensitivityProfile),
            preferences: profile.preferences,
            freshness: freshness,
          );
    } catch (_) {
      // Non-fatal: the next cycle retries. The OS alarm from a previous
      // cycle stays pending in the meantime.
    }

    return result;
  }
}

/// App-wide refresh + alert coordinator.
final alertCoordinatorProvider = Provider<AlertCoordinator>((ref) {
  return AlertCoordinator(ref);
});
