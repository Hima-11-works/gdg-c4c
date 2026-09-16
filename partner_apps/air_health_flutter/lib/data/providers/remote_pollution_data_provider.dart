import 'package:dio/dio.dart';

import '../../domain/models/models.dart';
import '../pollution_data_provider.dart';

/// Skeleton for future real-API integration.
///
/// Every method throws [UnimplementedError] — implement once the
/// backend API contract is finalised. The app will never call this
/// until [pollutionDataProvider] is overridden to return it.
///
/// Uses [Dio] for HTTP — already in pubspec, ready to configure
/// interceptors, base URL, auth headers, etc.
class RemotePollutionDataProvider implements PollutionDataProvider {
  RemotePollutionDataProvider({Dio? dio}) : _dio = dio ?? Dio();

  // ignore: unused_field — will be used when endpoints are implemented
  final Dio _dio;

  @override
  Future<AirQualityReading> getCurrentAirQuality(LocationPoint location) {
    throw UnimplementedError('RemotePollutionDataProvider not yet implemented');
  }

  @override
  Future<List<ForecastPoint>> getForecast(
    LocationPoint location,
    Duration horizon,
  ) {
    throw UnimplementedError('RemotePollutionDataProvider not yet implemented');
  }

  @override
  Future<List<NearbyArea>> getNearbyAreas(LocationPoint location) {
    throw UnimplementedError('RemotePollutionDataProvider not yet implemented');
  }

  @override
  Future<List<PollutionEvent>> getPollutionEvents(LocationPoint location) {
    throw UnimplementedError('RemotePollutionDataProvider not yet implemented');
  }

  @override
  Future<DataFreshness> getDataFreshness() {
    throw UnimplementedError('RemotePollutionDataProvider not yet implemented');
  }
}
