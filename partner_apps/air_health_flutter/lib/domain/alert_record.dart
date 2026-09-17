import '../domain/models/models.dart';

/// A single alert record for the alerts history.
///
/// Combines the engine's [AlertDecision] with the user-facing
/// [AlertMessage] and a timestamp, so the Alerts screen can display
/// everything without re-running the engine.
class AlertRecord {
  const AlertRecord({
    required this.id,
    required this.decision,
    required this.title,
    required this.body,
    required this.guidance,
    required this.explanation,
    required this.createdAt,
    this.resolvedAt,
  });

  final String id;
  final AlertDecision decision;
  final String title;
  final String body;
  final String guidance;
  final String explanation;
  final DateTime createdAt;
  final DateTime? resolvedAt;

  bool get isResolved => resolvedAt != null;

  /// Active: created within the last 2 hours and not resolved.
  bool get isActive =>
      !isResolved &&
      DateTime.now().difference(createdAt).inHours < 2;

  /// Recent: created within the last 24 hours but not active.
  bool get isRecent =>
      !isActive &&
      !isResolved &&
      DateTime.now().difference(createdAt).inHours < 24;

  /// Resolved: explicitly marked resolved.
  bool get isResolvedSection => isResolved;

  AlertSeverity get severity => decision.severity;
  int get currentAqi => decision.currentAqi;
  int? get predictedAqi => decision.predictedAqi;
  DateTime? get predictedTime => decision.predictedTime;
  Duration? get leadTime => decision.leadTime;
  double get confidence => decision.confidence;
  AlertTrigger get trigger => decision.trigger;

  AlertRecord copyWith({DateTime? resolvedAt}) {
    return AlertRecord(
      id: id,
      decision: decision,
      title: title,
      body: body,
      guidance: guidance,
      explanation: explanation,
      createdAt: createdAt,
      resolvedAt: resolvedAt ?? this.resolvedAt,
    );
  }
}
