import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';

import 'package:air_health_flutter/domain/alert_engine.dart';
import 'package:air_health_flutter/domain/alert_message_service.dart';
import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/domain/sensitivity_rules.dart';
import 'package:air_health_flutter/notifications/alert_notification_dispatcher.dart';
import 'package:air_health_flutter/notifications/notification_service.dart';
import 'package:air_health_flutter/services/alert_sound_service.dart';

class MockNotificationService extends Mock implements NotificationService {}

class MockAlertSoundService extends Mock implements AlertSoundService {}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('Patient Disease Severity Rules', () {
    test('normal citizen uses standard thresholds', () {
      const normalProfile = UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.standard,
        preferences: UserAlertPreferences(),
      );

      expect(normalProfile.isPatient, isFalse);
      final rules = SensitivityRules.forProfile(normalProfile);
      expect(rules.warningCategory, CpcbCategory.poor);
      expect(rules.rapidRiseAqiPerHour, 40);
    });

    test('mild patient has moderate warning threshold and 20 AQI/hr rise', () {
      const mildProfile = UserSensitivityProfile(
        healthContext: UserHealthContext.asthma,
        sensitivity: AlertSensitivity.standard,
        diseaseSeverity: DiseaseSeverity.mild,
        preferences: UserAlertPreferences(),
      );

      expect(mildProfile.isPatient, isTrue);
      final rules = SensitivityRules.forProfile(mildProfile);
      expect(rules.warningCategory, CpcbCategory.moderate);
      expect(rules.rapidRiseAqiPerHour, 20);
    });

    test('moderate patient has satisfactory warning threshold and 15 AQI/hr rise', () {
      const modProfile = UserSensitivityProfile(
        healthContext: UserHealthContext.copd,
        sensitivity: AlertSensitivity.standard,
        diseaseSeverity: DiseaseSeverity.moderate,
        preferences: UserAlertPreferences(),
      );

      expect(modProfile.isPatient, isTrue);
      final rules = SensitivityRules.forProfile(modProfile);
      expect(rules.warningCategory, CpcbCategory.satisfactory);
      expect(rules.rapidRiseAqiPerHour, 15);
    });

    test('severe patient triggers early at low AQI (satisfactory) and 10 AQI/hr rise', () {
      const severeProfile = UserSensitivityProfile(
        healthContext: UserHealthContext.oncologyResp,
        sensitivity: AlertSensitivity.standard,
        diseaseSeverity: DiseaseSeverity.severe,
        preferences: UserAlertPreferences(),
      );

      expect(severeProfile.isPatient, isTrue);
      final rules = SensitivityRules.forProfile(severeProfile);
      expect(rules.warningCategory, CpcbCategory.satisfactory);
      expect(rules.rapidRiseAqiPerHour, 10);
      expect(rules.leadTimePreference, const Duration(hours: 6));
    });
  });

  group('Patient Rising AQI Alert Evaluation', () {
    const engine = AlertEngine();
    final now = DateTime(2026, 9, 29, 12, 0);

    test('triggers warning when surrounding AQI rises above patient threshold', () {
      const patientProfile = UserSensitivityProfile(
        healthContext: UserHealthContext.asthma,
        sensitivity: AlertSensitivity.standard,
        diseaseSeverity: DiseaseSeverity.severe,
        preferences: UserAlertPreferences(),
      );

      // AQI 75 is in Satisfactory (51-100) — ignored by normal citizen, but alerts severe lung patient
      final reading = AirQualityReading(
        aqiCpcb: 75,
        category: CpcbCategory.satisfactory,
        recordedAt: now,
      );

      final result = engine.evaluate(
        profile: patientProfile,
        current: reading,
        forecast: [],
        events: [],
        freshness: DataFreshness(retrievedAt: now, quality: DataQuality.full),
        now: now,
      );

      expect(result.decisions, isNotEmpty);
      final decision = result.decisions.first;
      expect(decision.shouldAlert, isTrue);
      expect(decision.messageContext, contains('AQI is rising in your surrounding'));
      expect(decision.guidance, contains('take necessary precaution and action'));
    });
  });

  group('AlertMessageService patient messages', () {
    const service = AlertMessageService();

    test('builds lock-screen and in-app message requesting necessary precautions', () {
      const decision = AlertDecision(
        shouldAlert: true,
        severity: AlertSeverity.warning,
        trigger: AlertTrigger.rapidRise,
        currentAqi: 80,
        predictedAqi: 120,
        leadTime: Duration(hours: 2),
        confidence: 0.9,
        messageContext: 'Rising rapidly',
        dedupKey: 'test_rapid_rise',
        guidance: 'Take precaution',
      );

      final msg = service.buildMessage(
        decision: decision,
        sensitivity: AlertSensitivity.standard,
        healthContext: UserHealthContext.asthma,
        diseaseSeverity: DiseaseSeverity.moderate,
      );

      expect(msg.title, 'AQI is rising rapidly');
      expect(msg.lockScreenBody, contains('AQI is rising rapidly nearby'));
      expect(msg.lockScreenBody, contains('precaution and action'));
      expect(msg.body, contains('Asthma'));
      expect(msg.guidance, contains('take necessary precaution'));
    });
  });

  group('AlertSoundService & Dispatcher integration', () {
    late MockNotificationService mockNotif;
    late MockAlertSoundService mockSound;
    late AlertNotificationDispatcher dispatcher;

    setUp(() {
      mockNotif = MockNotificationService();
      mockSound = MockAlertSoundService();
      dispatcher = AlertNotificationDispatcher(
        notificationService: mockNotif,
        messageService: const AlertMessageService(),
        soundService: mockSound,
      );

      when(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
            urgent: any(named: 'urgent'),
          )).thenAnswer((_) async {});

      when(() => mockSound.play5SecondAlertSound(
            onComplete: any(named: 'onComplete'),
          )).thenAnswer((_) async {});
    });

    test('triggers 5-second alert sound and notification for patient alert', () async {
      const decision = AlertDecision(
        shouldAlert: true,
        severity: AlertSeverity.warning,
        trigger: AlertTrigger.rapidRise,
        currentAqi: 90,
        predictedAqi: 130,
        confidence: 0.9,
        messageContext: 'Rising rapidly',
        dedupKey: 'rapid_rise_test',
        guidance: 'Stay indoors',
      );

      final count = await dispatcher.dispatch(
        decisions: [decision],
        sensitivity: AlertSensitivity.standard,
        healthContext: UserHealthContext.asthma,
        diseaseSeverity: DiseaseSeverity.severe,
      );

      expect(count, 1);
      verify(() => mockNotif.show(
            id: any(named: 'id'),
            title: any(named: 'title'),
            body: any(named: 'body'),
            payload: any(named: 'payload'),
            urgent: true,
          )).called(1);

      verify(() => mockSound.play5SecondAlertSound()).called(1);
    });
  });
}
