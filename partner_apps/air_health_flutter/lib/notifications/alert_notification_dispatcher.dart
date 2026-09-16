import '../domain/alert_engine.dart';
import '../domain/alert_message_service.dart';
import '../domain/models/models.dart';
import 'notification_service.dart';

/// Wires [AlertEngine] output to [NotificationService].
///
/// Takes engine decisions, builds lock-screen-safe messages via
/// [AlertMessageService], and dispatches notifications. Deduplicates
/// by [AlertDecision.dedupKey] — same key replaces the notification
/// (update-in-place for escalations).
///
/// This is the ONLY place that connects the alert domain to the
/// notification plugin. Screens never call NotificationService directly
/// for alerts.
class AlertNotificationDispatcher {
  AlertNotificationDispatcher({
    required NotificationService notificationService,
    required AlertMessageService messageService,
  })  : _notificationService = notificationService,
        _messageService = messageService;

  final NotificationService _notificationService;
  final AlertMessageService _messageService;

  /// Track which dedupKeys have been notified this session, so we can
  /// cancel stale ones.
  final Map<String, int> _activeNotificationIds = {};

  /// Dispatch notifications for the given [decisions].
  ///
  /// [sensitivity] is used to build the in-app message; the notification
  /// body always uses the lock-screen-safe variant.
  ///
  /// Returns the number of notifications actually shown.
  Future<int> dispatch({
    required List<AlertDecision> decisions,
    required AlertSensitivity sensitivity,
  }) async {
    int count = 0;
    final seenKeys = <String>{};

    for (final decision in decisions) {
      if (!decision.shouldAlert) continue;
      if (seenKeys.contains(decision.dedupKey)) continue;
      seenKeys.add(decision.dedupKey);

      final message = _messageService.buildMessage(
        decision: decision,
        sensitivity: sensitivity,
      );

      // Use a deterministic ID from the dedupKey so the same alert
      // replaces (escalates) rather than duplicates.
      final notificationId = _idFromKey(decision.dedupKey);

      await _notificationService.show(
        id: notificationId,
        title: message.title,
        body: message.lockScreenBody, // NEVER the full body
      );

      _activeNotificationIds[decision.dedupKey] = notificationId;
      count++;
    }

    return count;
  }

  /// Cancel all active alert notifications.
  Future<void> cancelAll() async {
    await _notificationService.cancelAll();
    _activeNotificationIds.clear();
  }

  /// Cancel a specific alert notification by dedupKey.
  Future<void> cancelByKey(String dedupKey) async {
    final id = _activeNotificationIds.remove(dedupKey);
    if (id != null) {
      await _notificationService.cancel(id);
    }
  }

  /// Deterministic notification ID from a dedupKey string.
  ///
  /// Same key always produces the same ID, so an escalation updates
  /// the existing notification rather than creating a new one.
  static int _idFromKey(String key) {
    // Simple hash — good enough for notification IDs, which only need
    // to be unique within this app's notification channel.
    var hash = 0;
    for (var i = 0; i < key.length; i++) {
      hash = (hash * 31 + key.codeUnitAt(i)) & 0x7FFFFFFF;
    }
    return hash;
  }
}
