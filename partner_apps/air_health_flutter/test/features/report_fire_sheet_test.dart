import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:air_health_flutter/data/reports/fire_report_api.dart';
import 'package:air_health_flutter/domain/models/fire_report.dart';
import 'package:air_health_flutter/domain/models/location_point.dart';
import 'package:air_health_flutter/domain/models/report_evidence.dart';
import 'package:air_health_flutter/features/reports/report_fire_sheet.dart';
import 'package:air_health_flutter/providers/data_providers.dart';
import 'package:air_health_flutter/providers/location_providers.dart';
import 'package:air_health_flutter/services/evidence_photo_picker.dart';

/// A one-pixel PNG: enough bytes to be a real photo for the fake.
final _tinyPng = Uint8List.fromList(const [0x89, 0x50, 0x4E, 0x47]);

EvidencePhoto _photo() => EvidencePhoto(
      name: 'smoke.jpg',
      contentType: 'image/jpeg',
      bytes: _tinyPng,
    );

/// Hands back one photo, so the evidence path can be exercised without a
/// platform channel.
class _StubPhotoPicker implements EvidencePhotoPicker {
  _StubPhotoPicker(this.photo);

  final EvidencePhoto? photo;
  int calls = 0;

  @override
  Future<EvidencePhoto?> pickPhoto() async {
    calls++;
    return photo;
  }
}

class _RefusingPhotoPicker implements EvidencePhotoPicker {
  @override
  Future<EvidencePhoto?> pickPhoto() async {
    throw const EvidencePhotoPickerException(
      'unsupported_platform',
      'Choosing a photo is not available on this platform yet.',
    );
  }
}

/// Records submissions instead of touching the network.
class _RecordingFireReportApiClient implements FireReportApiClient {
  final submitted = <FireReportDraft>[];
  final evidenceSubmissions = <String>[];
  int reportId = 0;
  ReportEvidence? stored;

  @override
  Future<FireReport> submitReport(FireReportDraft draft) async {
    submitted.add(draft);
    reportId++;
    return FireReport(
      id: reportId,
      h3Cell: '8828308281fffff',
      latitude: draft.latitude,
      longitude: draft.longitude,
      kind: draft.kind,
      smokeIntensity: draft.smokeIntensity,
      durationHours: draft.durationHours,
      reportedAt: DateTime.utc(2026, 9, 21, 12),
    );
  }

  @override
  Future<List<FireReport>> listActiveReports() async => const [];

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
    onProgress?.call(5, 10);
    onProgress?.call(10, 10);
    stored = ReportEvidence(
      id: 1,
      reportId: reportId,
      clientReportId: clientReportId,
      verificationStatus: EvidenceVerificationStatus.unverified,
      media: photo == null
          ? null
          : ReportEvidenceMedia(
              contentType: photo.contentType,
              byteSize: photo.byteSize,
              sha256: 'abcdef0123456789',
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
    return stored!;
  }

  @override
  Future<ReportEvidence> fetchEvidence(int reportId) async {
    final record = stored;
    if (record == null) {
      throw const EvidenceUploadException(
        code: 'not_found',
        message: 'no evidence',
        statusCode: 404,
      );
    }
    return record;
  }
}

/// Creates the report, then fails the evidence upload [failures] times before
/// letting it through - the shape of an interrupted upload.
class _FlakyEvidenceClient extends _RecordingFireReportApiClient {
  _FlakyEvidenceClient(this.failures, {this.terminal = false});

  final int failures;
  final bool terminal;
  int attempts = 0;

  @override
  Future<ReportEvidence> submitEvidence({
    required int reportId,
    required String clientReportId,
    required EvidencePhoto? photo,
    required CitizenSensorEvidence? sensor,
    String? notes,
    void Function(int sent, int total)? onProgress,
  }) async {
    attempts++;
    evidenceSubmissions.add(clientReportId);
    if (attempts <= failures) {
      if (terminal) {
        throw const EvidenceUploadException(
          code: 'media_content_invalid',
          message: 'photo is not a complete image',
          statusCode: 415,
        );
      }
      throw const EvidenceUploadException(
        code: 'network_error',
        message: 'The upload was interrupted before the server answered.',
      );
    }
    return super.submitEvidence(
      reportId: reportId,
      clientReportId: clientReportId,
      photo: photo,
      sensor: sensor,
      notes: notes,
      onProgress: onProgress,
    );
  }
}

/// Always fails to create a report, to exercise the error path.
class _FailingFireReportApiClient implements FireReportApiClient {
  @override
  Future<FireReport> submitReport(FireReportDraft draft) async {
    throw Exception('connection refused');
  }

  @override
  Future<List<FireReport>> listActiveReports() async => const [];

  @override
  Future<ReportEvidence> submitEvidence({
    required int reportId,
    required String clientReportId,
    required EvidencePhoto? photo,
    required CitizenSensorEvidence? sensor,
    String? notes,
    void Function(int sent, int total)? onProgress,
  }) async =>
      throw UnimplementedError();

  @override
  Future<ReportEvidence> fetchEvidence(int reportId) async =>
      throw UnimplementedError();
}

const testLocation = LocationPoint(
  latitude: 20.2961,
  longitude: 85.8245,
  label: 'Bhubaneswar',
);

Future<void> pumpSheet(
  WidgetTester tester, {
  required FireReportApiClient? client,
  EvidencePhotoPicker? picker,
}) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        fireReportApiClientProvider.overrideWithValue(client),
        evidencePhotoPickerProvider
            .overrideWithValue(picker ?? _StubPhotoPicker(null)),
        currentLocationProvider.overrideWith((ref) async => testLocation),
        resolvedLocationProvider.overrideWithValue(testLocation),
      ],
      child: const MaterialApp(
        home: Scaffold(body: ReportFireSheet()),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('renders the form: kinds, smoke slider, duration, note',
      (tester) async {
    await pumpSheet(tester, client: _RecordingFireReportApiClient());

    expect(find.text('Report a fire'), findsOneWidget);
    for (final kind in FireKind.values) {
      expect(find.text(kind.label), findsOneWidget);
    }
    expect(find.text('Just started'), findsOneWidget);
    expect(find.text('More than 6 hours'), findsOneWidget);
    expect(find.byType(Slider), findsOneWidget);
    // The note, and the reading value. The reading is now *transmitted* as
    // evidence, so the sheet must not call it device-local any more.
    expect(find.byType(TextField), findsNWidgets(2));
    expect(find.text('Local sensor reading (optional)'), findsOneWidget);
    expect(find.textContaining('Unverified'), findsWidgets);
    expect(find.textContaining('this device'), findsNothing);
    expect(find.text('Submit report'), findsOneWidget);
  });

  testWidgets('submit with no evidence creates the report and pops',
      (tester) async {
    final client = _RecordingFireReportApiClient();
    await pumpSheet(tester, client: client);

    await tester.tap(find.text('Industrial fire'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(client.submitted, hasLength(1));
    final draft = client.submitted.single;
    expect(draft.kind, FireKind.industrialFire);
    expect(draft.smokeIntensity, 3);
    expect(draft.durationHours, 0.0);
    expect(draft.notes, isNull);
    expect(draft.clientReportId, isNotEmpty);
    expect(draft.latitude, testLocation.latitude);
    // Nothing was attached, so no evidence call is made at all.
    expect(client.evidenceSubmissions, isEmpty);
    expect(find.byType(ReportFireSheet), findsNothing);
  });

  testWidgets('a photo is uploaded as evidence after the report is created',
      (tester) async {
    final client = _RecordingFireReportApiClient();
    final picker = _StubPhotoPicker(_photo());
    await pumpSheet(tester, client: client, picker: picker);

    await tester.tap(find.text('Choose a photo'));
    await tester.pumpAndSettle();
    expect(find.textContaining('smoke.jpg'), findsOneWidget);

    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(client.submitted, hasLength(1));
    expect(client.evidenceSubmissions, hasLength(1));
    expect(picker.calls, 1);
    // The confirmation reports what the server stored, and its status.
    expect(find.text('Evidence stored on the server'), findsOneWidget);
    expect(find.textContaining('Unverified'), findsWidgets);
    expect(find.textContaining('never becomes a station observation'),
        findsOneWidget);
  });

  testWidgets('a sensor reading is uploaded as evidence with its provenance',
      (tester) async {
    final client = _RecordingFireReportApiClient();
    await pumpSheet(tester, client: client);

    await tester.enterText(
        find.byKey(const Key('report-reading-value')), '87.5');
    await tester.pumpAndSettle();
    await tester.tap(find.byType(CheckboxListTile));
    await tester.pumpAndSettle();
    expect(find.textContaining('Attach 87.5 µg/m³ as evidence'), findsOneWidget);

    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(client.evidenceSubmissions, hasLength(1));
    final stored = client.stored!;
    expect(stored.sensor!.value, 87.5);
    expect(stored.sensor!.pollutant, 'pm25');
    // A citizen reading is never presented as a measurement.
    expect(stored.sensor!.verified, isFalse);
    expect(stored.sensor!.source, 'citizen');
  });

  testWidgets(
      'a failed evidence upload keeps the sheet open, keeps the report id, '
      'and retries against it', (tester) async {
    final client = _FlakyEvidenceClient(1);
    await pumpSheet(tester, client: client, picker: _StubPhotoPicker(_photo()));

    await tester.tap(find.text('Choose a photo'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    // The report was created and the sheet stayed open.
    expect(client.submitted, hasLength(1));
    expect(find.byType(ReportFireSheet), findsOneWidget);
    expect(find.text('Report stored'), findsOneWidget);
    expect(find.textContaining('already stored'), findsWidgets);
    expect(find.text('Retry evidence upload'), findsWidgets);

    await tester.tap(find.text('Retry evidence upload').first);
    await tester.pumpAndSettle();

    // The retry reused the same report (no second submission) and the same
    // evidence idempotency key.
    expect(client.submitted, hasLength(1));
    expect(client.evidenceSubmissions, hasLength(2));
    expect(client.evidenceSubmissions.toSet(), hasLength(1));
    expect(find.text('Evidence stored on the server'), findsOneWidget);
  });

  testWidgets('a terminal rejection is not dressed up as a retry that works',
      (tester) async {
    final client = _FlakyEvidenceClient(99, terminal: true);
    await pumpSheet(tester, client: client, picker: _StubPhotoPicker(_photo()));

    await tester.tap(find.text('Choose a photo'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(find.byType(ReportFireSheet), findsOneWidget);
    expect(find.textContaining('media_content_invalid'), findsOneWidget);
    expect(find.textContaining('HTTP 415'), findsOneWidget);
    expect(find.text('Send it again (after changing it above)'), findsWidgets);
  });

  testWidgets('an unavailable photo picker is reported, not hidden',
      (tester) async {
    final client = _RecordingFireReportApiClient();
    await pumpSheet(tester, client: client, picker: _RefusingPhotoPicker());

    await tester.tap(find.text('Choose a photo'));
    await tester.pumpAndSettle();

    expect(find.textContaining('not available on this platform'), findsOneWidget);
    // A reading can still be attached without a photo.
    await tester.enterText(
        find.byKey(const Key('report-reading-value')), '40');
    await tester.pumpAndSettle();
    expect(find.byType(CheckboxListTile), findsOneWidget);
  });

  testWidgets('a failed report keeps the sheet open and shows guidance',
      (tester) async {
    await pumpSheet(tester, client: _FailingFireReportApiClient());

    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(find.byType(ReportFireSheet), findsOneWidget);
    expect(
      find.text('Submit report'),
      findsOneWidget,
      reason: 'the button re-enables so the user can retry',
    );
  });
}
