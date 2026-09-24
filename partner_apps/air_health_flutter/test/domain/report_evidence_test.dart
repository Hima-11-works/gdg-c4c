import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:air_health_flutter/data/reports/fire_report_api.dart';
import 'package:air_health_flutter/domain/models/report_evidence.dart';

final _now = DateTime.utc(2026, 9, 24, 12);

CitizenSensorEvidence _reading({
  String raw = '87.5',
  String unit = 'µg/m³',
  DateTime? measuredAt,
  SensorPollutant pollutant = SensorPollutant.pm25,
}) =>
    CitizenSensorEvidence.tryCreate(
      rawValue: raw,
      unit: unit,
      measuredAt: measuredAt ?? _now,
      pollutant: pollutant,
      now: _now,
    )!;

void main() {
  group('CitizenSensorEvidence.tryCreate', () {
    test('accepts a non-negative number and keeps its unit', () {
      final reading = _reading();
      expect(reading.value, 87.5);
      expect(reading.unit, 'µg/m³');
      expect(reading.pollutant, SensorPollutant.pm25);
      expect(reading.display, '87.5 µg/m³');
    });

    test('rejects blank, non-numeric and negative input', () {
      expect(_readingOrNull(raw: ''), isNull);
      expect(_readingOrNull(raw: 'abc'), isNull);
      expect(_readingOrNull(raw: '-1'), isNull);
      expect(_readingOrNull(unit: '  '), isNull);
    });

    test('refuses a reading older than the backend accepts', () {
      expect(
        _readingOrNull(measuredAt: _now.subtract(const Duration(hours: 73))),
        isNull,
        reason: 'CITIZEN_SENSOR_MAX_AGE_HOURS is 72',
      );
      expect(
        _readingOrNull(measuredAt: _now.subtract(const Duration(hours: 71))),
        isNotNull,
      );
    });

    test('refuses a reading too far in the future', () {
      expect(
        _readingOrNull(measuredAt: _now.add(const Duration(seconds: 301))),
        isNull,
        reason: 'CITIZEN_SENSOR_MAX_FUTURE_SKEW_SECONDS is 300',
      );
      expect(
        _readingOrNull(measuredAt: _now.add(const Duration(seconds: 299))),
        isNotNull,
      );
    });

    test('sends all four parts together, with a timezone-aware timestamp', () {
      final fields = _reading().toFormFields();
      expect(fields.keys.toSet(), {
        'sensor_pollutant',
        'sensor_value',
        'sensor_unit',
        'sensor_measured_at',
      });
      expect(fields['sensor_pollutant'], 'pm25');
      expect(fields['sensor_value'], '87.5');
      expect(fields['sensor_unit'], 'µg/m³');
      expect(fields['sensor_measured_at'], endsWith('Z'));
    });
  });

  group('EvidencePhoto', () {
    EvidencePhoto photo(String type, int size) => EvidencePhoto(
          name: 'p',
          contentType: type,
          bytes: Uint8List(size),
        );

    test('accepts the allow-listed types', () {
      expect(photo('image/jpeg', 10).typeIsAcceptable, isTrue);
      expect(photo('image/png', 10).typeIsAcceptable, isTrue);
      expect(photo('image/webp', 10).typeIsAcceptable, isTrue);
    });

    test('treats a generic declared type as acceptable, not as a failure', () {
      expect(photo('application/octet-stream', 10).typeIsAcceptable, isTrue);
      expect(photo('', 10).typeIsAcceptable, isTrue);
      expect(photo('*/*', 10).typeIsAcceptable, isTrue);
    });

    test('refuses a declared type outside the allow-list', () {
      expect(photo('application/pdf', 10).typeIsAcceptable, isFalse);
      expect(photo('image/gif', 10).typeIsAcceptable, isFalse);
    });

    test('checks the size cap before anything is uploaded', () {
      expect(photo('image/png', 1024).isWithinSizeCap, isTrue);
      expect(
        photo('image/png', EvidencePhoto.defaultMaxBytes + 1).isWithinSizeCap,
        isFalse,
      );
    });
  });

  group('EvidenceUploadException', () {
    test('a transport failure carries no HTTP status and is retryable', () {
      const failure = EvidenceUploadException(
        code: 'network_error',
        message: 'interrupted',
      );
      expect(failure.statusCode, isNull);
      expect(failure.isRetryable, isTrue,
          reason: 'nothing is known about what reached the server');
    });

    test('a refusal is not retryable', () {
      for (final code in const [
        'media_content_invalid',
        'unrecognized_media_content',
        'media_content_mismatch',
        'unsupported_media_type',
        'media_too_large',
        'validation_error',
        'not_found',
        'conflict',
      ]) {
        final failure = EvidenceUploadException(code: code, message: '', statusCode: 415);
        expect(failure.isRetryable, isFalse, reason: '$code fails the same way again');
      }
    });

    test('a storage failure is retryable, as the backend asks', () {
      for (final code in const ['media_unavailable', 'media_not_durable']) {
        final failure = EvidenceUploadException(code: code, message: '', statusCode: 503);
        expect(failure.isRetryable, isTrue, reason: code);
      }
    });
  });

  group('ReportEvidence.fromJson', () {
    Map<String, dynamic> payload({
      String? status = 'unverified',
      Map<String, dynamic>? media,
      Map<String, dynamic>? sensor,
    }) =>
        {
          'id': 42,
          'report_id': 7,
          'client_report_id': 'ev-1',
          'verification_status': status,
          'media': media,
          'sensor': sensor,
          'notes': 'clip on the railing',
          'submitted_at': '2026-09-24T09:10:00Z',
        };

    test('reads a photo-only record', () {
      final evidence = ReportEvidence.fromJson(payload(
        media: {
          'content_type': 'image/png',
          'byte_size': 184320,
          'sha256': 'b1c2',
          'url': '/api/v1/reports/7/evidence/photo',
          'is_placeholder': false,
        },
      ));
      expect(evidence.hasPhoto, isTrue);
      expect(evidence.hasSensor, isFalse);
      expect(evidence.media!.byteSize, 184320);
      expect(evidence.verificationStatus, EvidenceVerificationStatus.unverified);
    });

    test('reads a sensor-only record and keeps it untrusted', () {
      final evidence = ReportEvidence.fromJson(payload(
        sensor: {
          'pollutant': 'pm25',
          'value': 87.5,
          'unit': 'ug/m3',
          'measured_at': '2026-09-24T09:10:00Z',
          'latitude': 28.55,
          'longitude': 77.2,
          'source': 'citizen',
          'verified': false,
        },
      ));
      expect(evidence.sensor!.source, 'citizen');
      expect(evidence.sensor!.verified, isFalse);
      expect(evidence.sensor!.value, 87.5);
    });

    test('an unknown verification state is never presented as verified', () {
      final evidence = ReportEvidence.fromJson(payload(status: 'something_new'));
      expect(evidence.verificationStatus, EvidenceVerificationStatus.unverified);
    });

    test('moderation states round-trip', () {
      for (final status in EvidenceVerificationStatus.values) {
        expect(
          EvidenceVerificationStatus.fromWire(status.wireValue),
          status,
        );
      }
    });
  });
}

CitizenSensorEvidence? _readingOrNull({
  String raw = '',
  String unit = 'µg/m³',
  DateTime? measuredAt,
}) =>
    CitizenSensorEvidence.tryCreate(
      rawValue: raw,
      unit: unit,
      measuredAt: measuredAt ?? _now,
      pollutant: SensorPollutant.pm25,
      now: _now,
    );
