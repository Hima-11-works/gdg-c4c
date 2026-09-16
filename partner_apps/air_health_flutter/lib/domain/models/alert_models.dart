import 'user_alert_preferences.dart';

/// Why an alert was generated.
enum AlertTrigger {
  currentThreshold,
  forecastThreshold,
  rapidRise,
  approachingPollution,
  recovery,
}

/// The output of the alert engine for a single situation.
class AlertDecision {
  const AlertDecision({
    required this.shouldAlert,
    required this.severity,
    required this.trigger,
    required this.currentAqi,
    this.predictedAqi,
    this.predictedTime,
    this.leadTime,
    required this.confidence,
    required this.messageContext,
    required this.dedupKey,
    required this.guidance,
  });

  /// No-alert sentinel — returned when the engine finds nothing to report.
  static const AlertDecision none = AlertDecision(
    shouldAlert: false,
    severity: AlertSeverity.info,
    trigger: AlertTrigger.currentThreshold,
    currentAqi: 0,
    confidence: 0,
    messageContext: '',
    dedupKey: '',
    guidance: '',
  );

  final bool shouldAlert;
  final AlertSeverity severity;
  final AlertTrigger trigger;
  final int currentAqi;
  final int? predictedAqi;
  final DateTime? predictedTime;
  final Duration? leadTime;
  final double confidence;

  /// Machine-readable key for deduplication (e.g. "forecast_poor_20260916").
  final String dedupKey;

  /// Contextual, non-diagnostic message for the user.
  final String messageContext;

  /// Exposure-aware guidance — never diagnostic.
  final String guidance;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is AlertDecision &&
          shouldAlert == other.shouldAlert &&
          severity == other.severity &&
          trigger == other.trigger &&
          currentAqi == other.currentAqi &&
          predictedAqi == other.predictedAqi &&
          predictedTime == other.predictedTime &&
          leadTime == other.leadTime &&
          confidence == other.confidence &&
          messageContext == other.messageContext &&
          dedupKey == other.dedupKey &&
          guidance == other.guidance;

  @override
  int get hashCode => Object.hash(
        shouldAlert,
        severity,
        trigger,
        currentAqi,
        predictedAqi,
        predictedTime,
        leadTime,
        confidence,
        messageContext,
        dedupKey,
        guidance,
      );

  @override
  String toString() =>
      'AlertDecision(alert=$shouldAlert, sev=${severity.name}, '
      'trigger=${trigger.name}, aqi=$currentAqi)';
}

/// Deduplication state persisted across engine evaluations.
class DedupEntry {
  const DedupEntry({
    required this.key,
    required this.lastAlertedAt,
    this.escalated = false,
  });

  final String key;
  final DateTime lastAlertedAt;
  final bool escalated;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is DedupEntry &&
          key == other.key &&
          lastAlertedAt == other.lastAlertedAt &&
          escalated == other.escalated;

  @override
  int get hashCode => Object.hash(key, lastAlertedAt, escalated);

  @override
  String toString() =>
      'DedupEntry($key, at=$lastAlertedAt, escalated=$escalated)';
}
