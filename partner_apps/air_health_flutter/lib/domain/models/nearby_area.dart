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
}

enum AreaTrend { improving, stable, worsening }
