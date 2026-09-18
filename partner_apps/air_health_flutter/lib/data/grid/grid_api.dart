/// App-side adapter for the platform's grid API (`/api/v1/...`).
///
/// The backend is unauthenticated and serves H3-cell data:
///   GET /api/v1/grid/current   → [{h3_cell, timestamp, confidence, pm25, pdi, ...}]
///   GET /api/v1/grid/forecast  → [{h3_cell, forecast_time, predicted_pm25, ...}]
///   GET /api/v1/weather        → [{h3_cell, latitude, longitude, ...}]
///   GET /api/v1/alerts         → [{h3_cell, severity, message, ...}]
///
/// Every response is `{generated_at, is_demo, data}`. Note that grid cells
/// carry no coordinates: `/weather` is the only endpoint that reports a
/// cell's lat/lon, so the provider joins the two on `h3_cell` to locate a
/// point. [GridApiClient] is an interface so the mapping in
/// `GridApiPollutionDataProvider` can be tested with an in-memory fake.
library;

import 'package:dio/dio.dart';

import '../../domain/models/models.dart';

/// Bounding box for a level-of-detail read (all four edges, WGS84 degrees).
class GeoBounds {
  const GeoBounds({
    required this.minLat,
    required this.minLon,
    required this.maxLat,
    required this.maxLon,
  });

  /// A square box of ±[radiusDeg] degrees around [center].
  factory GeoBounds.around(LocationPoint center, double radiusDeg) {
    return GeoBounds(
      minLat: center.latitude - radiusDeg,
      minLon: center.longitude - radiusDeg,
      maxLat: center.latitude + radiusDeg,
      maxLon: center.longitude + radiusDeg,
    );
  }

  final double minLat;
  final double minLon;
  final double maxLat;
  final double maxLon;

  Map<String, dynamic> toQuery() => <String, dynamic>{
        'min_lat': minLat,
        'min_lon': minLon,
        'max_lat': maxLat,
        'max_lon': maxLon,
      };
}

/// `{generated_at, is_demo, data}` — every grid API response.
class GridEnvelope<T> {
  const GridEnvelope({
    required this.generatedAt,
    required this.isDemo,
    required this.data,
  });

  final DateTime generatedAt;
  final bool isDemo;
  final T data;
}

/// One cell's current state (`/grid/current`).
class GridStateDto {
  const GridStateDto({
    required this.h3Cell,
    required this.timestamp,
    required this.confidence,
    this.pm25,
    this.pdi,
    this.windSpeed,
    this.windDirection,
  });

  final String h3Cell;
  final DateTime timestamp;
  final double confidence;
  final double? pm25;
  final double? pdi;
  final double? windSpeed;
  final double? windDirection;

  factory GridStateDto.fromJson(Map<String, dynamic> json) => GridStateDto(
        h3Cell: json['h3_cell'] as String,
        timestamp: DateTime.parse(json['timestamp'] as String),
        confidence: (json['confidence'] as num).toDouble(),
        pm25: (json['pm25'] as num?)?.toDouble(),
        pdi: (json['pdi'] as num?)?.toDouble(),
        windSpeed: (json['wind_speed'] as num?)?.toDouble(),
        windDirection: (json['wind_direction'] as num?)?.toDouble(),
      );
}

/// One cell at one forecast horizon (`/grid/forecast?minutes=`).
class ForecastDto {
  const ForecastDto({
    required this.h3Cell,
    required this.generatedAt,
    required this.forecastTime,
    required this.forecastMinutes,
    required this.predictedPm25,
    required this.confidence,
  });

  final String h3Cell;
  final DateTime generatedAt;
  final DateTime forecastTime;
  final int forecastMinutes;
  final double predictedPm25;
  final double confidence;

  factory ForecastDto.fromJson(Map<String, dynamic> json) {
    final hours = (json['forecast_hours'] as num?)?.toDouble();
    final minutes = json['forecast_minutes'] != null
        ? (json['forecast_minutes'] as num).toInt()
        : ((hours ?? 0) * 60).round();
    return ForecastDto(
      h3Cell: json['h3_cell'] as String,
      generatedAt: DateTime.parse(json['generated_at'] as String),
      forecastTime: DateTime.parse(json['forecast_time'] as String),
      forecastMinutes: minutes,
      predictedPm25: (json['predicted_pm25'] as num).toDouble(),
      confidence: (json['confidence'] as num).toDouble(),
    );
  }
}

/// One cell's latest weather (`/weather`) — the app's source of coordinates.
class WeatherDto {
  const WeatherDto({
    required this.h3Cell,
    required this.latitude,
    required this.longitude,
    required this.measuredAt,
    this.windSpeed,
    this.windDirection,
    this.precipitation,
    this.boundaryLayerHeight,
    this.temperature,
    this.humidity,
  });

  final String h3Cell;
  final double latitude;
  final double longitude;
  final DateTime measuredAt;
  final double? windSpeed;
  final double? windDirection;
  final double? precipitation;
  final double? boundaryLayerHeight;
  final double? temperature;
  final double? humidity;

  factory WeatherDto.fromJson(Map<String, dynamic> json) => WeatherDto(
        h3Cell: json['h3_cell'] as String,
        latitude: (json['latitude'] as num).toDouble(),
        longitude: (json['longitude'] as num).toDouble(),
        measuredAt: DateTime.parse(json['measured_at'] as String),
        windSpeed: (json['wind_speed'] as num?)?.toDouble(),
        windDirection: (json['wind_direction'] as num?)?.toDouble(),
        precipitation: (json['precipitation'] as num?)?.toDouble(),
        boundaryLayerHeight: (json['boundary_layer_height'] as num?)?.toDouble(),
        temperature: (json['temperature'] as num?)?.toDouble(),
        humidity: (json['humidity'] as num?)?.toDouble(),
      );
}

/// One alert (`/alerts`).
class AlertDto {
  const AlertDto({
    required this.h3Cell,
    required this.severity,
    required this.message,
    required this.createdAt,
    this.currentPm25,
    this.forecastPm25,
    this.forecastHours,
    this.confidence,
    this.forecastTime,
  });

  final String h3Cell;
  final String severity;
  final String message;
  final DateTime createdAt;
  final double? currentPm25;
  final double? forecastPm25;
  final double? forecastHours;
  final double? confidence;
  final DateTime? forecastTime;

  factory AlertDto.fromJson(Map<String, dynamic> json) => AlertDto(
        h3Cell: json['h3_cell'] as String,
        severity: json['severity'] as String,
        message: json['message'] as String,
        createdAt: DateTime.parse(json['created_at'] as String),
        currentPm25: (json['current_pm25'] as num?)?.toDouble(),
        forecastPm25: (json['forecast_pm25'] as num?)?.toDouble(),
        forecastHours: (json['forecast_hours'] as num?)?.toDouble(),
        confidence: (json['confidence'] as num?)?.toDouble(),
        forecastTime: json['forecast_time'] == null
            ? null
            : DateTime.parse(json['forecast_time'] as String),
      );
}

/// The grid API surface the adapter needs. Implemented by [DioGridApiClient]
/// and by in-memory fakes in tests.
abstract class GridApiClient {
  Future<GridEnvelope<List<GridStateDto>>> current({
    GeoBounds? bounds,
    int? resolution,
  });

  Future<GridEnvelope<List<ForecastDto>>> forecast({
    required int minutes,
    GeoBounds? bounds,
    int? resolution,
  });

  Future<GridEnvelope<List<WeatherDto>>> weather({
    GeoBounds? bounds,
    int? resolution,
  });

  Future<GridEnvelope<List<AlertDto>>> alerts();
}

/// Dio-backed [GridApiClient]. [dio]'s base URL is the API origin
/// (e.g. `http://localhost:8000`); this class adds the `/api/v1` prefix.
class DioGridApiClient implements GridApiClient {
  DioGridApiClient({required Dio dio}) : _dio = dio;

  final Dio _dio;

  static const _apiPrefix = '/api/v1';

  @override
  Future<GridEnvelope<List<GridStateDto>>> current({
    GeoBounds? bounds,
    int? resolution,
  }) async {
    final json = await _get('$_apiPrefix/grid/current', _query(bounds, resolution));
    return _parseEnvelope(
      json,
      (data) => (data as List<dynamic>)
          .map((e) => GridStateDto.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }

  @override
  Future<GridEnvelope<List<ForecastDto>>> forecast({
    required int minutes,
    GeoBounds? bounds,
    int? resolution,
  }) async {
    final query = _query(bounds, resolution)..['minutes'] = minutes;
    final json = await _get('$_apiPrefix/grid/forecast', query);
    return _parseEnvelope(
      json,
      (data) => (data as List<dynamic>)
          .map((e) => ForecastDto.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }

  @override
  Future<GridEnvelope<List<WeatherDto>>> weather({
    GeoBounds? bounds,
    int? resolution,
  }) async {
    final json = await _get('$_apiPrefix/weather', _query(bounds, resolution));
    return _parseEnvelope(
      json,
      (data) => (data as List<dynamic>)
          .map((e) => WeatherDto.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }

  @override
  Future<GridEnvelope<List<AlertDto>>> alerts() async {
    final json = await _get('$_apiPrefix/alerts', const <String, dynamic>{});
    return _parseEnvelope(
      json,
      (data) => (data as List<dynamic>)
          .map((e) => AlertDto.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }

  Future<Map<String, dynamic>> _get(
    String path,
    Map<String, dynamic> query,
  ) async {
    final response = await _dio.get<Map<String, dynamic>>(
      path,
      queryParameters: query.isEmpty ? null : query,
    );
    return response.data!;
  }

  static Map<String, dynamic> _query(GeoBounds? bounds, int? resolution) {
    return <String, dynamic>{
      if (resolution != null) 'resolution': resolution,
      if (bounds != null) ...bounds.toQuery(),
    };
  }

  static GridEnvelope<T> _parseEnvelope<T>(
    Map<String, dynamic> json,
    T Function(dynamic data) parseData,
  ) {
    return GridEnvelope<T>(
      generatedAt: DateTime.parse(json['generated_at'] as String),
      isDemo: json['is_demo'] as bool? ?? false,
      data: parseData(json['data']),
    );
  }
}
