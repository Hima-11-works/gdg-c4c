import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/data/reports/fire_report_api.dart';

void main() {
  group('Citizen report review status', () {
    test('parses current lifecycle state and model eligibility', () {
      final status = ReportReviewStatusDto.fromJson({
        'id': 42,
        'status': 'corroborated',
        'status_meaning': 'Confirmed by independent reports.',
        'affects_air_quality_model': true,
        'last_status_change_at': '2026-09-30T08:15:00Z',
        'expires_at': null,
        'evidence_count': 1,
        'evidence_expected': false,
      });

      expect(status.id, 42);
      expect(status.status, 'corroborated');
      expect(status.displayStatus, 'Accepted');
      expect(status.affectsAirQualityModel, isTrue);
      expect(status.evidenceCount, 1);
    });

    test('labels submitted reports as pending rather than accepted', () {
      final status = ReportReviewStatusDto.fromJson({
        'id': 8,
        'status': 'submitted',
        'status_meaning': 'Received and awaiting review.',
        'affects_air_quality_model': false,
      });

      expect(status.displayStatus, 'Received · awaiting review');
      expect(status.affectsAirQualityModel, isFalse);
    });

    test('requests the v2 detail endpoint and unwraps its envelope', () async {
      final dio = Dio(BaseOptions(baseUrl: 'https://air-health.test'));
      RequestOptions? capturedRequest;
      dio.interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
        capturedRequest = options;
        handler.resolve(Response<Map<String, dynamic>>(
          requestOptions: options,
          data: {
            'generated_at': '2026-09-30T08:15:00Z',
            'is_demo': false,
            'data': {
              'id': 71,
              'status': 'under_review',
              'status_meaning': 'Being reviewed by an authority.',
              'affects_air_quality_model': false,
            },
          },
        ));
      }));

      final result = await DioFireReportApiClient(dio: dio).getReviewStatus(71);

      expect(capturedRequest?.path, '/api/v2/reports/71');
      expect(result.id, 71);
      expect(result.displayStatus, 'Under review');
    });
  });
}
