import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/alert_message_service.dart';
import 'package:air_health_flutter/domain/models/models.dart';

void main() {
  const service = AlertMessageService();

  AlertDecision makeDecision({
    AlertTrigger trigger = AlertTrigger.currentThreshold,
    AlertSeverity severity = AlertSeverity.warning,
    int currentAqi = 250,
    int? predictedAqi,
    Duration? leadTime,
    double confidence = 0.85,
  }) {
    return AlertDecision(
      shouldAlert: true,
      severity: severity,
      trigger: trigger,
      currentAqi: currentAqi,
      predictedAqi: predictedAqi,
      predictedTime: leadTime != null
          ? DateTime.now().add(leadTime)
          : null,
      leadTime: leadTime,
      confidence: confidence,
      messageContext: '',
      dedupKey: 'test',
      guidance: '',
    );
  }

  const banned = [
    'attack', 'medication', 'medicine', 'prescription',
    'diagnosis', 'diagnose', 'asthma', 'copd',
    'safe for', 'take your', 'guarantee',
    'you are having', 'emergency',
    'prescribed', 'treatments', 'seek medical',
  ];

  void expectNoBannedLanguage(AlertMessage msg) {
    final text =
        '${msg.title} ${msg.body} ${msg.lockScreenBody} ${msg.guidance}'
            .toLowerCase();
    for (final word in banned) {
      expect(text, isNot(contains(word)),
          reason: 'Found banned word "$word" in: $text');
    }
  }

  void expectLockScreenSafe(AlertMessage msg) {
    final lockLower = msg.lockScreenBody.toLowerCase();
    expect(lockLower, isNot(contains('sensitive')),
        reason: 'Lock screen mentions sensitivity');
    expect(lockLower, isNot(contains('early')),
        reason: 'Lock screen mentions early alerts');
    expect(lockLower, isNot(contains('care plan')),
        reason: 'Lock screen mentions care plan');
  }

  group('AlertMessageService', () {
    // ── Current threshold ──────────────────────────────────────────

    group('currentThreshold', () {
      test('standard: includes AQI and category', () {
        final msg = service.buildMessage(
          decision: makeDecision(currentAqi: 250),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.title, contains('Poor'));
        expect(msg.body, contains('250'));
        expect(msg.body, contains('Poor'));
        expect(msg.lockScreenBody, contains('250'));
        expectNoBannedLanguage(msg);
        expectLockScreenSafe(msg);
      });

      test('sensitive: includes early-alert suffix', () {
        final msg = service.buildMessage(
          decision: makeDecision(currentAqi: 142),
          sensitivity: AlertSensitivity.sensitive,
        );
        expect(msg.body, contains('early air-quality alerts'));
        expect(msg.body, contains('reducing prolonged outdoor exposure'));
        expect(msg.lockScreenBody, isNot(contains('early')));
        expectLockScreenSafe(msg);
      });

      test('high: includes high-sensitivity suffix', () {
        final msg = service.buildMessage(
          decision: makeDecision(currentAqi: 55),
          sensitivity: AlertSensitivity.high,
        );
        expect(msg.body, contains('high-sensitivity'));
        expect(msg.lockScreenBody, isNot(contains('high-sensitivity')));
      });

      test('standard: no sensitivity suffix', () {
        final msg = service.buildMessage(
          decision: makeDecision(currentAqi: 250),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.body, isNot(contains('early')));
        expect(msg.body, isNot(contains('sensitivity')));
      });

      test('Very Poor shows correct label', () {
        final msg = service.buildMessage(
          decision: makeDecision(currentAqi: 350),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.title, contains('Very Poor'));
      });

      test('Severe shows correct label', () {
        final msg = service.buildMessage(
          decision: makeDecision(currentAqi: 450),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.title, contains('Severe'));
      });
    });

    // ── Forecast threshold ─────────────────────────────────────────

    group('forecastThreshold', () {
      test('includes predicted category and lead time', () {
        final msg = service.buildMessage(
          decision: makeDecision(
            trigger: AlertTrigger.forecastThreshold,
            currentAqi: 180,
            predictedAqi: 250,
            leadTime: const Duration(minutes: 90),
          ),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.title, contains('worsen'));
        expect(msg.body, contains('Poor'));
        expect(msg.body, contains('1 hours 30 minutes'));
        expect(msg.lockScreenBody, contains('1 hours 30 minutes'));
        expectNoBannedLanguage(msg);
        expectLockScreenSafe(msg);
      });

      test('sensitive: includes early-alert suffix', () {
        final msg = service.buildMessage(
          decision: makeDecision(
            trigger: AlertTrigger.forecastThreshold,
            predictedAqi: 200,
            leadTime: const Duration(hours: 2),
          ),
          sensitivity: AlertSensitivity.sensitive,
        );
        expect(msg.body, contains('early air-quality alerts'));
        expect(msg.lockScreenBody, isNot(contains('early')));
      });

      test('short lead time: "soon"', () {
        final msg = service.buildMessage(
          decision: makeDecision(
            trigger: AlertTrigger.forecastThreshold,
            predictedAqi: 200,
            leadTime: null,
          ),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.body, contains('soon'));
      });
    });

    // ── Rapid rise ─────────────────────────────────────────────────

    group('rapidRise', () {
      test('includes rising language and lead time', () {
        final msg = service.buildMessage(
          decision: makeDecision(
            trigger: AlertTrigger.rapidRise,
            currentAqi: 60,
            predictedAqi: 200,
            leadTime: const Duration(hours: 3),
          ),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.title, contains('rapidly'));
        expect(msg.body, contains('rising quickly'));
        expect(msg.body, contains('3 hours'));
        expectNoBannedLanguage(msg);
        expectLockScreenSafe(msg);
      });

      test('sensitive: includes suffix', () {
        final msg = service.buildMessage(
          decision: makeDecision(
            trigger: AlertTrigger.rapidRise,
            predictedAqi: 200,
            leadTime: const Duration(hours: 2),
          ),
          sensitivity: AlertSensitivity.sensitive,
        );
        expect(msg.body, contains('early air-quality alerts'));
      });
    });

    // ── Approaching pollution ──────────────────────────────────────

    group('approachingPollution', () {
      test('includes approaching language and lead time', () {
        final msg = service.buildMessage(
          decision: makeDecision(
            trigger: AlertTrigger.approachingPollution,
            predictedAqi: 310,
            leadTime: const Duration(minutes: 75),
          ),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.title, contains('approaching'));
        expect(msg.body, contains('nearby area'));
        expect(msg.body, contains('1 hours 15 minutes'));
        expectNoBannedLanguage(msg);
        expectLockScreenSafe(msg);
      });

      test('sensitive: includes suffix', () {
        final msg = service.buildMessage(
          decision: makeDecision(
            trigger: AlertTrigger.approachingPollution,
            leadTime: const Duration(hours: 1),
          ),
          sensitivity: AlertSensitivity.sensitive,
        );
        expect(msg.body, contains('early air-quality alerts'));
      });
    });

    // ── Recovery ───────────────────────────────────────────────────

    group('recovery', () {
      test('includes improvement language', () {
        final msg = service.buildMessage(
          decision: makeDecision(
            trigger: AlertTrigger.recovery,
            severity: AlertSeverity.info,
            currentAqi: 95,
          ),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.title, contains('improving'));
        expect(msg.body, contains('95'));
        expect(msg.body, contains('Satisfactory'));
        expect(msg.guidance, contains('improved'));
        expectNoBannedLanguage(msg);
      });

      test('sensitive: no early-alert suffix on recovery', () {
        final msg = service.buildMessage(
          decision: makeDecision(
            trigger: AlertTrigger.recovery,
            severity: AlertSeverity.info,
            currentAqi: 95,
          ),
          sensitivity: AlertSensitivity.sensitive,
        );
        // Recovery messages don't need the "early alerts" suffix.
        expect(msg.body, isNot(contains('early')));
      });
    });

    // ── Lock-screen safety ─────────────────────────────────────────

    group('lock-screen safety', () {
      test('no sensitivity or health info in lock-screen body', () {
        for (final trigger in AlertTrigger.values) {
          for (final sensitivity in AlertSensitivity.values) {
            final msg = service.buildMessage(
              decision: makeDecision(
                trigger: trigger,
                currentAqi: 250,
                predictedAqi: 300,
                leadTime: const Duration(hours: 1),
              ),
              sensitivity: sensitivity,
            );
            expectLockScreenSafe(msg);
          }
        }
      });
    });

    // ── No medical language across all messages ─────────────────────

    group('no medical language', () {
      test('all triggers × all sensitivities: no banned words', () {
        for (final trigger in AlertTrigger.values) {
          for (final sensitivity in AlertSensitivity.values) {
            final msg = service.buildMessage(
              decision: makeDecision(
                trigger: trigger,
                currentAqi: 250,
                predictedAqi: 300,
                leadTime: const Duration(hours: 1),
              ),
              sensitivity: sensitivity,
            );
            expectNoBannedLanguage(msg);
          }
        }
      });
    });

    // ── Guidance ───────────────────────────────────────────────────

    group('guidance', () {
      test('info severity: no special precautions', () {
        final msg = service.buildMessage(
          decision: makeDecision(severity: AlertSeverity.info),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.guidance, contains('acceptable'));
      });

      test('advisory: consider reducing exposure', () {
        final msg = service.buildMessage(
          decision: makeDecision(severity: AlertSeverity.advisory),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.guidance, contains('reducing'));
      });

      test('warning: reduce exposure', () {
        final msg = service.buildMessage(
          decision: makeDecision(severity: AlertSeverity.warning),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.guidance, contains('Reduce'));
      });

      test('urgent: avoid outdoor activity', () {
        final msg = service.buildMessage(
          decision: makeDecision(severity: AlertSeverity.urgent),
          sensitivity: AlertSensitivity.standard,
        );
        expect(msg.guidance, contains('Avoid outdoor'));
      });
    });
  });
}
