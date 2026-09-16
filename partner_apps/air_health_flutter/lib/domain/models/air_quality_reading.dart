import 'cpcb_category.dart';

/// A point-in-time air quality measurement.
class AirQualityReading {
  const AirQualityReading({
    required this.aqiCpcb,
    this.pm25,
    this.primaryPollutant,
    required this.category,
    required this.recordedAt,
  });

  /// Integer AQI per CPCB methodology.
  final int aqiCpcb;

  /// PM2.5 concentration in µg/m³, when available.
  final double? pm25;

  /// Name of the dominant pollutant (e.g. "PM2.5").
  final String? primaryPollutant;

  /// CPCB category derived from [aqiCpcb].
  final CpcbCategory category;

  /// When this reading was taken (provider-side timestamp).
  final DateTime recordedAt;

  /// Convenience: is the air quality at or above "Poor"?
  bool get isPoorOrWorse =>
      category == CpcbCategory.poor ||
      category == CpcbCategory.veryPoor ||
      category == CpcbCategory.severe;
}
