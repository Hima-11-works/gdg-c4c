import 'dart:async';
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:fire_dept_simulator/src/api.dart';

void main() {
  test('getFireDetection reads the exact persisted FIRMS row', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final receivedUri = Completer<Uri>();
    server.listen((request) async {
      receivedUri.complete(request.uri);
      request.response.headers.contentType = ContentType.json;
      request.response.write(jsonEncode({
        'data': {
          'detection_id': 'abc123',
          'h3_cell': '8828308281fffff',
          'product': 'VIIRS_NOAA21_NRT',
          'latitude': 28.6,
          'longitude': 77.2,
          'frp_mw': 18.4,
          'brightness_ti4_k': 332.1,
          'confidence_raw': 'n',
          'confidence_class': 'nominal',
          'acquired_at': '2026-10-21T10:00:00Z',
          'satellite': 'NOAA-21',
          'daynight': 'D',
        },
      }));
      await request.response.close();
    });
    final api = IncidentApi(
      baseUrl: 'http://${InternetAddress.loopbackIPv4.address}:${server.port}',
    );
    addTearDown(api.close);
    addTearDown(() async => server.close(force: true));

    final detectionId = 'a' * 64;
    final detection = await api.getFireDetection(detectionId);
    final uri = await receivedUri.future;

    expect(uri.path, '/api/v1/fires/$detectionId');
    expect(detection.id, 'abc123');
    expect(detection.product, 'VIIRS_NOAA21_NRT');
    expect(detection.confidenceClass, 'nominal');
  });

  test('getReport reads persisted report detail by id', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final receivedUri = Completer<Uri>();
    server.listen((request) async {
      receivedUri.complete(request.uri);
      request.response.headers.contentType = ContentType.json;
      request.response.write(jsonEncode({
        'data': {
          'id': 42,
          'h3_cell': '8828308281fffff',
          'latitude': 37.77,
          'longitude': -122.42,
          'kind': 'building_fire',
          'smoke_intensity': 4,
          'duration_hours': 1.5,
          'notes': 'Smoke from the second floor',
          'reported_at': '2026-10-21T10:00:00Z',
          'status': 'submitted',
          'status_meaning': 'Unverified report',
          'is_verified': false,
          'affects_air_quality_model': false,
        },
      }));
      await request.response.close();
    });
    final api = IncidentApi(
      baseUrl: 'http://${InternetAddress.loopbackIPv4.address}:${server.port}',
    );
    addTearDown(api.close);
    addTearDown(() async {
      await server.close(force: true);
    });

    final report = await api.getReport(42);
    final uri = await receivedUri.future;

    expect(uri.path, '/api/v1/reports/42');
    expect(report.id, 42);
    expect(report.notes, 'Smoke from the second floor');
  });
}
