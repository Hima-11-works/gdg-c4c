import '../domain/models/models.dart';

/// A forecast alarm that is (or was) scheduled with the OS.
///
/// Alarms are keyed by the alert they represent (see
/// [ForecastAlarmScheduler.keyFor]) and re-scheduled in place when the
/// forecast moves, so the same alarm never rings twice.
class ScheduledAlarm {
  const ScheduledAlarm({
    required this.id,
    required this.key,
    required this.fireAt,
    required this.severity,
    required this.categoryLabel,
    required this.predictedAqi,
  });

  /// The notification id the alarm was scheduled under. Stable per [key],
  /// so re-scheduling replaces the pending alarm instead of stacking.
  final int id;

  /// Deduplication key for the underlying forecast crossing.
  final String key;

  /// The absolute instant the alarm rings.
  final DateTime fireAt;

  final AlertSeverity severity;

  /// Human-readable category for the alarm text (e.g. "Poor").
  final String categoryLabel;

  /// Predicted AQI at the crossing.
  final int predictedAqi;

  ScheduledAlarm copyWith({DateTime? fireAt}) => ScheduledAlarm(
        id: id,
        key: key,
        fireAt: fireAt ?? this.fireAt,
        severity: severity,
        categoryLabel: categoryLabel,
        predictedAqi: predictedAqi,
      );

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is ScheduledAlarm &&
          id == other.id &&
          key == other.key &&
          fireAt == other.fireAt &&
          severity == other.severity &&
          categoryLabel == other.categoryLabel &&
          predictedAqi == other.predictedAqi;

  @override
  int get hashCode => Object.hash(
        id,
        key,
        fireAt,
        severity,
        categoryLabel,
        predictedAqi,
      );

  @override
  String toString() =>
      'ScheduledAlarm($categoryLabel at $fireAt, sev=${severity.name})';
}
