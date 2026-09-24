import 'package:flutter_test/flutter_test.dart';
import 'package:air_health_flutter/data/reports/fire_report_api.dart';
import 'package:air_health_flutter/domain/models/fire_report.dart';
import 'package:air_health_flutter/domain/models/report_evidence.dart';

/// An in-memory FireReportApiClient: records drafts and hands back a stored
/// report, idempotent on clientReportId like the backend.
class FakeFireReportApiClient implements FireReportApiClient {
  final stored = <FireReport>[];
  final submitted = <FireReportDraft>[];
  final evidenceSubmissions = <String>[];
  final evidence = <int, ReportEvidence>{};
  int _nextId = 1;

  @override
  Future<FireReport> submitReport(FireReportDraft draft) async {
    submitted.add(draft);
    final report = FireReport(
      id: _nextId++,
      h3Cell: '8828308281fffff',
      latitude: draft.latitude,
      longitude: draft.longitude,
      kind: draft.kind,
      smokeIntensity: draft.smokeIntensity,
      durationHours: draft.durationHours,
      reportedAt: DateTime.utc(2026, 9, 21, 12),
      notes: draft.notes,
      clientReportId: draft.clientReportId,
    );
    stored.add(report);
    return report;
  }

  @override
  Future<List<FireReport>> listActiveReports() async => List.of(stored);

  @override
  Future<ReportEvidence> submitEvidence({
    required int reportId,
    required String clientReportId,
    required EvidencePhoto? photo,
    required CitizenSensorEvidence? sensor,
    String? notes,
    void Function(int sent, int total)? onProgress,
  }) async {
    evidenceSubmissions.add(clientReportId);
    // Idempotent on (reportId, clientReportId), like the backend: a retry
    // returns the stored record rather than a second one.
    final existing = evidence[reportId];
    if (existing != null && existing.clientReportId == clientReportId) {
      return existing;
    }
    onProgress?.call(10, 10);
    final record = ReportEvidence(
      id: reportId,
      reportId: reportId,
      clientReportId: clientReportId,
      verificationStatus: EvidenceVerificationStatus.unverified,
      media: photo == null
          ? null
          : ReportEvidenceMedia(
              contentType: photo.contentType,
              byteSize: photo.byteSize,
              sha256: 'deadbeef',
              url: '/api/v1/reports/$reportId/evidence/photo',
              isPlaceholder: false,
            ),
      sensor: sensor == null
          ? null
          : ReportEvidenceSensor(
              pollutant: sensor.pollutant.wireValue,
              value: sensor.value,
              unit: sensor.unit,
              measuredAt: sensor.measuredAt,
              latitude: 28.55,
              longitude: 77.20,
              source: 'citizen',
              verified: false,
            ),
      notes: notes,
      submittedAt: DateTime.utc(2026, 9, 24, 12),
    );
    evidence[reportId] = record;
    return record;
  }

  @override
  Future<ReportEvidence> fetchEvidence(int reportId) async {
    final record = evidence[reportId];
    if (record == null) {
      throw const EvidenceUploadException(
        code: 'not_found',
        message: 'no evidence for this report',
        statusCode: 404,
      );
    }
    return record;
  }
}

void main() {
  group('FireReportDraft.create', () {
    test('accepts a valid draft and trims a blank note to null', () {
      final draft = FireReportDraft.create(
        latitude: 28.55,
        longitude: 77.20,
        kind: FireKind.cropBurning,
        smokeIntensity: 4,
        durationHours: 2.0,
        notes: '   ',
        clientReportId: 'abc',
      );

      expect(draft.notes, isNull);
      expect(draft.smokeIntensity, 4);
      expect(draft.kind.value, 'crop_burning');
    });

    test('rejects an out-of-range smoke slider', () {
      expect(
        () => FireReportDraft.create(
          latitude: 28.55,
          longitude: 77.20,
          kind: FireKind.forestFire,
          smokeIntensity: 0,
          durationHours: 1.0,
        ),
        throwsArgumentError,
      );
      expect(
        () => FireReportDraft.create(
          latitude: 28.55,
          longitude: 77.20,
          kind: FireKind.forestFire,
          smokeIntensity: 6,
          durationHours: 1.0,
        ),
        throwsArgumentError,
      );
    });

    test('rejects out-of-range coordinates and duration', () {
      expect(
        () => FireReportDraft.create(
          latitude: 91.0,
          longitude: 0.0,
          kind: FireKind.other,
          smokeIntensity: 3,
          durationHours: 1.0,
        ),
        throwsArgumentError,
      );
      expect(
        () => FireReportDraft.create(
          latitude: 0.0,
          longitude: 181.0,
          kind: FireKind.other,
          smokeIntensity: 3,
          durationHours: 1.0,
        ),
        throwsArgumentError,
      );
      expect(
        () => FireReportDraft.create(
          latitude: 0.0,
          longitude: 0.0,
          kind: FireKind.other,
          smokeIntensity: 3,
          durationHours: -1.0,
        ),
        throwsArgumentError,
      );
    });

    test('rejects notes over 280 characters', () {
      expect(
        () => FireReportDraft.create(
          latitude: 0.0,
          longitude: 0.0,
          kind: FireKind.other,
          smokeIntensity: 3,
          durationHours: 0.0,
          notes: 'x' * (FireReportDraft.maxNotesLength + 1),
        ),
        throwsArgumentError,
      );
    });

    test('duration options map to fractional hours the backend stores', () {
      expect(FireDurationOption.all, hasLength(5));
      expect(FireDurationOption.justStarted.hours, 0.0);
      expect(FireDurationOption.moreThanSixHours.hours, greaterThan(6.0));
      for (final option in FireDurationOption.all) {
        expect(option.hours >= 0 && option.hours <= 24, isTrue,
            reason: '${option.label} must be within the backend bound');
      }
    });
  });

  group('FakeFireReportApiClient (transport contract)', () {
    test('submit records the draft and returns a stored report', () async {
      final client = FakeFireReportApiClient();
      final draft = FireReportDraft.create(
        latitude: 28.55,
        longitude: 77.20,
        kind: FireKind.industrialFire,
        smokeIntensity: 5,
        durationHours: 2.0,
        notes: 'Plume visible from the road',
      );

      final report = await client.submitReport(draft);

      expect(report.id, 1);
      expect(report.kind, FireKind.industrialFire);
      expect(client.submitted, hasLength(1));
      expect(client.stored.single.id, report.id);
    });

    test('listActive returns what was submitted', () async {
      final client = FakeFireReportApiClient();
      await client.submitReport(
        FireReportDraft.create(
          latitude: 28.55,
          longitude: 77.20,
          kind: FireKind.buildingFire,
          smokeIntensity: 2,
          durationHours: 0.0,
        ),
      );

      expect(await client.listActiveReports(), hasLength(1));
    });
  });

  group('CitizenReportVerification', () {
    test('names the only true status of a resident submission', () {
      expect(CitizenReportVerification.label, 'Unverified');
      expect(CitizenReportVerification.badge, contains('Unverified'));
    });

    test('says the photo and reading are transmitted, not kept on the device',
        () {
      final detail = CitizenReportVerification.evidenceUploadDetail;
      expect(detail, contains('Sent to the backend'));
      expect(detail, contains('unverified evidence'));
      expect(detail, isNot(contains('not sent')));
      expect(detail, isNot(contains('this device')));
    });
  });
}
