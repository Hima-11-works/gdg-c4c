import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/data/providers/dummy_pollution_data_provider.dart';
import 'package:air_health_flutter/data/providers/scenario_data.dart';
import 'package:air_health_flutter/domain/models/models.dart';

void main() {
  // Fixed anchor for deterministic tests.
  final anchor = DateTime(2026, 9, 17, 10, 0);
  const location = LocationPoint(latitude: 20.2961, longitude: 85.8245, label: 'Bhubaneswar');

  DummyPollutionDataProvider makeProvider(Scenario s) {
    return DummyPollutionDataProvider(scenario: s, anchor: anchor);
  }

  group('DummyPollutionDataProvider', () {
    group('cleanStable', () {
      test('current AQI is Good', () async {
        final reading = await makeProvider(Scenario.cleanStable)
            .getCurrentAirQuality(location);
        expect(reading.category, CpcbCategory.good);
        expect(reading.pm25, isNotNull);
      });

      test('forecast covers 12 hours and is stable', () async {
        final forecast = await makeProvider(Scenario.cleanStable)
            .getForecast(location, const Duration(hours: 12));
        expect(forecast.length, 12);
        for (final f in forecast) {
          expect(f.category, CpcbCategory.good);
          expect(f.confidence, greaterThan(0.9));
        }
      });

      test('has nearby area', () async {
        final areas = await makeProvider(Scenario.cleanStable)
            .getNearbyAreas(location);
        expect(areas, isNotEmpty);
      });

      test('no pollution events', () async {
        final events = await makeProvider(Scenario.cleanStable)
            .getPollutionEvents(location);
        expect(events, isEmpty);
      });

      test('data quality is full', () async {
        final freshness = await makeProvider(Scenario.cleanStable)
            .getDataFreshness();
        expect(freshness.quality, DataQuality.full);
      });
    });

    group('gradualRise', () {
      test('starts Satisfactory, forecast worsens over time', () async {
        final provider = makeProvider(Scenario.gradualRise);
        final reading = await provider.getCurrentAirQuality(location);
        expect(reading.category, CpcbCategory.satisfactory);

        final forecast = await provider.getForecast(location, const Duration(hours: 12));
        expect(forecast.length, 12);
        // Last hour should be worse than first.
        expect(forecast.last.aqiCpcb, greaterThan(forecast.first.aqiCpcb));
      });
    });

    group('rapidSpike', () {
      test('starts Satisfactory, forecast shows rapid rise', () async {
        final provider = makeProvider(Scenario.rapidSpike);
        final reading = await provider.getCurrentAirQuality(location);
        expect(reading.category, CpcbCategory.satisfactory);

        final forecast = await provider.getForecast(location, const Duration(hours: 12));
        // Hour 3 should be much higher than hour 0.
        expect(forecast[2].aqiCpcb, greaterThan(170));
      });
    });

    group('approachingPlume', () {
      test('current is Moderate, event arrives soon', () async {
        final provider = makeProvider(Scenario.approachingPlume);
        final reading = await provider.getCurrentAirQuality(location);
        expect(reading.category, CpcbCategory.moderate);

        final events = await provider.getPollutionEvents(location);
        expect(events.length, 1);
        expect(events.first.sourceArea, 'Industrial Belt');
        expect(events.first.peakAqiEstimate, greaterThan(250));
      });

      test('nearby area is severe', () async {
        final provider = makeProvider(Scenario.approachingPlume);
        final areas = await provider.getNearbyAreas(location);
        expect(areas.length, 1);
        expect(areas.first.aqiNow, greaterThan(300));
      });

      test('forecast shows spike at hours 1-2', () async {
        final provider = makeProvider(Scenario.approachingPlume);
        final forecast = await provider.getForecast(location, const Duration(hours: 12));
        expect(forecast[1].aqiCpcb, greaterThan(250)); // hour 2 is peak
      });
    });

    group('severeNow', () {
      test('current AQI is Very Poor', () async {
        final provider = makeProvider(Scenario.severeNow);
        final reading = await provider.getCurrentAirQuality(location);
        expect(reading.category, CpcbCategory.veryPoor);
        expect(reading.aqiCpcb, greaterThan(300));
      });

      test('forecast slowly declines but stays high', () async {
        final provider = makeProvider(Scenario.severeNow);
        final forecast = await provider.getForecast(location, const Duration(hours: 12));
        expect(forecast.last.aqiCpcb, greaterThan(200));
      });
    });

    group('recovery', () {
      test('current is Poor, forecast improves', () async {
        final provider = makeProvider(Scenario.recovery);
        final reading = await provider.getCurrentAirQuality(location);
        expect(reading.category, CpcbCategory.poor);

        final forecast = await provider.getForecast(location, const Duration(hours: 12));
        expect(forecast.last.aqiCpcb, lessThan(forecast.first.aqiCpcb));
      });

      test('nearby area is improving', () async {
        final provider = makeProvider(Scenario.recovery);
        final areas = await provider.getNearbyAreas(location);
        expect(areas.length, 1);
        expect(areas.first.trend, AreaTrend.improving);
      });
    });

    group('dataUnavailable', () {
      test('current reading exists but forecast is empty', () async {
        final provider = makeProvider(Scenario.dataUnavailable);
        final reading = await provider.getCurrentAirQuality(location);
        expect(reading.aqiCpcb, 85);

        final forecast = await provider.getForecast(location, const Duration(hours: 12));
        expect(forecast, isEmpty);
      });

      test('data quality is forecastUnavailable', () async {
        final provider = makeProvider(Scenario.dataUnavailable);
        final freshness = await provider.getDataFreshness();
        expect(freshness.quality, DataQuality.forecastUnavailable);
      });

      test('no nearby areas or events', () async {
        final provider = makeProvider(Scenario.dataUnavailable);
        expect(await provider.getNearbyAreas(location), isEmpty);
        expect(await provider.getPollutionEvents(location), isEmpty);
      });
    });

    group('partialData', () {
      test('PM2.5 is null', () async {
        final provider = makeProvider(Scenario.partialData);
        final reading = await provider.getCurrentAirQuality(location);
        expect(reading.pm25, isNull);
        expect(reading.primaryPollutant, isNull);
      });

      test('forecast covers only 6 hours with null PM2.5', () async {
        final provider = makeProvider(Scenario.partialData);
        final forecast = await provider.getForecast(location, const Duration(hours: 12));
        expect(forecast.length, 6);
        for (final f in forecast) {
          expect(f.pm25, isNull);
        }
      });

      test('data quality is partial', () async {
        final provider = makeProvider(Scenario.partialData);
        final freshness = await provider.getDataFreshness();
        expect(freshness.quality, DataQuality.partial);
      });
    });

    group('getForecast horizon filtering', () {
      test('returns fewer points for shorter horizon', () async {
        final provider = makeProvider(Scenario.cleanStable);
        final forecast6h = await provider.getForecast(location, const Duration(hours: 6));
        expect(forecast6h.length, lessThanOrEqualTo(6));
      });
    });

    group('determinism', () {
      test('same scenario + anchor produces identical data', () async {
        final p1 = makeProvider(Scenario.approachingPlume);
        final p2 = makeProvider(Scenario.approachingPlume);
        final r1 = await p1.getCurrentAirQuality(location);
        final r2 = await p2.getCurrentAirQuality(location);
        expect(r1, equals(r2));

        final f1 = await p1.getForecast(location, const Duration(hours: 12));
        final f2 = await p2.getForecast(location, const Duration(hours: 12));
        expect(f1, equals(f2));
      });
    });
  });
}
