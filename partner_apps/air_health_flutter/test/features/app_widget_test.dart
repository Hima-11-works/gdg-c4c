import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:air_health_flutter/app/app.dart';
import 'package:air_health_flutter/domain/models/fire_report.dart';
import 'package:air_health_flutter/domain/models/location_point.dart';
import 'package:air_health_flutter/providers/data_providers.dart';
import 'package:air_health_flutter/providers/location_providers.dart';
import 'package:air_health_flutter/providers/onboarding_providers.dart';
import 'package:air_health_flutter/providers/prefs_providers.dart';

/// Records submissions instead of touching the network.
class _RecordingFireReportApiClient implements FireReportApiClient {
  @override
  Future<FireReport> submitReport(FireReportDraft draft) async {
    throw UnimplementedError();
  }

  @override
  Future<List<FireReport>> listActiveReports() async => const [];
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
          gridApiClientProvider.overrideWithValue(null),
          fireReportApiClientProvider.overrideWithValue(null),
          citizenSensorApiClientProvider.overrideWithValue(null),
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
          gridApiClientProvider.overrideWithValue(null),
          fireReportApiClientProvider.overrideWithValue(null),
          citizenSensorApiClientProvider.overrideWithValue(null),
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
    // The null override keeps this test offline and verifies the no-backend state.
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          gridApiClientProvider.overrideWithValue(null),
          fireReportApiClientProvider.overrideWithValue(null),
          citizenSensorApiClientProvider.overrideWithValue(null),
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
          gridApiClientProvider.overrideWithValue(null),
          citizenSensorApiClientProvider.overrideWithValue(null),
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
