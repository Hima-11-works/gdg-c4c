import 'package:flutter_test/flutter_test.dart';

import 'package:air_health_flutter/data/providers/dummy_pollution_data_provider.dart';
import 'package:air_health_flutter/data/providers/scenario_data.dart';
import 'package:air_health_flutter/domain/alert_engine.dart';
import 'package:air_health_flutter/domain/models/models.dart';

/// H12 acceptance test — verifies the full data flow from provider
/// through alert engine for the approachingPlume scenario.
///
/// This is not a widget test (those require a running app); it
/// validates the domain logic end-to-end.
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
      forecast = await provider.getForecast(location, const Duration(hours: 12));
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
      final decisions = engine.evaluate(
        profile: profile,
        current: reading,
        forecast: forecast,
        events: events,
        freshness: freshness,
        now: anchor,
      );

      expect(decisions, isNotEmpty);
      // Should have at least one alert (either current, forecast, or approaching)
      expect(decisions.any((d) => d.shouldAlert), isTrue);
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

      final sensitiveDecisions = engine.evaluate(
        profile: sensitiveProfile,
        current: reading,
        forecast: forecast,
        events: events,
        freshness: freshness,
        now: anchor,
      );

      final standardDecisions = engine.evaluate(
        profile: standardProfile,
        current: reading,
        forecast: forecast,
        events: events,
        freshness: freshness,
        now: anchor,
      );

      // Sensitive should generate at least as many alerts as standard.
      expect(sensitiveDecisions.length, greaterThanOrEqualTo(standardDecisions.length));
    });

    test('all alert decisions have non-empty guidance', () {
      const profile = UserSensitivityProfile(
        healthContext: UserHealthContext.none,
        sensitivity: AlertSensitivity.sensitive,
        preferences: UserAlertPreferences(),
      );

      const engine = AlertEngine();
      final decisions = engine.evaluate(
        profile: profile,
        current: reading,
        forecast: forecast,
        events: events,
        freshness: freshness,
        now: anchor,
      );

      for (final d in decisions) {
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
      final decisions = engine.evaluate(
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
      ];

      for (final d in decisions) {
        final text = '${d.guidance} ${d.messageContext}'.toLowerCase();
        for (final word in banned) {
          expect(text, isNot(contains(word)));
        }
      }
    });
  });
}
