import 'package:dio/dio.dart';

/// Submission transport for an explicitly reported PM2.5 meter reading.
class CitizenSensorApiClient {
  CitizenSensorApiClient({required Dio dio}) : _dio = dio;

  final Dio _dio;

  Future<void> submit({
    required String submissionId,
    required double latitude,
    required double longitude,
    required double pm25UgM3,
    required String deviceLabel,
    required DateTime measuredAt,
  }) async {
    final response = await _dio.post<Map<String, dynamic>>(
      '/api/v1/sensors/citizen',
      data: {
        'client_submission_id': submissionId,
        'latitude': latitude,
        'longitude': longitude,
        'pm25_ugm3': pm25UgM3,
        'device_label': deviceLabel,
        'measured_at': measuredAt.toUtc().toIso8601String(),
        'consent': true,
      },
    );
    if (response.data == null || response.data!['data'] is! Map<String, dynamic>) {
      throw const FormatException('The server returned an invalid sensor-reading receipt.');
    }
  }
}
