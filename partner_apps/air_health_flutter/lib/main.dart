import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'app/app.dart';
import 'mocks/dev_providers.dart';
import 'notifications/notification_service.dart';
import 'routing/app_router.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  runApp(
    ProviderScope(
      overrides: devProviderOverrides,
      child: const AirHealthApp(),
    ),
  );

  // Initialise local notifications (non-blocking, safe from startup crashes).
  // Tapping an alert deep-links to the Alerts screen.
  try {
    await NotificationService().initialise(
      onNotificationTap: (payload) {
        if (payload != 'alerts') return;
        final context = rootNavigatorKey.currentContext;
        if (context == null) return;
        GoRouter.of(context).go('/alerts');
      },
    );
  } catch (error, stack) {
    debugPrint('Notification service initialization failed: $error\n$stack');
  }
}
