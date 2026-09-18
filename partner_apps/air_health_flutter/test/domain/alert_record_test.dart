import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/alert_record.dart';
import 'package:air_health_flutter/domain/models/models.dart';

void main() {
  final now = DateTime.now();

  AlertRecord makeRecord({
    required DateTime createdAt,
    DateTime? resolvedAt,
  }) {
    return AlertRecord(
      id: 'r1',
      decision: const AlertDecision(
        shouldAlert: true,
        severity: AlertSeverity.warning,
        trigger: AlertTrigger.currentThreshold,
        currentAqi: 250,
        confidence: 1,
        messageContext: 'context',
        dedupKey: 'current_poor',
        guidance: 'guidance',
      ),
      title: 'Poor air quality',
      body: 'body',
      guidance: 'guidance',
      explanation: 'explanation',
      createdAt: createdAt,
      resolvedAt: resolvedAt,
    );
  }

  test('created 30 minutes ago is active', () {
    final r = makeRecord(createdAt: now.subtract(const Duration(minutes: 30)));
    expect(r.isActive, isTrue);
    expect(r.isRecent, isFalse);
    expect(r.isStale, isFalse);
  });

  test('created 5 hours ago is recent', () {
    final r = makeRecord(createdAt: now.subtract(const Duration(hours: 5)));
    expect(r.isActive, isFalse);
    expect(r.isRecent, isTrue);
    expect(r.isStale, isFalse);
  });

  test('unresolved and older than 24h is stale, not invisible', () {
    final r = makeRecord(createdAt: now.subtract(const Duration(hours: 30)));
    expect(r.isActive, isFalse);
    expect(r.isRecent, isFalse);
    // Regression: this used to match no section and silently disappear.
    expect(r.isStale, isTrue);
  });

  test('resolved records are neither active, recent nor stale', () {
    final r = makeRecord(
      createdAt: now.subtract(const Duration(hours: 30)),
      resolvedAt: now.subtract(const Duration(hours: 1)),
    );
    expect(r.isResolved, isTrue);
    expect(r.isActive, isFalse);
    expect(r.isRecent, isFalse);
    expect(r.isStale, isFalse);
  });
}
