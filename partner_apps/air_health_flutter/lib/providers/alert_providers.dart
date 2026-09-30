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
import 'location_providers.dart';
import 'profile_providers.dart';

import '../services/alert_sound_service.dart';

/// NotificationService instance.
final notificationServiceProvider = Provider<NotificationService>((ref) {
  return NotificationService();
});

/// Current OS notification permission, shown in Settings and refreshed after
/// the user changes the system grant.
final notificationPermissionProvider = FutureProvider<bool>((ref) {
  return ref.read(notificationServiceProvider).canDeliverNotifications();
});

/// AlertSoundService instance.
final alertSoundServiceProvider = Provider<AlertSoundService>((ref) {
  return AlertSoundService();
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
    soundService: ref.read(alertSoundServiceProvider),
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

/// Last real device position used for alert evaluation. A meaningful move
/// starts a new location-scoped dedup window.
final alertEvaluationLocationProvider = StateProvider<LocationPoint?>((ref) {
  return null;
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
    _ref.invalidate(currentLocationProvider);
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

    final location = await _ref.read(currentLocationProvider.future);
    if (location.isFallback) {
      // A city demo fallback is not the user's position. Do not deliver a
      // location-specific AQI alert or leave an old forecast alarm active.
      if (_ref.read(alertEvaluationLocationProvider) != null) {
        _resetLocationScopedAlerts();
      }
      _ref.read(alertEvaluationLocationProvider.notifier).state = null;
      await _ref.read(forecastAlarmSchedulerProvider).cancelAll();
      return null;
    }
    final previousLocation = _ref.read(alertEvaluationLocationProvider);
    if (previousLocation != null &&
        previousLocation.distanceTo(location) >= 0.5) {
      _resetLocationScopedAlerts();
    }
    _ref.read(alertEvaluationLocationProvider.notifier).state = location;

    late final AirQualityReading current;
    late final List<ForecastPoint> forecast;
    late final List<PollutionEvent> events;
    late final DataFreshness freshness;
    try {
      current = await _ref.read(currentAirQualityProvider.future);
      forecast = await _ref.read(forecastProvider.future);
      events = await _ref.read(pollutionEventsProvider.future);
      freshness = await _ref.read(dataFreshnessProvider.future);
    } catch (_) {
      // Do not leave a previously scheduled alarm to fire against a failed
      // or unavailable refresh. Screens surface the underlying data error.
      try {
        await _ref.read(forecastAlarmSchedulerProvider).cancelAll();
      } catch (_) {}
      return null;
    }

    // Build the sensitivity profile for the engine, carrying the user's
    // persisted alert preferences (master switch, quiet hours, severity floor,
    // recovery alerts) rather than defaults.
    final sensitivityProfile = UserSensitivityProfile(
      healthContext: profile.healthContext,
      sensitivity: profile.sensitivity,
      customRules: profile.customRules,
      preferences: profile.preferences,
      diseaseSeverity: profile.diseaseSeverity,
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

    if (result.decisions.isNotEmpty) {
      // Dispatch notifications (lock-screen-safe messages only).
      final delivered = await _ref
          .read(alertNotificationDispatcherProvider)
          .dispatch(
            decisions: result.decisions,
            sensitivity: profile.sensitivity,
            healthContext: profile.healthContext,
            diseaseSeverity: profile.diseaseSeverity,
          );

      // Keep a decision eligible while OS notifications are denied. The alert
      // history remains visible in-app, and granting permission later can
      // deliver the still-relevant warning rather than losing it to dedup.
      if (delivered > 0) {
        _ref.read(alertDedupStateProvider.notifier).state = result.dedupState;
      }

      // Record for the Alerts screen, and resolve any recovery decisions.
      final history = _ref.read(alertHistoryProvider.notifier);
      history.addFromDecisions(result.decisions);
      for (final decision in result.decisions) {
        if (decision.trigger == AlertTrigger.recovery) {
          history.resolveByKey(decision.dedupKey);
        }
      }
    } else {
      _ref.read(alertDedupStateProvider.notifier).state = result.dedupState;
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

  void _resetLocationScopedAlerts() {
    _ref.read(alertDedupStateProvider.notifier).state = const [];
    _ref.read(alertHistoryProvider.notifier).resolveAllActive();
  }
}

/// App-wide refresh + alert coordinator.
final alertCoordinatorProvider = Provider<AlertCoordinator>((ref) {
  return AlertCoordinator(ref);
});
