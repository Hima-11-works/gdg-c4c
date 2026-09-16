import 'forecast_point.dart';
import 'location_point.dart';

/// An area near the user's location, with its own AQI snapshot.
class NearbyArea {
  const NearbyArea({
    required this.location,
    required this.name,
    required this.aqiNow,
    required this.forecast,
    required this.trend,
    required this.distanceKm,
    required this.confidence,
  });

  final LocationPoint location;
  final String name;
  final int aqiNow;
  final List<ForecastPoint> forecast;
  final AreaTrend trend;
  final double distanceKm;
  final double confidence;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is NearbyArea &&
          location == other.location &&
          name == other.name &&
          aqiNow == other.aqiNow &&
          _listEquals(forecast, other.forecast) &&
          trend == other.trend &&
          distanceKm == other.distanceKm &&
          confidence == other.confidence;

  @override
  int get hashCode => Object.hash(
        location,
        name,
        aqiNow,
        Object.hashAll(forecast),
        trend,
        distanceKm,
        confidence,
      );

  @override
  String toString() =>
      'NearbyArea($name, aqi=$aqiNow, trend=$trend, dist=${distanceKm}km)';

  static bool _listEquals<T>(List<T> a, List<T> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }
}

enum AreaTrend { improving, stable, worsening }
