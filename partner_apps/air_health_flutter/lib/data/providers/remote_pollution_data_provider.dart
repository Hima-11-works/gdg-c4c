import 'package:dio/dio.dart';

import '../../domain/models/models.dart';
import '../dto/dto.dart';
import '../pollution_data_provider.dart';

/// Real-API implementation of [PollutionDataProvider].
///
/// Uses Dio for HTTP and DTOs for JSON→domain mapping.
/// Widgets never see raw JSON — every response is mapped to a
/// domain model before leaving this class.
///
/// To activate, change ONE line in [pollutionDataProvider]:
/// ```dart
/// final pollutionDataProvider = Provider<PollutionDataProvider>((ref) {
///   final config = ApiConfig.fromEnvironment();
///   return RemotePollutionDataProvider(dio: createPollutionDio(config));
/// });
/// ```
///
/// Nothing else in the app needs to change.
class RemotePollutionDataProvider implements PollutionDataProvider {
  RemotePollutionDataProvider({required Dio dio}) : _dio = dio;

  final Dio _dio;

  @override
  Future<AirQualityReading> getCurrentAirQuality(LocationPoint location) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/air-quality/current',
      queryParameters: {
        'lat': location.latitude,
        'lon': location.longitude,
      },
    );
    return AirQualityReadingDto.fromJson(response.data!).toDomain();
  }

  @override
  Future<List<ForecastPoint>> getForecast(
    LocationPoint location,
    Duration horizon,
  ) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/air-quality/forecast',
      queryParameters: {
        'lat': location.latitude,
        'lon': location.longitude,
        'hours': horizon.inHours,
      },
    );
    final items = response.data!['data'] as List<dynamic>;
    return items
        .map((e) => ForecastPointDto.fromJson(e as Map<String, dynamic>))
        .map((dto) => dto.toDomain())
        .toList();
  }

  @override
  Future<List<NearbyArea>> getNearbyAreas(LocationPoint location) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/nearby',
      queryParameters: {
        'lat': location.latitude,
        'lon': location.longitude,
      },
    );
    final items = response.data!['data'] as List<dynamic>;
    return items
        .map((e) => NearbyAreaDto.fromJson(e as Map<String, dynamic>))
        .map((dto) => dto.toDomain())
        .toList();
  }

  @override
  Future<List<PollutionEvent>> getPollutionEvents(LocationPoint location) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/events',
      queryParameters: {
        'lat': location.latitude,
        'lon': location.longitude,
      },
    );
    final items = response.data!['data'] as List<dynamic>;
    return items
        .map((e) => PollutionEventDto.fromJson(e as Map<String, dynamic>))
        .map((dto) => dto.toDomain())
        .toList();
  }

  @override
  Future<DataFreshness> getDataFreshness() async {
    final response = await _dio.get<Map<String, dynamic>>('/data-freshness');
    return DataFreshnessDto.fromJson(response.data!).toDomain();
  }
}
