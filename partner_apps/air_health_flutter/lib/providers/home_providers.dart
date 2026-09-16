import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/formatters.dart';
import '../domain/models/models.dart';
import 'data_providers.dart';

// ── Location ───────────────────────────────────────────────────────────

/// The user's current location — hardcoded for the dummy provider.
/// Will be replaced by a real geolocation provider later.
const _defaultLocation = LocationPoint(
  latitude: 20.2961,
  longitude: 85.8245,
  label: 'Bhubaneswar',
);

final currentLocationProvider = Provider<LocationPoint>((ref) {
  return _defaultLocation;
});

// ── Air quality ────────────────────────────────────────────────────────

final currentAirQualityProvider = FutureProvider<AirQualityReading>((ref) {
  final provider = ref.read(pollutionDataProvider);
  final location = ref.read(currentLocationProvider);
  return provider.getCurrentAirQuality(location);
});

final forecastProvider = FutureProvider<List<ForecastPoint>>((ref) {
  final provider = ref.read(pollutionDataProvider);
  final location = ref.read(currentLocationProvider);
  return provider.getForecast(location, const Duration(hours: 12));
});

final nearbyAreasProvider = FutureProvider<List<NearbyArea>>((ref) {
  final provider = ref.read(pollutionDataProvider);
  final location = ref.read(currentLocationProvider);
  return provider.getNearbyAreas(location);
});

final pollutionEventsProvider = FutureProvider<List<PollutionEvent>>((ref) {
  final provider = ref.read(pollutionDataProvider);
  final location = ref.read(currentLocationProvider);
  return provider.getPollutionEvents(location);
});

final dataFreshnessProvider = FutureProvider<DataFreshness>((ref) {
  final provider = ref.read(pollutionDataProvider);
  return provider.getDataFreshness();
});

// ── Derived: trend ─────────────────────────────────────────────────────

/// Computes the AQI trend by comparing the current reading to the
/// average of the first 3 forecast hours.
final trendProvider = FutureProvider<AreaTrend>((ref) async {
  final current = await ref.watch(currentAirQualityProvider.future);
  final forecast = await ref.watch(forecastProvider.future);
  if (forecast.isEmpty) return AreaTrend.stable;
  final lookahead = forecast.take(3).toList();
  final avgForecast =
      lookahead.map((f) => f.aqiCpcb).reduce((a, b) => a + b) /
          lookahead.length;
  final delta = avgForecast - current.aqiCpcb;
  if (delta > 15) return AreaTrend.worsening;
  if (delta < -15) return AreaTrend.improving;
  return AreaTrend.stable;
});

// ── Derived: next category crossing ────────────────────────────────────

/// Finds the first forecast point that crosses into a higher CPCB
/// category than the current reading.
final nextCategoryCrossingProvider =
    FutureProvider<CategoryCrossing?>((ref) async {
  final current = await ref.watch(currentAirQualityProvider.future);
  final forecast = await ref.watch(forecastProvider.future);
  for (final point in forecast) {
    if (point.category.index > current.category.index) {
      return CategoryCrossing(
        category: point.category,
        at: point.at,
        aqi: point.aqiCpcb,
      );
    }
  }
  return null;
});

class CategoryCrossing {
  const CategoryCrossing({
    required this.category,
    required this.at,
    required this.aqi,
  });

  final CpcbCategory category;
  final DateTime at;
  final int aqi;

  String get message =>
      'Expected to reach ${category.label} around ${Formatters.time(at)}';
}
