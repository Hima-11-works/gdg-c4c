import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest_all.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;

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

  /// Alarm-style channel for scheduled forecast alarms. Uses the device's
  /// ALARM volume stream, so the alarm is audible even when media and
  /// notification volume are muted (the user may still silence it via DND).
  static const _alarmChannel = AndroidNotificationChannel(
    'air_health_alarm',
    'Forecast air-quality alarms',
    description:
        'Rings at predicted air-quality changes, even while the app is closed.',
    importance: Importance.max,
    playSound: true,
    enableVibration: true,
    audioAttributesUsage: AudioAttributesUsage.alarm,
  );

  bool _timezonesInitialised = false;

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
    await android.createNotificationChannel(_alarmChannel);
  }

  /// Load the timezone database once, before any [tz.TZDateTime] is built.
  void _ensureTimezonesLoaded() {
    if (_timezonesInitialised) return;
    tz_data.initializeTimeZones();
    _timezonesInitialised = true;
  }

  /// Whether the OS will honour exact alarms.
  ///
  /// Android 12+ gates exact alarms behind a user grant (SCHEDULE_EXACT_ALARM,
  /// off by default on Android 14+); older Android and iOS have no such
  /// restriction, so they always report `true`.
  Future<bool> canScheduleExactAlarms() async {
    final android = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (android == null) return true;
    return await android.canScheduleExactAlarms() ?? true;
  }

  /// Schedule an alarm-style notification for [fireAt] (an absolute instant).
  ///
  /// Fires even while the app is closed and the screen is off: on Android it
  /// is an allow-while-idle AlarmManager alarm, on iOS a time-interval
  /// trigger — both delivered by the OS, not by this process. Uses the alarm
  /// audio stream so the device's alarm volume applies.
  ///
  /// When exact alarms are not grantable (Android 12+ without the user's
  /// grant) this falls back to an inexact alarm, which may fire up to ~15
  /// minutes late.
  ///
  /// Set [urgent] to add a full-screen intent, so a locked screen lights up
  /// for the alarm.
  ///
  /// Scheduling a notification with an [id] that already has a pending alarm
  /// replaces it — the caller deduplicates by alert key.
  Future<void> scheduleAlarm({
    required int id,
    required DateTime fireAt,
    required String title,
    required String body,
    String? payload,
    bool urgent = false,
  }) async {
    _ensureTimezonesLoaded();
    final exact = await canScheduleExactAlarms();
    await _plugin.zonedSchedule(
      id,
      title,
      body,
      // An absolute instant expressed in UTC; with absoluteTime
      // interpretation the OS fires on the instant itself, so the device's
      // timezone (or a DST change before the alarm) cannot shift it.
      tz.TZDateTime.from(fireAt, tz.getLocation('UTC')),
      NotificationDetails(
        android: AndroidNotificationDetails(
          _alarmChannel.id,
          _alarmChannel.name,
          channelDescription: _alarmChannel.description,
          icon: 'ic_notification',
          importance: Importance.max,
          priority: urgent ? Priority.max : Priority.high,
          audioAttributesUsage: AudioAttributesUsage.alarm,
          // Light the screen even while locked, for urgent alarms.
          fullScreenIntent: urgent,
          styleInformation: BigTextStyleInformation(body),
        ),
      ),
      uiLocalNotificationDateInterpretation:
          UILocalNotificationDateInterpretation.absoluteTime,
      androidScheduleMode: exact
          ? AndroidScheduleMode.exactAllowWhileIdle
          : AndroidScheduleMode.inexactAllowWhileIdle,
      payload: payload,
    );
  }

  /// Cancel a previously [scheduleAlarm]d alarm (or a shown notification)
  /// with [id].
  Future<void> cancelAlarm(int id) => _plugin.cancel(id);

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
