import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/domain/alert_engine.dart';
import 'package:air_health_flutter/domain/sensitivity_rules.dart';
import 'package:air_health_flutter/domain/models/models.dart';

void main() {
  const engine = AlertEngine();
  final now = DateTime(2026, 9, 17, 10, 0);

  UserSensitivityProfile makeProfile({
    UserHealthContext context = UserHealthContext.none,
    AlertSensitivity sensitivity = AlertSensitivity.standard,
  }) {
    return UserSensitivityProfile(
      healthContext: context,
      sensitivity: sensitivity,
      preferences: const UserAlertPreferences(),
    );
  }

  AirQualityReading makeReading(int aqi, CpcbCategory cat) {
    return AirQualityReading(
      aqiCpcb: aqi,
      pm25: aqi * 0.6,
      primaryPollutant: 'PM2.5',
      category: cat,
      recordedAt: now,
    );
  }

  List<ForecastPoint> makeForecast(List<int> aqis) {
    return List.generate(aqis.length, (i) {
      return ForecastPoint(
        at: now.add(Duration(hours: i + 1)),
        aqiCpcb: aqis[i],
        confidence: 0.85,
      );
    });
  }

  DataFreshness makeFresh() =>
      DataFreshness(retrievedAt: now, quality: DataQuality.full);

  group('SensitivityRules', () {
    test('standard: warns at Poor (201+)', () {
      final rules = SensitivityRules.forProfile(
        makeProfile(sensitivity: AlertSensitivity.standard),
      );
      expect(rules.warningCategory, CpcbCategory.poor);
      expect(rules.forecastCategory, CpcbCategory.poor);
    });

    test('sensitive: warns at Moderate (101+)', () {
      final rules = SensitivityRules.forProfile(
        makeProfile(sensitivity: AlertSensitivity.sensitive),
      );
      expect(rules.warningCategory, CpcbCategory.moderate);
    });

    test('high: warns at Satisfactory (51+)', () {
      final rules = SensitivityRules.forProfile(
        makeProfile(sensitivity: AlertSensitivity.high),
      );
      expect(rules.warningCategory, CpcbCategory.satisfactory);
    });

    test('custom: uses custom thresholds', () {
      const profile = UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.custom,
        preferences: UserAlertPreferences(),
        customRules: CustomSensitivityRules(
          warningAqi: 80,
          forecastWarningAqi: 120,
          rapidRiseAqiPerHour: 20,
        ),
      );
      final rules = SensitivityRules.forProfile(profile);
      expect(rules.warningCategory, CpcbCategory.satisfactory);
      expect(rules.forecastCategory, CpcbCategory.moderate);
      expect(rules.rapidRiseAqiPerHour, 20);
    });
  });

  group('AlertEngine', () {
    test('no alert when AQI is Good and standard profile', () {
      final decisions = engine.evaluate(
        profile: makeProfile(),
        current: makeReading(42, CpcbCategory.good),
        forecast: makeForecast([45, 48, 50]),
        events: const [],
        freshness: makeFresh(),
        now: now,
      );
      expect(decisions, isEmpty);
    });

    test('current threshold fires at Poor for standard', () {
      final decisions = engine.evaluate(
        profile: makeProfile(),
        current: makeReading(250, CpcbCategory.poor),
        forecast: const [],
        events: const [],
        freshness: makeFresh(),
        now: now,
      );
      expect(decisions, isNotEmpty);
      expect(decisions.first.trigger, AlertTrigger.currentThreshold);
      expect(decisions.first.severity, AlertSeverity.warning);
    });

    test('current threshold fires at Moderate for sensitive', () {
      final decisions = engine.evaluate(
        profile: makeProfile(sensitivity: AlertSensitivity.sensitive),
        current: makeReading(142, CpcbCategory.moderate),
        forecast: const [],
        events: const [],
        freshness: makeFresh(),
        now: now,
      );
      expect(decisions, isNotEmpty);
      expect(decisions.first.trigger, AlertTrigger.currentThreshold);
    });

    test('no current alert at Moderate for standard', () {
      final decisions = engine.evaluate(
        profile: makeProfile(),
        current: makeReading(142, CpcbCategory.moderate),
        forecast: const [],
        events: const [],
        freshness: makeFresh(),
        now: now,
      );
      final currentAlerts =
          decisions.where((d) => d.trigger == AlertTrigger.currentThreshold);
      expect(currentAlerts, isEmpty);
    });

    test('forecast threshold fires when forecast crosses category', () {
      final decisions = engine.evaluate(
        profile: makeProfile(),
        current: makeReading(180, CpcbCategory.moderate),
        forecast: makeForecast([200, 250, 280]),
        events: const [],
        freshness: makeFresh(),
        now: now,
      );
      final forecastAlerts =
          decisions.where((d) => d.trigger == AlertTrigger.forecastThreshold);
      expect(forecastAlerts, isNotEmpty);
    });

    test('rapid rise fires when AQI spikes', () {
      final decisions = engine.evaluate(
        profile: makeProfile(),
        current: makeReading(60, CpcbCategory.satisfactory),
        forecast: makeForecast([120, 200, 250]),
        events: const [],
        freshness: makeFresh(),
        now: now,
      );
      final rapidRise =
          decisions.where((d) => d.trigger == AlertTrigger.rapidRise);
      expect(rapidRise, isNotEmpty);
    });

    test('approaching pollution fires for nearby event', () {
      final decisions = engine.evaluate(
        profile: makeProfile(),
        current: makeReading(120, CpcbCategory.moderate),
        forecast: const [],
        events: [
          PollutionEvent(
            id: 'plume-001',
            sourceArea: 'Industrial Belt',
            expectedArrivalAt: now.add(const Duration(minutes: 45)),
            peakAqiEstimate: 310,
            confidence: 0.88,
            description: 'Heavy plume',
          ),
        ],
        freshness: makeFresh(),
        now: now,
      );
      final approaching = decisions
          .where((d) => d.trigger == AlertTrigger.approachingPollution);
      expect(approaching, isNotEmpty);
      expect(approaching.first.predictedAqi, 310);
    });

    test('no alerts when alerts disabled', () {
      const profile = UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.standard,
        preferences: UserAlertPreferences(alertsEnabled: false),
      );
      final decisions = engine.evaluate(
        profile: profile,
        current: makeReading(350, CpcbCategory.veryPoor),
        forecast: const [],
        events: const [],
        freshness: makeFresh(),
        now: now,
      );
      expect(decisions, isEmpty);
    });

    test('high sensitivity fires at Satisfactory', () {
      final decisions = engine.evaluate(
        profile: makeProfile(sensitivity: AlertSensitivity.high),
        current: makeReading(55, CpcbCategory.satisfactory),
        forecast: const [],
        events: const [],
        freshness: makeFresh(),
        now: now,
      );
      expect(decisions, isNotEmpty);
      expect(decisions.first.severity, AlertSeverity.info);
    });
  });
}
