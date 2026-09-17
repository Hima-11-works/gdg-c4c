/// User notification and alert preferences — separate from health context.
///
/// Controls *how* alerts are delivered, not *when* they trigger.
class UserAlertPreferences {
  const UserAlertPreferences({
    this.alertsEnabled = true,
    this.quietHoursStart,
    this.quietHoursEnd,
    this.minimumSeverity = AlertSeverity.info,
    this.recoveryAlertsEnabled = true,
  });

  /// Master switch for all alerts.
  final bool alertsEnabled;

  /// Start of quiet hours (no notifications), or null if disabled.
  final DateTime? quietHoursStart;

  /// End of quiet hours, or null if disabled.
  final DateTime? quietHoursEnd;

  /// Minimum severity level that triggers a notification.
  final AlertSeverity minimumSeverity;

  /// Whether to send recovery notifications (AQI improving after alert).
  final bool recoveryAlertsEnabled;

  bool get hasQuietHours => quietHoursStart != null && quietHoursEnd != null;

  UserAlertPreferences copyWith({
    bool? alertsEnabled,
    DateTime? quietHoursStart,
    DateTime? quietHoursEnd,
    AlertSeverity? minimumSeverity,
    bool? recoveryAlertsEnabled,
  }) {
    return UserAlertPreferences(
      alertsEnabled: alertsEnabled ?? this.alertsEnabled,
      quietHoursStart: quietHoursStart ?? this.quietHoursStart,
      quietHoursEnd: quietHoursEnd ?? this.quietHoursEnd,
      minimumSeverity: minimumSeverity ?? this.minimumSeverity,
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
          recoveryAlertsEnabled == other.recoveryAlertsEnabled;

  @override
  int get hashCode => Object.hash(
        alertsEnabled,
        quietHoursStart,
        quietHoursEnd,
        minimumSeverity,
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
