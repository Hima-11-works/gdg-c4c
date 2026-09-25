// Regression check for the envelope-unwrapping defect.
//
// GET/POST /api/v1/reports and /reports/{id}/evidence answer with the platform
// envelope `{generated_at, is_demo, data}`. The client returned the whole
// envelope where the interface promised the inner record, so every caller
// received an object with no `id` - which is exactly the field evidence is
// attached to. These assertions pin the unwrapped shape.
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:air_health_flutter/data/reports/fire_report_api.dart';
import 'package:air_health_flutter/domain/models/fire_report.dart';
import 'package:air_health_flutter/domain/models/report_evidence.dart';

/// A `Dio` whose adapter answers each path with a canned envelope, so the test
/// exercises the real parsing rather than a stub of it.
Dio _dioReturning(String Function(String path) body) {
  final dio = Dio(BaseOptions(baseUrl: 'http://localhost:8000'));
  dio.httpClientAdapter = _CannedAdapter(body);
  return dio;
}

class _CannedAdapter implements HttpClientAdapter {
  _CannedAdapter(this._body);

  final String Function(String path) _body;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final data = _body(options.path);
    return ResponseBody.fromString(
      '{"generated_at":"2026-09-25T11:00:00Z","is_demo":false,"data":$data}',
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

const _reportJson =
    '{"id":36,"h3_cell":"883da11467fffff","latitude":28.61,"longitude":77.2,'
    '"kind":"crop_burning","smoke_intensity":3,"duration_hours":2.0,'
    '"notes":"probe","client_report_id":"audit-probe-001",'
    '"reported_at":"2026-09-25T11:00:24Z"}';

const _evidenceJson =
    '{"id":28,"report_id":36,"client_report_id":"audit-probe-ev-001",'
    '"verification_status":"unverified","media":null,'
    '"sensor":{"pollutant":"pm25","value":78.5,"unit":"ug/m3",'
    '"measured_at":"2026-09-25T11:01:17Z","latitude":28.61,"longitude":77.2,'
    '"source":"citizen","verified":false},"notes":"probe",'
    '"submitted_at":"2026-09-25T11:01:17Z"}';

void main() {
  group('DioFireReportApiClient unwraps the platform envelope', () {
    test('submitReport returns the report, not the envelope', () async {
      final client = DioFireReportApiClient(
        dio: _dioReturning((path) => _reportJson),
      );
      final report = await client.submitReport(
        const FireReportDraft(
          latitude: 28.61,
          longitude: 77.2,
          kind: FireKind.cropBurning,
          smokeIntensity: 3,
          durationHours: 2,
        ),
      );
      // The defect: this was the envelope, so `id` was absent and the report
      // could not be given evidence.
      expect(report.id, 36);
      expect(report.clientReportId, 'audit-probe-001');
      expect(report.h3Cell, '883da11467fffff');
    });

    test('listActiveReports returns a list, not an envelope', () async {
      final client = DioFireReportApiClient(
        dio: _dioReturning((path) => '[$_reportJson]'),
      );
      final reports = await client.listActiveReports();
      expect(reports, isA<List<FireReport>>());
      expect(reports.single.id, 36);
    });

    test('fetchEvidence returns the evidence and keeps it unverified', () async {
      final client = DioFireReportApiClient(
        dio: _dioReturning((path) => _evidenceJson),
      );
      final evidence = await client.fetchEvidence(36);
      // The report id must survive the unwrap: it is what a retry reuses.
      expect(evidence.reportId, 36);
      expect(evidence.verificationStatus, EvidenceVerificationStatus.unverified);
      expect(evidence.sensor?.verified, isFalse);
      expect(evidence.sensor?.source, 'citizen');
    });
  });
}
