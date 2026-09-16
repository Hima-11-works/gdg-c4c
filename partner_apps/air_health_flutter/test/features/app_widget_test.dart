import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:air_health_flutter/app/app.dart';

void main() {
  testWidgets('App renders and shows bottom navigation', (tester) async {
    await tester.pumpWidget(
      const ProviderScope(child: AirHealthApp()),
    );

    // Bottom navigation destinations should be present.
    expect(find.text('Home'), findsOneWidget);
    expect(find.text('Nearby'), findsOneWidget);
    expect(find.text('Alerts'), findsOneWidget);
    expect(find.text('Profile'), findsOneWidget);
  });

  testWidgets('Home screen is shown by default', (tester) async {
    await tester.pumpWidget(
      const ProviderScope(child: AirHealthApp()),
    );

    expect(find.text('Air Health'), findsOneWidget);
  });
}
