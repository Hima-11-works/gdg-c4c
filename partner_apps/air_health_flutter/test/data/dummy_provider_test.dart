import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/data/providers/dummy_pollution_data_provider.dart';
import 'package:air_health_flutter/data/providers/scenario_data.dart';
import 'package:air_health_flutter/domain/models/models.dart';

void main() {
  final anchor = DateTime(2026, 9, 17, 10, 0);
  const location = LocationPoint(latitude: 20.3, longitude: 85.8, label: 'Bhubaneswar');

  group('DummyPollutionDataProvider', () {
    late DummyPollutionDataProvider provider;

    setUp(() {
      provider = DummyPollutionDataProvider(
        scenario: Scenario.cleanStable,
        anchor: anchor,
      );
    });

    test('getCurrentAirQuality returns a valid reading', () async {
      final reading = await provider.getCurrentAirQuality(location);
      expect(reading.aqiCpcb, greaterThan(0));
      expect(reading.category, isNotNull);
      expect(reading.recordedAt, isNotNull);
    });

    test('getForecast returns 12 hourly points', () async {
      final forecast = await provider.getForecast(location, const Duration(hours: 12));
      expect(forecast.length, 12);
      for (final point in forecast) {
        expect(point.confidence, inInclusiveRange(0.0, 1.0));
        expect(point.aqiCpcb, greaterThan(0));
      }
    });

    test('getDataFreshness returns full quality', () async {
      final freshness = await provider.getDataFreshness();
      expect(freshness.quality, DataQuality.full);
    });

    test('getNearbyAreas returns non-empty for cleanStable', () async {
      final areas = await provider.getNearbyAreas(location);
      expect(areas, isNotEmpty);
    });

    test('getPollutionEvents returns empty for cleanStable', () async {
      final events = await provider.getPollutionEvents(location);
      expect(events, isEmpty);
    });
  });
}
