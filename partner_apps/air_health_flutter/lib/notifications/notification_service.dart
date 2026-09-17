import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// Result of a notification permission request.
enum NotificationPermissionResult {
  granted,
  denied,
  permanentlyDenied,
}

/// Wraps flutter_local_notifications — the only file that imports the plugin.
///
/// Responsibilities:
/// - Initialise the plugin
/// - Request notification permissions
/// - Show local notifications
/// - Cancel notifications by ID
/// - Cancel all notifications
///
/// Notification text uses [AlertMessage.lockScreenBody] — NEVER the
/// full body, which may contain sensitivity context.
class NotificationService {
  NotificationService({FlutterLocalNotificationsPlugin? plugin})
      : _plugin = plugin ?? FlutterLocalNotificationsPlugin();

  final FlutterLocalNotificationsPlugin _plugin;

  static const _androidChannel = AndroidNotificationChannel(
    'air_health_alerts',
    'Air Quality Alerts',
    description: 'Personalised air quality alerts',
    importance: Importance.high,
  );

  /// Initialise the plugin. Call once at app startup.
  Future<void> initialise() async {
    const androidSettings =
        AndroidInitializationSettings('@mipmap/ic_launcher');
    const initSettings = InitializationSettings(android: androidSettings);
    await _plugin.initialize(initSettings);
  }

  /// Request notification permissions from the user.
  ///
  /// On Android 13+ this triggers the runtime permission dialog.
  /// Returns the permission state.
  Future<NotificationPermissionResult> requestPermissions() async {
    final android = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (android == null) return NotificationPermissionResult.denied;

    final granted = await android.requestNotificationsPermission();
    if (granted == true) return NotificationPermissionResult.granted;
    return NotificationPermissionResult.denied;
  }

  /// Show a local notification.
  ///
  /// [id] is used for deduplication — same id replaces the previous
  /// notification (update-in-place for escalations).
  ///
  /// [title] and [body] must be lock-screen safe — no health context,
  /// no sensitivity info, no diagnosis.
  Future<void> show({
    required int id,
    required String title,
    required String body,
    String? payload,
  }) async {
    final androidDetails = AndroidNotificationDetails(
      _androidChannel.id,
      _androidChannel.name,
      channelDescription: _androidChannel.description,
      importance: Importance.high,
      priority: Priority.high,
      styleInformation: BigTextStyleInformation(body),
    );
    final details = NotificationDetails(android: androidDetails);
    await _plugin.show(id, title, body, details, payload: payload);
  }

  /// Cancel a specific notification by [id].
  Future<void> cancel(int id) async {
    await _plugin.cancel(id);
  }

  /// Cancel all pending notifications.
  Future<void> cancelAll() async {
    await _plugin.cancelAll();
  }
}
