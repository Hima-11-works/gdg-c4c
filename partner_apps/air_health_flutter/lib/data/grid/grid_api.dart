/// App-side adapter for the platform's published grid API (`/api/v2/...`).
///
/// The backend is unauthenticated and serves H3-cell data:
///   GET /api/v2/grid/current   → {run_id, mode, data: [{h3_cell, valid_at, ...}]}
///   GET /api/v2/grid/forecast  → {run_id, mode, data: [{forecast_time, ...}]}
///   GET /api/v2/weather        → {run_id, mode, data: [{latitude, longitude, ...}]}
///   GET /api/v2/alerts         → {run_id, mode, data: [{severity, message, ...}]}
///
/// Every response is a run-pinned envelope. Grid cells include coordinates;
/// `/weather` is retained as a fallback for older or partial snapshots.
/// [GridApiClient] is an interface so the mapping in
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
    this.runId,
    this.mode,
  });

  final DateTime generatedAt;
  final bool isDemo;
  final T data;
  final String? runId;
  final String? mode;
}

/// Metadata for one immutable published scenario.
class GridPublication {
  const GridPublication({
    required this.runId,
    required this.generatedAt,
    required this.mode,
    required this.isDemo,
    required this.supportedForecastMinutes,
  });

  final String runId;
  final DateTime generatedAt;
  final String mode;
  final bool isDemo;
  final List<int> supportedForecastMinutes;
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
    this.latitude,
    this.longitude,
  });

  final String h3Cell;
  final DateTime timestamp;
  final double confidence;
  final double? pm25;
  final double? pdi;
  final double? windSpeed;
  final double? windDirection;
  final double? latitude;
  final double? longitude;

  factory GridStateDto.fromJson(Map<String, dynamic> json) => GridStateDto(
        h3Cell: json['h3_cell'] as String,
        timestamp: DateTime.parse(json['valid_at'] as String),
        confidence: (json['confidence'] as num).toDouble(),
        pm25: (json['pm25'] as num?)?.toDouble(),
        pdi: (json['pdi'] as num?)?.toDouble(),
        windSpeed: (json['wind_speed_ms'] as num?)?.toDouble(),
        windDirection: (json['wind_direction_deg'] as num?)?.toDouble(),
        latitude: (json['latitude'] as num?)?.toDouble(),
        longitude: (json['longitude'] as num?)?.toDouble(),
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
    final hours = (json['forecast_hours'] as num).toDouble();
    final minutes = (hours * 60).round();
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
        measuredAt: DateTime.parse(json['valid_at'] as String),
        windSpeed: (json['wind_speed_ms'] as num?)?.toDouble(),
        windDirection: (json['wind_direction_deg'] as num?)?.toDouble(),
        precipitation: (json['precipitation_mm'] as num?)?.toDouble(),
        boundaryLayerHeight: (json['boundary_layer_height_m'] as num?)?.toDouble(),
        temperature: (json['temperature_c'] as num?)?.toDouble(),
        humidity: (json['relative_humidity_pct'] as num?)?.toDouble(),
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
  /// Resolve one publication pointer and its supported forecast horizons.
  Future<GridPublication> latestPublication();

  Future<GridEnvelope<List<GridStateDto>>> current({
    GeoBounds? bounds,
    int? resolution,
    String? runId,
  });

  Future<GridEnvelope<List<ForecastDto>>> forecast({
    required int minutes,
    GeoBounds? bounds,
    int? resolution,
    String? runId,
  });

  Future<GridEnvelope<List<WeatherDto>>> weather({
    GeoBounds? bounds,
    int? resolution,
    String? runId,
  });

  Future<GridEnvelope<List<AlertDto>>> alerts({String? runId});
}

/// Dio-backed [GridApiClient]. [dio]'s base URL is the API origin
/// (e.g. `http://localhost:8000`); this class adds the `/api/v2` prefix.
class DioGridApiClient implements GridApiClient {
  DioGridApiClient({required Dio dio}) : _dio = dio;

  final Dio _dio;

  static const _apiPrefix = '/api/v2';
  GridPublication? _cachedPublication;
  DateTime? _publicationFetchedAt;
  Future<GridPublication>? _publicationRequest;

  @override
  Future<GridPublication> latestPublication() async {
    final fetchedAt = _publicationFetchedAt;
    if (_cachedPublication != null &&
        fetchedAt != null &&
        DateTime.now().difference(fetchedAt) < const Duration(minutes: 5)) {
      return _cachedPublication!;
    }
    final pending = _publicationRequest;
    if (pending != null) return pending;
    final request = _get('$_apiPrefix/meta', const <String, dynamic>{}).then((json) {
      final forecastMinutes = (json['supported_horizons_hours'] as List<dynamic>)
          .map((hours) => ((hours as num).toDouble() * 60).round())
          .toSet()
          .toList();
      forecastMinutes.sort();
      final publication = GridPublication(
        runId: json['latest_run_id'] as String,
        generatedAt: DateTime.parse(json['generated_at'] as String),
        mode: (json['data_mode'] as String).toLowerCase(),
        isDemo: json['data_mode'] == 'demo',
        supportedForecastMinutes: List<int>.unmodifiable(forecastMinutes),
      );
      _cachedPublication = publication;
      _publicationFetchedAt = DateTime.now();
      return publication;
    });
    _publicationRequest = request;
    try {
      return await request;
    } finally {
      _publicationRequest = null;
    }
  }

  @override
  Future<GridEnvelope<List<GridStateDto>>> current({
    GeoBounds? bounds,
    int? resolution,
    String? runId,
  }) async {
    final json = await _getPinned(
      '$_apiPrefix/grid/current',
      _query(bounds, resolution),
      runId: runId,
    );
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
    String? runId,
  }) async {
    final query = _query(bounds, resolution)..['hours'] = minutes / 60;
    final json = await _getPinned('$_apiPrefix/grid/forecast', query, runId: runId);
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
    String? runId,
  }) async {
    final json = await _getPinned(
      '$_apiPrefix/weather',
      _query(bounds, resolution),
      runId: runId,
    );
    return _parseEnvelope(
      json,
      (data) => (data as List<dynamic>)
          .map((e) => WeatherDto.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }

  @override
  Future<GridEnvelope<List<AlertDto>>> alerts({String? runId}) async {
    final json = await _getPinned(
      '$_apiPrefix/alerts',
      const <String, dynamic>{},
      runId: runId,
    );
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

  Future<Map<String, dynamic>> _getPinned(
    String path,
    Map<String, dynamic> query, {
    String? runId,
  ) async {
    final pinnedRunId = runId ?? (await latestPublication()).runId;
    return _get(path, <String, dynamic>{...query, 'run_id': pinnedRunId});
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
      runId: json['run_id'] as String?,
      mode: json['mode'] as String?,
      data: parseData(json['data']),
    );
  }
}
