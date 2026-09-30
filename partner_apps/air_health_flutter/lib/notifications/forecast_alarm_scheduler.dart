import '../domain/alert_engine.dart';
import '../domain/models/models.dart';
import '../storage/forecast_alarm_store.dart';
import 'notification_service.dart';
import 'scheduled_alarm.dart';

/// Schedules OS-level alarms for forecasted air-quality changes.
///
/// Unlike the in-app alert evaluation (which only runs while this process is
/// alive), alarms are registered with the OS at a specific instant, so they
/// ring even when the app has been closed and the screen is off — the alarm
/// fires from the OS's alarm scheduler, not from a running Dart isolate.
///
/// [reconcile] is idempotent: each call recomputes the *desired* alarm set
/// from the latest forecast and syncs the scheduled set to it — cancelling
/// alarms whose crossing disappeared or moved, scheduling new ones, and
/// dropping ones that already fired. Alarms are keyed by the crossing they
/// represent (see [keyFor]), so the same alarm is never scheduled twice.
class ForecastAlarmScheduler {
  ForecastAlarmScheduler({
    required NotificationService notificationService,
    required ForecastAlarmStore store,
  })  : _notificationService = notificationService,
        _store = store;

  final NotificationService _notificationService;
  final ForecastAlarmStore _store;

  /// Forecast look-ahead beyond which no alarm is scheduled. The grid API's
  /// own forecast horizon is capped at 6 hours, so nothing beyond that is
  /// real data.
  static const maxHorizon = Duration(hours: 6);

  /// Most upcoming alarms to keep. The nearest crossings are the useful ones;
  /// Android also caps an app's pending intents.
  static const maxScheduledAlarms = 3;

  /// Alarms are only worth scheduling if they ring at least this far in the
  /// future — a crossing closer than this is imminent, and the in-app
  /// heads-up notification (evaluated on the same cycle) covers it.
  static const minLeadTime = Duration(minutes: 2);

  /// Stable dedup key for a forecast crossing. Two evaluations of the same
  /// crossing produce the same key, so its alarm is scheduled once and
  /// re-scheduled in place when the forecast moves.
  static String keyFor(CpcbCategory category, DateTime at) =>
      'alarm_forecast_${category.name}_'
      '${at.year}${_two(at.month)}${_two(at.day)}_${_two(at.hour)}${_two(at.minute)}';

  /// Deterministic notification id for an alarm key.
  static int idFor(String key) {
    var hash = 0;
    for (var i = 0; i < key.length; i++) {
      hash = (hash * 31 + key.codeUnitAt(i)) & 0x7FFFFFFF;
    }
    // Keep alarm ids in a distinct range from ad-hoc notification ids so a
    // cancelAlarm can never cancel an unrelated immediate notification.
    return 0x40000000 | hash;
  }

  static String _two(int value) => value.toString().padLeft(2, '0');

  /// The alarm set the current forecast warrants, ignoring OS state.
  ///
  /// Pure and deterministic — the unit tests exercise this directly.
  static List<ScheduledAlarm> desiredAlarms({
    required List<ForecastPoint> forecast,
    required SensitivityRules rules,
    required UserAlertPreferences preferences,
    required DataFreshness freshness,
    required DateTime now,
  }) {
    if (!preferences.alertsEnabled || !preferences.forecastAlarmsEnabled) {
      return const [];
    }
    // Never alarm off stale data — a stale forecast is not a prediction.
    if (freshness.isStaleAt(now)) return const [];

    final candidates = <ScheduledAlarm>[];
    for (final point in forecast) {
      if (point.category.index < rules.forecastCategory.index) continue;
      if (point.confidence < rules.minForecastConfidence) continue;

      final lead = point.at.difference(now);
      if (lead < minLeadTime) continue;
      if (lead > maxHorizon) continue;

      final severity = AlertEngine.severityForCategory(point.category);
      if (severity < preferences.minimumSeverity) continue;

      final fireAt = point.at.subtract(preferences.alarmLead);
      if (fireAt.isBefore(now.add(minLeadTime))) continue;

      candidates.add(ScheduledAlarm(
        id: idFor(keyFor(point.category, point.at)),
        key: keyFor(point.category, point.at),
        fireAt: fireAt,
        severity: severity,
        categoryLabel: point.category.label,
        predictedAqi: point.aqiCpcb,
      ));
    }

    candidates.sort((a, b) => a.fireAt.compareTo(b.fireAt));
    return candidates.take(maxScheduledAlarms).toList(growable: false);
  }

  /// Align the scheduled alarms with the desired set.
  ///
  /// Cancels alarms no longer warranted (crossing gone, moved past the
  /// horizon, user switched alarms off), re-schedules ones whose time moved,
  /// and schedules brand-new ones. Returns the alarms that are now scheduled.
  Future<List<ScheduledAlarm>> reconcile({
    required List<ForecastPoint> forecast,
    required SensitivityRules rules,
    required UserAlertPreferences preferences,
    required DataFreshness freshness,
    DateTime? now,
  }) async {
    final effectiveNow = now ?? DateTime.now();
    if (!await _notificationService.canDeliverNotifications()) {
      await cancelAll();
      return const [];
    }
    final desired = desiredAlarms(
      forecast: forecast,
      rules: rules,
      preferences: preferences,
      freshness: freshness,
      now: effectiveNow,
    );
    final desiredByKey = {for (final a in desired) a.key: a};

    final current = (await _store.readAll())
        .where((a) => a.fireAt.isAfter(effectiveNow))
        .toList();

    final scheduled = <ScheduledAlarm>[];

    for (final existing in current) {
      final wanted = desiredByKey[existing.key];
      if (wanted == null) {
        // Crossing gone (forecast improved, or a different threshold now).
        await _notificationService.cancelAlarm(existing.id);
      } else if (wanted.fireAt == existing.fireAt) {
        // Unchanged — the OS alarm for this id/time is already registered,
        // and it survives both a killed app and a reboot.
        scheduled.add(existing);
        desiredByKey.remove(existing.key);
      } else {
        // The crossing moved: drop the old pending alarm; the new time is
        // scheduled below under the same id.
        await _notificationService.cancelAlarm(existing.id);
      }
    }

    // Everything still in desiredByKey is new or has moved.
    for (final alarm in desired) {
      if (!desiredByKey.containsKey(alarm.key)) continue;
      desiredByKey.remove(alarm.key);
      await _notificationService.scheduleAlarm(
        id: alarm.id,
        fireAt: alarm.fireAt,
        title: _title(alarm),
        body: _body(alarm, effectiveNow),
        payload: 'alerts',
        urgent: alarm.severity == AlertSeverity.urgent,
      );
      scheduled.add(alarm);
    }

    // Keep only what is scheduled now, oldest first.
    scheduled.sort((a, b) => a.fireAt.compareTo(b.fireAt));
    await _store.writeAll(scheduled);
    return scheduled;
  }

  /// Cancel every scheduled alarm (used by "Reset app" and the alarm toggle
  /// turning off).
  Future<void> cancelAll() async {
    for (final alarm in await _store.readAll()) {
      await _notificationService.cancelAlarm(alarm.id);
    }
    await _store.deleteAll();
  }

  String _title(ScheduledAlarm alarm) {
    return switch (alarm.severity) {
      AlertSeverity.urgent =>
        'Severe air quality expected: ${alarm.categoryLabel}',
      _ => 'Air quality change expected: ${alarm.categoryLabel}',
    };
  }

  String _body(ScheduledAlarm alarm, DateTime now) {
    final lead = alarm.fireAt.difference(now);
    final when = _formatWhen(lead);
    return '$when, air quality is forecast to reach '
        '${alarm.categoryLabel} (AQI ${alarm.predictedAqi}).';
  }

  static String _formatWhen(Duration lead) {
    if (lead.inHours >= 1) {
      final minutes = lead.inMinutes % 60;
      return minutes == 0
          ? 'In ${lead.inHours}h'
          : 'In ${lead.inHours}h ${minutes}m';
    }
    return 'In ${lead.inMinutes}m';
  }
}
