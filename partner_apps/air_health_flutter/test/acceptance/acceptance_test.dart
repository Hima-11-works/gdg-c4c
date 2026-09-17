import 'package:flutter_test/flutter_test.dart';

import 'package:air_health_flutter/data/providers/dummy_pollution_data_provider.dart';
import 'package:air_health_flutter/data/providers/scenario_data.dart';
import 'package:air_health_flutter/domain/alert_engine.dart';
import 'package:air_health_flutter/domain/models/models.dart';

/// H12 acceptance test — verifies the full data flow from provider
/// through alert engine for the approachingPlume scenario.
void main() {
  final anchor = DateTime(2026, 9, 17, 10, 0);
  const location = LocationPoint(
    latitude: 20.2961,
    longitude: 85.8245,
    label: 'Bhubaneswar',
  );

  group('H12 acceptance — approachingPlume scenario', () {
    late DummyPollutionDataProvider provider;
    late AirQualityReading reading;
    late List<ForecastPoint> forecast;
    late List<PollutionEvent> events;
    late DataFreshness freshness;

    setUpAll(() async {
      provider = DummyPollutionDataProvider(
        scenario: Scenario.approachingPlume,
        anchor: anchor,
      );
      reading = await provider.getCurrentAirQuality(location);
      forecast =
          await provider.getForecast(location, const Duration(hours: 12));
      events = await provider.getPollutionEvents(location);
      freshness = await provider.getDataFreshness();
    });

    test('current AQI is Moderate', () {
      expect(reading.category, CpcbCategory.moderate);
      expect(reading.aqiCpcb, greaterThan(100));
    });

    test('12h forecast is non-empty and has valid confidence', () {
      expect(forecast, isNotEmpty);
      for (final f in forecast) {
        expect(f.confidence, inInclusiveRange(0.0, 1.0));
        expect(f.aqiCpcb, greaterThan(0));
      }
    });

    test('forecast shows spike at hours 1-2', () {
      expect(forecast[1].aqiCpcb, greaterThan(250));
    });

    test('pollution event is present', () {
      expect(events, isNotEmpty);
      expect(events.first.sourceArea, 'Industrial Belt');
      expect(events.first.peakAqiEstimate, greaterThan(250));
    });

    test('data quality is full', () {
      expect(freshness.quality, DataQuality.full);
    });

    test('sensitive profile generates alerts', () {
      const profile = UserSensitivityProfile(
        healthContext: UserHealthContext.asthma,
        sensitivity: AlertSensitivity.sensitive,
        preferences: UserAlertPreferences(),
      );

      const engine = AlertEngine();
      final result = engine.evaluate(
        profile: profile,
        current: reading,
        forecast: forecast,
        events: events,
        freshness: freshness,
        now: anchor,
      );

      expect(result.decisions, isNotEmpty);
      expect(result.decisions.any((d) => d.shouldAlert), isTrue);
    });

    test('standard profile generates fewer alerts than sensitive', () {
      const sensitiveProfile = UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.sensitive,
        preferences: UserAlertPreferences(),
      );

      const standardProfile = UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.standard,
        preferences: UserAlertPreferences(),
      );

      const engine = AlertEngine();

      final sensitiveResult = engine.evaluate(
        profile: sensitiveProfile,
        current: reading,
        forecast: forecast,
        events: events,
        freshness: freshness,
        now: anchor,
      );

      final standardResult = engine.evaluate(
        profile: standardProfile,
        current: reading,
        forecast: forecast,
        events: events,
        freshness: freshness,
        now: anchor,
      );

      expect(sensitiveResult.decisions.length,
          greaterThanOrEqualTo(standardResult.decisions.length));
    });

    test('all alert decisions have non-empty guidance', () {
      const profile = UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.sensitive,
        preferences: UserAlertPreferences(),
      );

      const engine = AlertEngine();
      final result = engine.evaluate(
        profile: profile,
        current: reading,
        forecast: forecast,
        events: events,
        freshness: freshness,
        now: anchor,
      );

      for (final d in result.decisions) {
        expect(d.guidance, isNotEmpty);
        expect(d.messageContext, isNotEmpty);
        expect(d.dedupKey, isNotEmpty);
      }
    });

    test('no diagnostic language in any guidance or message', () {
      const profile = UserSensitivityProfile(
        healthContext: UserHealthContext.asthma,
        sensitivity: AlertSensitivity.sensitive,
        preferences: UserAlertPreferences(),
      );

      const engine = AlertEngine();
      final result = engine.evaluate(
        profile: profile,
        current: reading,
        forecast: forecast,
        events: events,
        freshness: freshness,
        now: anchor,
      );

      const banned = [
        'attack',
        'medication',
        'medicine',
        'prescription',
        'diagnosis',
        'diagnose',
        'asthma',
        'copd',
        'safe for',
        'take your',
        'prescribed',
        'treatments',
        'seek medical',
      ];

      for (final d in result.decisions) {
        final text = '${d.guidance} ${d.messageContext}'.toLowerCase();
        for (final word in banned) {
          expect(text, isNot(contains(word)));
        }
      }
    });
  });
}
