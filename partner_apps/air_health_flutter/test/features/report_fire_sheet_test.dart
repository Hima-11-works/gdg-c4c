import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:air_health_flutter/domain/models/fire_report.dart';
import 'package:air_health_flutter/domain/models/location_point.dart';
import 'package:air_health_flutter/features/reports/report_fire_sheet.dart';
import 'package:air_health_flutter/providers/data_providers.dart';
import 'package:air_health_flutter/providers/location_providers.dart';

/// Records submissions instead of touching the network.
class _RecordingFireReportApiClient implements FireReportApiClient {
  final submitted = <FireReportDraft>[];

  @override
  Future<FireReport> submitReport(FireReportDraft draft) async {
    submitted.add(draft);
    return FireReport(
      id: 1,
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
}

/// Always fails, to exercise the error path.
class _FailingFireReportApiClient implements FireReportApiClient {
  @override
  Future<FireReport> submitReport(FireReportDraft draft) async {
    throw Exception('connection refused');
  }

  @override
  Future<List<FireReport>> listActiveReports() async => const [];
}

const testLocation = LocationPoint(
  latitude: 20.2961,
  longitude: 85.8245,
  label: 'Bhubaneswar',
);

Future<void> pumpSheet(
  WidgetTester tester, [
  FireReportApiClient? client,
]) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        fireReportApiClientProvider.overrideWithValue(client),
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
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('renders the form: kinds, smoke slider, duration, note',
      (tester) async {
    await pumpSheet(tester, _RecordingFireReportApiClient());

    expect(find.text('Report a fire'), findsOneWidget);
    // All five fire kinds are offered with their labels.
    for (final kind in FireKind.values) {
      expect(find.text(kind.label), findsOneWidget);
    }
    // The five duration buckets.
    expect(find.text('Just started'), findsOneWidget);
    expect(find.text('More than 6 hours'), findsOneWidget);
    expect(find.byType(Slider), findsOneWidget);
    expect(find.byType(TextField), findsNWidgets(2));
    expect(find.text('Submit report'), findsOneWidget);
  });

  testWidgets('submit sends the picked values and pops the sheet',
      (tester) async {
    final client = _RecordingFireReportApiClient();
    await pumpSheet(tester, client);

    // Pick "Industrial fire" and keep the other defaults.
    await tester.tap(find.text('Industrial fire'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Submit report'));
    await tester.tap(find.text('Submit report'));
    await tester.pumpAndSettle();

    expect(client.submitted, hasLength(1));
    final draft = client.submitted.single;
    expect(draft.kind, FireKind.industrialFire);
    expect(draft.smokeIntensity, 3); // slider default
    expect(draft.durationHours, 0.0); // "Just started" default
    expect(draft.notes, isNull);
    expect(draft.clientReportId, isNotEmpty);
    expect(draft.latitude, testLocation.latitude);

    // The sheet popped on success.
    expect(find.byType(ReportFireSheet), findsNothing);
  });

  testWidgets('a failed submit keeps the sheet open and shows guidance',
      (tester) async {
    await pumpSheet(tester, _FailingFireReportApiClient());

    await tester.ensureVisible(find.text('Submit report'));
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
