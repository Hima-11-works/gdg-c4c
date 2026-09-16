import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/data/providers/dummy_pollution_data_provider.dart';
import 'package:air_health_flutter/domain/models/models.dart';

void main() {
  group('DummyPollutionDataProvider', () {
    late DummyPollutionDataProvider provider;

    setUp(() {
      provider = DummyPollutionDataProvider();
    });

    test('getCurrentAirQuality returns a valid reading', () async {
      final reading = await provider.getCurrentAirQuality(
        const LocationPoint(latitude: 20.3, longitude: 85.8, label: 'Bhubaneswar'),
      );
      expect(reading.aqiCpcb, greaterThan(0));
      expect(reading.category, isNotNull);
      expect(reading.recordedAt, isNotNull);
    });

    test('getForecast returns 12 hourly points', () async {
      final forecast = await provider.getForecast(
        const LocationPoint(latitude: 20.3, longitude: 85.8),
        const Duration(hours: 12),
      );
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

    test('getNearbyAreas returns empty list (no scenarios yet)', () async {
      final areas = await provider.getNearbyAreas(
        const LocationPoint(latitude: 20.3, longitude: 85.8),
      );
      expect(areas, isEmpty);
    });

    test('getPollutionEvents returns empty list (no scenarios yet)', () async {
      final events = await provider.getPollutionEvents(
        const LocationPoint(latitude: 20.3, longitude: 85.8),
      );
      expect(events, isEmpty);
    });
  });
}
