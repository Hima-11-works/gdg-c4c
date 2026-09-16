import 'cpcb_category.dart';

/// A single hourly forecast data point.
class ForecastPoint {
  const ForecastPoint({
    required this.at,
    required this.aqiCpcb,
    this.pm25,
    required this.confidence,
  });

  /// Forecast valid time.
  final DateTime at;

  /// Predicted integer AQI.
  final int aqiCpcb;

  /// Predicted PM2.5, when available.
  final double? pm25;

  /// Provider confidence in [0.0, 1.0].
  final double confidence;

  /// CPCB category for this forecast point.
  CpcbCategory get category => CpcbCategory.fromAqi(aqiCpcb);

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is ForecastPoint &&
          at == other.at &&
          aqiCpcb == other.aqiCpcb &&
          pm25 == other.pm25 &&
          confidence == other.confidence;

  @override
  int get hashCode => Object.hash(at, aqiCpcb, pm25, confidence);

  @override
  String toString() =>
      'ForecastPoint(at=$at, aqi=$aqiCpcb, conf=$confidence)';
}
