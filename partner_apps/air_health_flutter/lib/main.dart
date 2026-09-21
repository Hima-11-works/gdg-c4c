import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'app/app.dart';
import 'mocks/dev_providers.dart';
import 'notifications/notification_service.dart';
import 'routing/app_router.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Initialise local notifications (no-op until permissions are granted).
  // Tapping an alert deep-links to the Alerts screen.
  await NotificationService().initialise(
    onNotificationTap: (payload) {
      if (payload != 'alerts') return;
      final context = rootNavigatorKey.currentContext;
      if (context == null) return;
      GoRouter.of(context).go('/alerts');
    },
  );

  runApp(
    ProviderScope(
      overrides: devProviderOverrides,
      child: const AirHealthApp(),
    ),
  );
}
