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

// ── Time-shifted snapshot (dev simulator) ──────────────────────────────

/// Builds the scenario as seen from [now], with [anchor] the scenario's fixed
/// start.
///
/// [buildScenario] always returns data "as of" its anchor. The dev simulator
/// needs the scenario to advance with a simulated clock, so this treats the
/// scenario's timeline — its `reading` at t0 plus its hourly `forecast` — as
/// continuous: the current reading is sampled at `now`, and the forecast is
/// sampled forward from `now`. With `now == anchor` it returns
/// [buildScenario] unchanged, so callers (and tests) that don't shift time
/// keep the exact authored values.
ScenarioData buildScenarioSnapshot(
  Scenario scenario,
  DateTime anchor,
  DateTime now,
) {
  final base = buildScenario(scenario, anchor);
  final elapsed = now.difference(anchor);
  if (elapsed.inSeconds <= 0) return base;

  final timeline = _ScenarioTimeline(base.reading, base.forecast);
  return ScenarioData(
    reading: timeline.readingAt(now, elapsed),
    forecast: [
      for (var h = 1; h <= base.forecast.length; h++)
        timeline.forecastAt(now, elapsed + Duration(hours: h), h),
    ],
    // Nearby areas and events are authored as single snapshots, so their
    // values are left as-is. An event's absolute `expectedArrivalAt` still
    // passes as the clock advances, which is exactly what the approaching-
    // pollution alert rule keys off.
    nearbyAreas: base.nearbyAreas,
    events: base.events,
    freshness: DataFreshness(
      retrievedAt: now,
      quality: base.freshness.quality,
      nextRefreshEta: base.freshness.nextRefreshEta,
    ),
  );
}

/// A scenario's reading + hourly forecast treated as a continuous timeline, so
/// AQI/pm25/confidence can be sampled at any elapsed time, not just whole
/// hours. Sampling past the last authored hour clamps to that last value.
class _ScenarioTimeline {
  _ScenarioTimeline(AirQualityReading reading, List<ForecastPoint> forecast)
      : _primaryPollutant = reading.primaryPollutant,
        _aqi = [reading.aqiCpcb, ...forecast.map((f) => f.aqiCpcb)],
        _pm25 = [reading.pm25, ...forecast.map((f) => f.pm25)],
        _confidence = [
          // The current reading carries no confidence; use the first forecast
          // point's as the best available stand-in for the t0 sample.
          forecast.isNotEmpty ? forecast.first.confidence : 1.0,
          ...forecast.map((f) => f.confidence),
        ];

  final String? _primaryPollutant;
  final List<int> _aqi;
  final List<double?> _pm25;
  final List<double> _confidence;

  int get _lastIndex => _aqi.length - 1;

  /// Fractional index into the timeline for [elapsed] since the anchor,
  /// clamped to `[0, lastIndex]`.
  double _indexAt(Duration elapsed) {
    final hours = elapsed.inMinutes / 60.0;
    if (hours <= 0) return 0;
    if (hours >= _lastIndex) return _lastIndex.toDouble();
    return hours;
  }

  AirQualityReading readingAt(DateTime now, Duration elapsed) {
    final i = _indexAt(elapsed);
    final aqi = _lerpAqi(i);
    return AirQualityReading(
      aqiCpcb: aqi,
      pm25: _lerpPm25(i),
      primaryPollutant: _primaryPollutant,
      category: CpcbCategory.fromAqi(aqi),
      recordedAt: now,
    );
  }

  ForecastPoint forecastAt(DateTime now, Duration elapsed, int hoursAhead) {
    final i = _indexAt(elapsed);
    return ForecastPoint(
      at: now.add(Duration(hours: hoursAhead)),
      aqiCpcb: _lerpAqi(i),
      pm25: _lerpPm25(i),
      confidence: _lerpConfidence(i),
    );
  }

  int _lerpAqi(double i) {
    final lo = i.floor();
    final hi = i.ceil();
    if (lo == hi) return _aqi[lo];
    final t = i - lo;
    return (_aqi[lo] + (_aqi[hi] - _aqi[lo]) * t).round();
  }

  double? _lerpPm25(double i) {
    final lo = i.floor();
    final hi = i.ceil();
    final a = _pm25[lo];
    final b = _pm25[hi];
    if (a == null || b == null) return a ?? b;
    if (lo == hi) return a;
    final t = i - lo;
    return a + (b - a) * t;
  }

  double _lerpConfidence(double i) {
    final lo = i.floor();
    final hi = i.ceil();
    if (lo == hi) return _confidence[lo];
    final t = i - lo;
    return (_confidence[lo] + (_confidence[hi] - _confidence[lo]) * t)
        .clamp(0.0, 1.0);
  }
}
