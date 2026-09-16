/// Why an alert was generated.
enum AlertTrigger {
  currentThreshold,
  forecastThreshold,
  rapidRise,
  approachingPollution,
  recovery,
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
}
