/// User notification and alert preferences — separate from health context.
///
/// Controls how and when alerts are delivered: master switch, quiet hours,
/// minimum severity, forecast lead time and recovery notifications.
class UserAlertPreferences {
  const UserAlertPreferences({
    this.alertsEnabled = true,
    this.quietHoursStart,
    this.quietHoursEnd,
    this.minimumSeverity = AlertSeverity.info,
    this.leadTime,
    this.recoveryAlertsEnabled = true,
  });

  /// Master switch for all alerts.
  final bool alertsEnabled;

  /// Start of quiet hours (no notifications), or null if disabled.
  ///
  /// Only the time-of-day component is meaningful; quiet hours wrap past
  /// midnight when [quietHoursStart] is later than [quietHoursEnd].
  final DateTime? quietHoursStart;

  /// End of quiet hours, or null if disabled.
  final DateTime? quietHoursEnd;

  /// Minimum severity level that triggers a notification.
  final AlertSeverity minimumSeverity;

  /// How far ahead a forecast alert may look, or null to use the default for
  /// the current sensitivity tier. Overrides
  /// `SensitivityRules.leadTimePreference`.
  final Duration? leadTime;

  /// Whether to send recovery notifications (AQI improving after alert).
  final bool recoveryAlertsEnabled;

  bool get hasQuietHours => quietHoursStart != null && quietHoursEnd != null;

  /// Whether [time] falls inside the configured quiet-hours window.
  ///
  /// Compares time-of-day only, so it is independent of the stored date.
  /// Handles windows that wrap past midnight (e.g. 22:00–07:00).
  bool isWithinQuietHours(DateTime time) {
    if (!hasQuietHours) return false;
    final start = quietHoursStart!.hour * 60 + quietHoursStart!.minute;
    final end = quietHoursEnd!.hour * 60 + quietHoursEnd!.minute;
    if (start == end) return false;
    final t = time.hour * 60 + time.minute;
    return start < end ? (t >= start && t < end) : (t >= start || t < end);
  }

  UserAlertPreferences copyWith({
    bool? alertsEnabled,
    DateTime? quietHoursStart,
    DateTime? quietHoursEnd,
    bool clearQuietHours = false,
    AlertSeverity? minimumSeverity,
    Duration? leadTime,
    bool clearLeadTime = false,
    bool? recoveryAlertsEnabled,
  }) {
    return UserAlertPreferences(
      alertsEnabled: alertsEnabled ?? this.alertsEnabled,
      quietHoursStart: clearQuietHours
          ? null
          : (quietHoursStart ?? this.quietHoursStart),
      quietHoursEnd:
          clearQuietHours ? null : (quietHoursEnd ?? this.quietHoursEnd),
      minimumSeverity: minimumSeverity ?? this.minimumSeverity,
      leadTime: clearLeadTime ? null : (leadTime ?? this.leadTime),
      recoveryAlertsEnabled:
          recoveryAlertsEnabled ?? this.recoveryAlertsEnabled,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is UserAlertPreferences &&
          alertsEnabled == other.alertsEnabled &&
          quietHoursStart == other.quietHoursStart &&
          quietHoursEnd == other.quietHoursEnd &&
          minimumSeverity == other.minimumSeverity &&
          leadTime == other.leadTime &&
          recoveryAlertsEnabled == other.recoveryAlertsEnabled;

  @override
  int get hashCode => Object.hash(
        alertsEnabled,
        quietHoursStart,
        quietHoursEnd,
        minimumSeverity,
        leadTime,
        recoveryAlertsEnabled,
      );

  @override
  String toString() =>
      'UserAlertPreferences(enabled=$alertsEnabled, min=${minimumSeverity.name})';
}

/// Alert severity levels — not tied to a medical condition.
enum AlertSeverity {
  info,
  advisory,
  warning,
  urgent;

  bool operator >=(AlertSeverity other) => index >= other.index;
  bool operator <=(AlertSeverity other) => index <= other.index;
  bool operator >(AlertSeverity other) => index > other.index;
  bool operator <(AlertSeverity other) => index < other.index;
}
