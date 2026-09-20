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

  static const _alertsChannel = AndroidNotificationChannel(
    'air_health_alerts',
    'Air quality alerts',
    description: 'Personalised air-quality advisories for your area.',
    importance: Importance.high,
  );

  /// A separate, louder channel for urgent (Very Poor/Severe) alerts, so the
  /// user can mute routine advisories without silencing genuine emergencies.
  static const _urgentChannel = AndroidNotificationChannel(
    'air_health_urgent',
    'Urgent air-quality warnings',
    description: 'Severe air quality that needs immediate attention.',
    importance: Importance.max,
    playSound: true,
    enableVibration: true,
  );

  /// Initialise the plugin. Call once at app startup.
  ///
  /// [onNotificationTap] receives the tapped notification's payload, so the
  /// app can deep-link (e.g. to the Alerts screen).
  Future<void> initialise({
    void Function(String? payload)? onNotificationTap,
  }) async {
    const androidSettings =
        AndroidInitializationSettings('ic_notification');
    final initSettings = InitializationSettings(android: androidSettings);
    await _plugin.initialize(
      initSettings,
      onDidReceiveNotificationResponse: (response) {
        onNotificationTap?.call(response.payload);
      },
    );
    await _registerChannels();
  }

  /// Create the Android notification channels explicitly so their
  /// user-visible names and descriptions are branded (Android 8+); the
  /// plugin would otherwise create them with only the bare id/name used at
  /// post time.
  Future<void> _registerChannels() async {
    final android = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (android == null) return;
    await android.createNotificationChannel(_alertsChannel);
    await android.createNotificationChannel(_urgentChannel);
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
  ///
  /// Set [urgent] for Very Poor/Severe air quality; it posts on the louder
  /// urgent channel instead of the routine one.
  Future<void> show({
    required int id,
    required String title,
    required String body,
    String? payload,
    bool urgent = false,
  }) async {
    final channel = urgent ? _urgentChannel : _alertsChannel;
    final androidDetails = AndroidNotificationDetails(
      channel.id,
      channel.name,
      channelDescription: channel.description,
      icon: 'ic_notification',
      importance: channel.importance,
      priority: urgent ? Priority.max : Priority.high,
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
