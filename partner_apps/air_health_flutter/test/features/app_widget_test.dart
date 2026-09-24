import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:air_health_flutter/app/app.dart';
import 'package:air_health_flutter/data/reports/fire_report_api.dart';
import 'package:air_health_flutter/domain/models/fire_report.dart';
import 'package:air_health_flutter/domain/models/location_point.dart';
import 'package:air_health_flutter/domain/models/report_evidence.dart';
import 'package:air_health_flutter/providers/data_providers.dart';
import 'package:air_health_flutter/providers/location_providers.dart';
import 'package:air_health_flutter/providers/onboarding_providers.dart';
import 'package:air_health_flutter/providers/prefs_providers.dart';

/// This suite only checks that the report entry point appears, so the client's
/// methods are stubs: nothing here submits anything.
class _RecordingFireReportApiClient implements FireReportApiClient {
  @override
  Future<FireReport> submitReport(FireReportDraft draft) async {
    throw UnimplementedError();
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

void main() {
  const testLocation = LocationPoint(
    latitude: 20.2961,
    longitude: 85.8245,
    label: 'Bhubaneswar',
  );

  testWidgets('App renders and shows bottom navigation', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          onboardingDoneProvider.overrideWith((ref) async => true),
          onboardingCompleteProvider.overrideWith((ref) async => true),
          currentLocationProvider.overrideWith((ref) async => testLocation),
          resolvedLocationProvider.overrideWithValue(testLocation),
        ],
        child: const AirHealthApp(),
      ),
    );
    await tester.pumpAndSettle();

    // Bottom navigation destinations should be present.
    expect(find.text('Home'), findsOneWidget);
    expect(find.text('Nearby'), findsOneWidget);
    expect(find.text('Alerts'), findsOneWidget);
    expect(find.text('Profile'), findsOneWidget);
  });

  testWidgets('Home screen is shown by default', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          onboardingDoneProvider.overrideWith((ref) async => true),
          onboardingCompleteProvider.overrideWith((ref) async => true),
          currentLocationProvider.overrideWith((ref) async => testLocation),
          resolvedLocationProvider.overrideWithValue(testLocation),
        ],
        child: const AirHealthApp(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Air Health'), findsOneWidget);
  });

  testWidgets('the report-a-fire FAB is hidden without a backend',
      (tester) async {
    // Tests run without POLLUTION_API_BASE_URL, and an explicit null
    // override makes the intent explicit: no backend -> no entry point.
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          fireReportApiClientProvider.overrideWithValue(null),
          onboardingDoneProvider.overrideWith((ref) async => true),
          onboardingCompleteProvider.overrideWith((ref) async => true),
          currentLocationProvider.overrideWith((ref) async => testLocation),
          resolvedLocationProvider.overrideWithValue(testLocation),
        ],
        child: const AirHealthApp(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Report fire'), findsNothing);
  });

  testWidgets('the report-a-fire FAB appears once a backend is configured',
      (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          fireReportApiClientProvider.overrideWithValue(
            _RecordingFireReportApiClient(),
          ),
          onboardingDoneProvider.overrideWith((ref) async => true),
          onboardingCompleteProvider.overrideWith((ref) async => true),
          currentLocationProvider.overrideWith((ref) async => testLocation),
          resolvedLocationProvider.overrideWithValue(testLocation),
        ],
        child: const AirHealthApp(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Report fire'), findsOneWidget);
  });
}
