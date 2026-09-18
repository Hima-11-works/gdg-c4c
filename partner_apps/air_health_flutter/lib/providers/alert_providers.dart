import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../domain/alert_engine.dart';
import '../domain/alert_message_service.dart';
import '../domain/models/models.dart';
import '../notifications/alert_notification_dispatcher.dart';
import '../notifications/notification_service.dart';
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

/// Dedup state for the alert engine — persisted across evaluations.
final alertDedupStateProvider = StateProvider<List<DedupEntry>>((ref) {
  return const [];
});

/// Runs the alert engine and dispatches notifications.
///
/// Call this after data refresh (e.g. on a timer or after pipeline run).
/// Returns the engine result for UI display.
final alertEvaluationProvider =
    FutureProvider<AlertEngineResult?>((ref) async {
  final profileAsync = ref.watch(userProfileProvider);
  final profile = profileAsync.valueOrNull;
  if (profile == null) return null;

  final currentAsync = ref.watch(currentAirQualityProvider);
  final forecastAsync = ref.watch(forecastProvider);
  final eventsAsync = ref.watch(pollutionEventsProvider);
  final freshnessAsync = ref.watch(dataFreshnessProvider);

  final current = currentAsync.valueOrNull;
  final forecast = forecastAsync.valueOrNull;
  final events = eventsAsync.valueOrNull;
  final freshness = freshnessAsync.valueOrNull;

  if (current == null || forecast == null || freshness == null) return null;
  final effectiveEvents = events ?? const <PollutionEvent>[];

  // Build the sensitivity profile for the engine, carrying the user's
  // persisted alert preferences (master switch, quiet hours, severity floor,
  // recovery alerts) rather than defaults.
  final sensitivityProfile = UserSensitivityProfile(
    healthContext: profile.healthContext,
    sensitivity: profile.sensitivity,
    customRules: profile.customRules,
    preferences: profile.preferences,
  );

  final engine = ref.read(alertEngineProvider);
  final priorAlerts = ref.read(alertDedupStateProvider);

  final result = engine.evaluate(
    profile: sensitivityProfile,
    current: current,
    forecast: forecast,
    events: effectiveEvents,
    freshness: freshness,
    priorAlerts: priorAlerts,
  );

  // Update dedup state.
  ref.read(alertDedupStateProvider.notifier).state = result.dedupState;

  // Dispatch notifications for new decisions.
  if (result.decisions.isNotEmpty) {
    final dispatcher = ref.read(alertNotificationDispatcherProvider);
    await dispatcher.dispatch(
      decisions: result.decisions,
      sensitivity: profile.sensitivity,
    );

    // Feed alert history for the Alerts screen.
    ref.read(alertHistoryProvider.notifier).addFromDecisions(result.decisions);

    // Resolve any recovery decisions.
    for (final d in result.decisions) {
      if (d.trigger == AlertTrigger.recovery) {
        ref.read(alertHistoryProvider.notifier).resolveByKey(d.dedupKey);
      }
    }
  }

  return result;
});
