import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:air_health_flutter/app/app.dart';
import 'package:air_health_flutter/domain/models/location_point.dart';
import 'package:air_health_flutter/providers/location_providers.dart';
import 'package:air_health_flutter/providers/onboarding_providers.dart';
import 'package:air_health_flutter/providers/prefs_providers.dart';

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
}
