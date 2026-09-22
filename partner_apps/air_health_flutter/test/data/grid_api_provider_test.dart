import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/data/grid/grid_api.dart';
import 'package:air_health_flutter/data/providers/grid_api_pollution_data_provider.dart';
import 'package:air_health_flutter/domain/models/models.dart';
import 'package:air_health_flutter/domain/pm25_aqi.dart';

class FakeGridApiClient implements GridApiClient {
  FakeGridApiClient({
    this.states = const [],
    this.weatherList = const [],
    this.forecastList = const [],
    this.alertList = const [],
    this.supportedHorizons = const [60, 120, 180],
    this.isDemo = false,
    this.mode = 'live',
    this.runId = 'test-run',
  });

  final List<GridStateDto> states;
  final List<WeatherDto> weatherList;
  final List<ForecastDto> forecastList;
  final List<AlertDto> alertList;
  final List<int> supportedHorizons;
  final bool isDemo;
  final String mode;
  final String runId;

  final DateTime generatedAt = DateTime.utc(2026, 9, 17, 10, 0);

  @override
  Future<GridPublication> latestPublication() async => GridPublication(
        runId: runId,
        generatedAt: generatedAt,
        mode: mode,
        isDemo: isDemo,
        supportedForecastMinutes: supportedHorizons,
      );

  @override
  Future<GridEnvelope<List<GridStateDto>>> current({
    GeoBounds? bounds,
    int? resolution,
    String? runId,
  }) async =>
      GridEnvelope(generatedAt: generatedAt, isDemo: isDemo, mode: mode, runId: runId, data: states);

  @override
  Future<GridEnvelope<List<ForecastDto>>> forecast({
    required int minutes,
    GeoBounds? bounds,
    int? resolution,
    String? runId,
  }) async =>
      GridEnvelope(
        generatedAt: generatedAt,
        isDemo: isDemo,
        mode: mode,
        runId: runId,
        data: forecastList.where((f) => f.forecastMinutes == minutes).toList(),
      );

  @override
  Future<GridEnvelope<List<WeatherDto>>> weather({
    GeoBounds? bounds,
    int? resolution,
    String? runId,
  }) async =>
      GridEnvelope(generatedAt: generatedAt, isDemo: isDemo, mode: mode, runId: runId, data: weatherList);

  @override
  Future<GridEnvelope<List<AlertDto>>> alerts({String? runId}) async =>
      GridEnvelope(generatedAt: generatedAt, isDemo: isDemo, mode: mode, runId: runId, data: alertList);
}

void main() {
  const location = LocationPoint(
    latitude: 20.2961,
    longitude: 85.8245,
    label: 'Bhubaneswar',
  );
  final t = DateTime.utc(2026, 9, 17, 10, 0);

  // cellA is the user's cell (nearest); cellB is ~9 km away.
  final states = [
    GridStateDto(
      h3Cell: 'cellA',
      timestamp: t,
      confidence: 0.9,
      pm25: 75,
    ),
    GridStateDto(
      h3Cell: 'cellB',
      timestamp: t,
      confidence: 0.8,
      pm25: 150,
    ),
  ];
  final weather = [
    WeatherDto(h3Cell: 'cellA', latitude: 20.296, longitude: 85.825, measuredAt: t),
    WeatherDto(h3Cell: 'cellB', latitude: 20.35, longitude: 85.90, measuredAt: t),
  ];
  final forecasts = [
    ForecastDto(
      h3Cell: 'cellA',
      generatedAt: t,
      forecastTime: t.add(const Duration(hours: 1)),
      forecastMinutes: 60,
      predictedPm25: 90,
      confidence: 0.7,
    ),
    ForecastDto(
      h3Cell: 'cellA',
      generatedAt: t,
      forecastTime: t.add(const Duration(hours: 2)),
      forecastMinutes: 120,
      predictedPm25: 110,
      confidence: 0.65,
    ),
    ForecastDto(
      h3Cell: 'cellB',
      generatedAt: t,
      forecastTime: t.add(const Duration(hours: 3)),
      forecastMinutes: 180,
      predictedPm25: 200,
      confidence: 0.6,
    ),
  ];
  final alerts = [
    AlertDto(
      h3Cell: 'cellA',
      severity: 'WARNING',
      message: 'PM2.5 is high',
      createdAt: t,
      forecastPm25: 120,
      confidence: 0.6,
      forecastTime: t.add(const Duration(hours: 2)),
    ),
    AlertDto(
      h3Cell: 'cellB',
      severity: 'CRITICAL',
      message: 'Unrelated cell',
      createdAt: t,
    ),
  ];

  GridApiPollutionDataProvider providerWith(FakeGridApiClient client) =>
      GridApiPollutionDataProvider(client: client);

  test('current reading uses the nearest cell and maps PM2.5 → AQI', () async {
    final provider = providerWith(
      FakeGridApiClient(states: states, weatherList: weather),
    );

    final reading = await provider.getCurrentAirQuality(location);

    expect(reading.aqiCpcb, pm25ToCpcbAqi(75));
    expect(reading.pm25, 75);
    expect(reading.primaryPollutant, 'PM2.5');
    expect(reading.recordedAt, t);
  });

  test('uses coordinates embedded in the published current snapshot', () async {
    final stateWithCoordinates = GridStateDto(
      h3Cell: 'cellA',
      timestamp: t,
      confidence: 0.9,
      pm25: 75,
      latitude: 20.296,
      longitude: 85.825,
    );
    final provider = providerWith(FakeGridApiClient(states: [stateWithCoordinates]));

    final reading = await provider.getCurrentAirQuality(location);

    expect(reading.pm25, 75);
  });

  test('forecast assembles the requested horizons for the user cell only',
      () async {
    final provider = providerWith(
      FakeGridApiClient(
        states: states,
        weatherList: weather,
        forecastList: forecasts,
      ),
    );

    final points = await provider.getForecast(location, const Duration(hours: 2));

    expect(points, hasLength(2));
    expect(points.first.at.isBefore(points.last.at), isTrue);
    expect(points.first.pm25, 90);
    expect(points.last.pm25, 110);
  });

  test('forecast returns nothing for a non-positive horizon', () async {
    final provider = providerWith(
      FakeGridApiClient(states: states, weatherList: weather),
    );
    expect(await provider.getForecast(location, Duration.zero), isEmpty);
  });

  test('forecast requests only horizons supported by the published run', () async {
    final provider = providerWith(FakeGridApiClient(
      states: states,
      weatherList: weather,
      forecastList: forecasts,
      supportedHorizons: const [60, 180],
    ));

    final points = await provider.getForecast(location, const Duration(minutes: 150));

    expect(points, hasLength(1));
    expect(points.single.pm25, 90);
  });

  test('nearby areas exclude the user cell and derive a trend', () async {
    final provider = providerWith(
      FakeGridApiClient(
        states: states,
        weatherList: weather,
        forecastList: forecasts,
      ),
    );

    final areas = await provider.getNearbyAreas(location);

    expect(areas, hasLength(1));
    expect(areas.single.name, isNot(startsWith('cellA')));
    expect(areas.single.aqiNow, pm25ToCpcbAqi(150));
    expect(areas.single.distanceKm, greaterThan(0));
    // cellB: 150 now → 200 forecast (+33%) counts as worsening.
    expect(areas.single.trend, AreaTrend.worsening);
  });

  test('events map alerts for the user cell only', () async {
    final provider = providerWith(
      FakeGridApiClient(states: states, weatherList: weather, alertList: alerts),
    );

    final events = await provider.getPollutionEvents(location);

    expect(events, hasLength(1));
    expect(events.single.peakAqiEstimate, pm25ToCpcbAqi(120));
    expect(events.single.description, 'PM2.5 is high');
    expect(events.single.confidence, 0.6);
  });

  test('freshness reflects the most recent response', () async {
    final provider = providerWith(
      FakeGridApiClient(states: states, weatherList: weather),
    );

    await provider.getCurrentAirQuality(location);
    final freshness = await provider.getDataFreshness();

    expect(freshness.retrievedAt, t);
    expect(freshness.quality, DataQuality.full);
    expect(freshness.runId, 'test-run');
    expect(freshness.mode, 'live');
    expect(freshness.isDemo, isFalse);
  });

  test('freshness preserves demo provenance', () async {
    final provider = providerWith(FakeGridApiClient(
      states: states,
      weatherList: weather,
      isDemo: true,
      mode: 'demo',
      runId: 'demo-scenario-2',
    ));

    await provider.getCurrentAirQuality(location);
    final freshness = await provider.getDataFreshness();

    expect(freshness.isDemo, isTrue);
    expect(freshness.mode, 'demo');
    expect(freshness.runId, 'demo-scenario-2');
  });

  test('throws when no cell with PM2.5 is near the location', () async {
    final provider = providerWith(FakeGridApiClient());

    await expectLater(
      provider.getCurrentAirQuality(location),
      throwsA(isA<PollutionDataUnavailable>()),
    );
  });
}
