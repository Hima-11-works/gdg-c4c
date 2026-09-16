import '../../domain/models/models.dart';

/// Deterministic scenario definitions.
///
/// Each scenario is a self-contained set of environmental data that
/// exercises a specific product path (alert rules, UI states, etc.).
/// All times are relative to a caller-supplied [anchor] so tests are
/// reproducible regardless of wall-clock time.
enum Scenario {
  cleanStable,
  gradualRise,
  rapidSpike,
  approachingPlume,
  severeNow,
  recovery,
  dataUnavailable,
  partialData,
}

/// Resolved environmental data for a single scenario.
class ScenarioData {
  const ScenarioData({
    required this.reading,
    required this.forecast,
    required this.nearbyAreas,
    required this.events,
    required this.freshness,
  });

  final AirQualityReading reading;
  final List<ForecastPoint> forecast;
  final List<NearbyArea> nearbyAreas;
  final List<PollutionEvent> events;
  final DataFreshness freshness;
}

/// Builds deterministic data for the given [scenario].
///
/// [anchor] is the "now" for the scenario — pass `DateTime.now()` in
/// production, a fixed timestamp in tests.
ScenarioData buildScenario(Scenario scenario, DateTime anchor) {
  return switch (scenario) {
    Scenario.cleanStable => _cleanStable(anchor),
    Scenario.gradualRise => _gradualRise(anchor),
    Scenario.rapidSpike => _rapidSpike(anchor),
    Scenario.approachingPlume => _approachingPlume(anchor),
    Scenario.severeNow => _severeNow(anchor),
    Scenario.recovery => _recovery(anchor),
    Scenario.dataUnavailable => _dataUnavailable(anchor),
    Scenario.partialData => _partialData(anchor),
  };
}

// ── Scenario builders ──────────────────────────────────────────────────

ScenarioData _cleanStable(DateTime anchor) {
  final reading = AirQualityReading(
    aqiCpcb: 42,
    pm25: 18.0,
    primaryPollutant: 'PM2.5',
    category: CpcbCategory.good,
    recordedAt: anchor,
  );
  final forecast = List.generate(12, (i) {
    return ForecastPoint(
      at: anchor.add(Duration(hours: i + 1)),
      aqiCpcb: 40 + (i % 3 == 0 ? 2 : 0),
      pm25: 17.0 + (i % 2),
      confidence: 0.95,
    );
  });
  return ScenarioData(
    reading: reading,
    forecast: forecast,
    nearbyAreas: [
      NearbyArea(
        location: const LocationPoint(latitude: 20.27, longitude: 85.82, label: 'Cuttack'),
        name: 'Cuttack',
        aqiNow: 38,
        forecast: forecast,
        trend: AreaTrend.stable,
        distanceKm: 25,
        confidence: 0.92,
      ),
    ],
    events: const [],
    freshness: DataFreshness(
      retrievedAt: anchor,
      quality: DataQuality.full,
    ),
  );
}

ScenarioData _gradualRise(DateTime anchor) {
  // AQI rises from 52 → ~178 over 12 hours, crossing "Moderate" at ~2h.
  final reading = AirQualityReading(
    aqiCpcb: 52,
    pm25: 30.0,
    primaryPollutant: 'PM2.5',
    category: CpcbCategory.satisfactory,
    recordedAt: anchor,
  );
  final forecast = List.generate(12, (i) {
    final aqi = 52 + (i * 10) + (i > 6 ? 8 : 0); // accelerates later
    return ForecastPoint(
      at: anchor.add(Duration(hours: i + 1)),
      aqiCpcb: aqi,
      pm25: 28.0 + (i * 5.5),
      confidence: 0.88 - (i * 0.02),
    );
  });
  return ScenarioData(
    reading: reading,
    forecast: forecast,
    nearbyAreas: const [],
    events: const [],
    freshness: DataFreshness(
      retrievedAt: anchor,
      quality: DataQuality.full,
    ),
  );
}

ScenarioData _rapidSpike(DateTime anchor) {
  // Flat 60 → +45/h for 3 hours → 195 at +3h.
  final reading = AirQualityReading(
    aqiCpcb: 60,
    pm25: 35.0,
    primaryPollutant: 'PM2.5',
    category: CpcbCategory.satisfactory,
    recordedAt: anchor,
  );
  final forecast = List.generate(12, (i) {
    final hour = i + 1;
    final aqi = hour <= 3 ? 60 + (hour * 45) : 195 - ((hour - 3) * 8);
    return ForecastPoint(
      at: anchor.add(Duration(hours: hour)),
      aqiCpcb: aqi.clamp(0, 500),
      pm25: (aqi * 0.6).clamp(0, 500),
      confidence: 0.82,
    );
  });
  return ScenarioData(
    reading: reading,
    forecast: forecast,
    nearbyAreas: const [],
    events: const [],
    freshness: DataFreshness(
      retrievedAt: anchor,
      quality: DataQuality.full,
    ),
  );
}

ScenarioData _approachingPlume(DateTime anchor) {
  // Local AQI is moderate (120), but a severe plume from "Industrial Belt"
  // arrives in ~75 minutes.
  final reading = AirQualityReading(
    aqiCpcb: 120,
    pm25: 65.0,
    primaryPollutant: 'PM2.5',
    category: CpcbCategory.moderate,
    recordedAt: anchor,
  );
  final arrival = anchor.add(const Duration(minutes: 75));
  final forecast = List.generate(12, (i) {
    final hour = i + 1;
    // Spike at hours 1-2 (plume arrival), then gradual decline.
    final aqi = switch (hour) {
      1 => 220,
      2 => 310,
      3 => 280,
      4 => 240,
      _ => 240 - ((hour - 4) * 15),
    };
    return ForecastPoint(
      at: anchor.add(Duration(hours: hour)),
      aqiCpcb: aqi.clamp(50, 500),
      pm25: (aqi * 0.55).clamp(10, 500),
      confidence: hour <= 2 ? 0.9 : 0.75,
    );
  });
  return ScenarioData(
    reading: reading,
    forecast: forecast,
    nearbyAreas: [
      NearbyArea(
        location: const LocationPoint(latitude: 20.15, longitude: 85.60, label: 'Industrial Belt'),
        name: 'Industrial Belt',
        aqiNow: 340,
        forecast: forecast,
        trend: AreaTrend.worsening,
        distanceKm: 8,
        confidence: 0.88,
      ),
    ],
    events: [
      PollutionEvent(
        id: 'plume-001',
        sourceArea: 'Industrial Belt',
        expectedArrivalAt: arrival,
        peakAqiEstimate: 310,
        confidence: 0.88,
        description: 'Heavy industrial emissions moving eastward',
      ),
    ],
    freshness: DataFreshness(
      retrievedAt: anchor,
      quality: DataQuality.full,
    ),
  );
}

ScenarioData _severeNow(DateTime anchor) {
  final reading = AirQualityReading(
    aqiCpcb: 348,
    pm25: 210.0,
    primaryPollutant: 'PM2.5',
    category: CpcbCategory.veryPoor,
    recordedAt: anchor,
  );
  final forecast = List.generate(12, (i) {
    final hour = i + 1;
    final aqi = 348 - (hour * 12); // slow decline
    return ForecastPoint(
      at: anchor.add(Duration(hours: hour)),
      aqiCpcb: aqi.clamp(200, 500),
      pm25: (aqi * 0.6).clamp(100, 500),
      confidence: 0.85,
    );
  });
  return ScenarioData(
    reading: reading,
    forecast: forecast,
    nearbyAreas: [
      NearbyArea(
        location: const LocationPoint(latitude: 20.35, longitude: 85.90, label: 'Patia'),
        name: 'Patia',
        aqiNow: 310,
        forecast: forecast,
        trend: AreaTrend.stable,
        distanceKm: 5,
        confidence: 0.80,
      ),
    ],
    events: const [],
    freshness: DataFreshness(
      retrievedAt: anchor,
      quality: DataQuality.full,
    ),
  );
}

ScenarioData _recovery(DateTime anchor) {
  // Was severe (220), now improving toward Good over 12h.
  final reading = AirQualityReading(
    aqiCpcb: 220,
    pm25: 130.0,
    primaryPollutant: 'PM2.5',
    category: CpcbCategory.poor,
    recordedAt: anchor,
  );
  final forecast = List.generate(12, (i) {
    final hour = i + 1;
    final aqi = 220 - (hour * 12);
    return ForecastPoint(
      at: anchor.add(Duration(hours: hour)),
      aqiCpcb: aqi.clamp(80, 300),
      pm25: (aqi * 0.55).clamp(40, 300),
      confidence: 0.90,
    );
  });
  return ScenarioData(
    reading: reading,
    forecast: forecast,
    nearbyAreas: [
      NearbyArea(
        location: const LocationPoint(latitude: 20.40, longitude: 85.85, label: 'Chandrasekharpur'),
        name: 'Chandrasekharpur',
        aqiNow: 95,
        forecast: forecast,
        trend: AreaTrend.improving,
        distanceKm: 8,
        confidence: 0.88,
      ),
    ],
    events: const [],
    freshness: DataFreshness(
      retrievedAt: anchor,
      quality: DataQuality.full,
    ),
  );
}

ScenarioData _dataUnavailable(DateTime anchor) {
  // Current reading is OK, but forecast is unavailable.
  final reading = AirQualityReading(
    aqiCpcb: 85,
    pm25: 45.0,
    primaryPollutant: 'PM2.5',
    category: CpcbCategory.satisfactory,
    recordedAt: anchor,
  );
  return ScenarioData(
    reading: reading,
    forecast: const [],
    nearbyAreas: const [],
    events: const [],
    freshness: DataFreshness(
      retrievedAt: anchor,
      quality: DataQuality.forecastUnavailable,
    ),
  );
}

ScenarioData _partialData(DateTime anchor) {
  // Current reading has no PM2.5, forecast only covers 6h.
  final reading = AirQualityReading(
    aqiCpcb: 110,
    pm25: null, // not available
    primaryPollutant: null,
    category: CpcbCategory.moderate,
    recordedAt: anchor,
  );
  final forecast = List.generate(6, (i) {
    final hour = i + 1;
    return ForecastPoint(
      at: anchor.add(Duration(hours: hour)),
      aqiCpcb: 110 + (hour * 5),
      pm25: null,
      confidence: 0.60,
    );
  });
  return ScenarioData(
    reading: reading,
    forecast: forecast,
    nearbyAreas: const [],
    events: const [],
    freshness: DataFreshness(
      retrievedAt: anchor,
      quality: DataQuality.partial,
    ),
  );
}
