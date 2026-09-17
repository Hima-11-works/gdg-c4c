/// Data Transfer Objects for the pollution API.
///
/// These map raw JSON responses to Dart objects. They live at the
/// data layer boundary — widgets and domain logic never see them.
/// Each DTO has a `toDomain()` method that converts to the
/// corresponding domain model.
library;

import '../../domain/models/models.dart';

// ── Air quality reading DTO ────────────────────────────────────────────

class AirQualityReadingDto {
  const AirQualityReadingDto({
    required this.aqi,
    this.pm25,
    this.primaryPollutant,
    required this.category,
    required this.recordedAt,
  });

  final int aqi;
  final double? pm25;
  final String? primaryPollutant;
  final String category;
  final String recordedAt;

  factory AirQualityReadingDto.fromJson(Map<String, dynamic> json) {
    return AirQualityReadingDto(
      aqi: json['aqi'] as int,
      pm25: (json['pm25'] as num?)?.toDouble(),
      primaryPollutant: json['primary_pollutant'] as String?,
      category: json['category'] as String,
      recordedAt: json['recorded_at'] as String,
    );
  }

  AirQualityReading toDomain() {
    return AirQualityReading(
      aqiCpcb: aqi,
      pm25: pm25,
      primaryPollutant: primaryPollutant,
      category: CpcbCategory.fromAqi(aqi),
      recordedAt: DateTime.parse(recordedAt),
    );
  }
}

// ── Forecast point DTO ─────────────────────────────────────────────────

class ForecastPointDto {
  const ForecastPointDto({
    required this.at,
    required this.aqi,
    this.pm25,
    required this.confidence,
  });

  final String at;
  final int aqi;
  final double? pm25;
  final double confidence;

  factory ForecastPointDto.fromJson(Map<String, dynamic> json) {
    return ForecastPointDto(
      at: json['at'] as String,
      aqi: json['aqi'] as int,
      pm25: (json['pm25'] as num?)?.toDouble(),
      confidence: (json['confidence'] as num).toDouble(),
    );
  }

  ForecastPoint toDomain() {
    return ForecastPoint(
      at: DateTime.parse(at),
      aqiCpcb: aqi,
      pm25: pm25,
      confidence: confidence,
    );
  }
}

// ── Nearby area DTO ────────────────────────────────────────────────────

class NearbyAreaDto {
  const NearbyAreaDto({
    required this.name,
    required this.latitude,
    required this.longitude,
    required this.aqiNow,
    required this.forecast,
    required this.trend,
    required this.distanceKm,
    required this.confidence,
  });

  final String name;
  final double latitude;
  final double longitude;
  final int aqiNow;
  final List<ForecastPointDto> forecast;
  final String trend;
  final double distanceKm;
  final double confidence;

  factory NearbyAreaDto.fromJson(Map<String, dynamic> json) {
    return NearbyAreaDto(
      name: json['name'] as String,
      latitude: (json['latitude'] as num).toDouble(),
      longitude: (json['longitude'] as num).toDouble(),
      aqiNow: json['aqi_now'] as int,
      forecast: (json['forecast'] as List<dynamic>)
          .map((e) => ForecastPointDto.fromJson(e as Map<String, dynamic>))
          .toList(),
      trend: json['trend'] as String,
      distanceKm: (json['distance_km'] as num).toDouble(),
      confidence: (json['confidence'] as num).toDouble(),
    );
  }

  NearbyArea toDomain() {
    return NearbyArea(
      location: LocationPoint(latitude: latitude, longitude: longitude, label: name),
      name: name,
      aqiNow: aqiNow,
      forecast: forecast.map((e) => e.toDomain()).toList(),
      trend: _parseTrend(trend),
      distanceKm: distanceKm,
      confidence: confidence,
    );
  }

  static AreaTrend _parseTrend(String value) {
    return switch (value) {
      'improving' => AreaTrend.improving,
      'worsening' => AreaTrend.worsening,
      _ => AreaTrend.stable,
    };
  }
}

// ── Pollution event DTO ────────────────────────────────────────────────

class PollutionEventDto {
  const PollutionEventDto({
    required this.id,
    required this.sourceArea,
    required this.expectedArrivalAt,
    required this.peakAqiEstimate,
    required this.confidence,
    required this.description,
  });

  final String id;
  final String sourceArea;
  final String expectedArrivalAt;
  final int peakAqiEstimate;
  final double confidence;
  final String description;

  factory PollutionEventDto.fromJson(Map<String, dynamic> json) {
    return PollutionEventDto(
      id: json['id'] as String,
      sourceArea: json['source_area'] as String,
      expectedArrivalAt: json['expected_arrival_at'] as String,
      peakAqiEstimate: json['peak_aqi_estimate'] as int,
      confidence: (json['confidence'] as num).toDouble(),
      description: json['description'] as String,
    );
  }

  PollutionEvent toDomain() {
    return PollutionEvent(
      id: id,
      sourceArea: sourceArea,
      expectedArrivalAt: DateTime.parse(expectedArrivalAt),
      peakAqiEstimate: peakAqiEstimate,
      confidence: confidence,
      description: description,
    );
  }
}

// ── Data freshness DTO ─────────────────────────────────────────────────

class DataFreshnessDto {
  const DataFreshnessDto({
    required this.retrievedAt,
    required this.quality,
    this.nextRefreshEta,
  });

  final String retrievedAt;
  final String quality;
  final String? nextRefreshEta;

  factory DataFreshnessDto.fromJson(Map<String, dynamic> json) {
    return DataFreshnessDto(
      retrievedAt: json['retrieved_at'] as String,
      quality: json['quality'] as String,
      nextRefreshEta: json['next_refresh_eta'] as String?,
    );
  }

  DataFreshness toDomain() {
    return DataFreshness(
      retrievedAt: DateTime.parse(retrievedAt),
      quality: _parseQuality(quality),
      nextRefreshEta: nextRefreshEta != null ? DateTime.parse(nextRefreshEta!) : null,
    );
  }

  static DataQuality _parseQuality(String value) {
    return switch (value) {
      'full' => DataQuality.full,
      'partial' => DataQuality.partial,
      'forecast_unavailable' => DataQuality.forecastUnavailable,
      _ => DataQuality.stale,
    };
  }
}
