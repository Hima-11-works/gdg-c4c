import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../mocks/dev_simulator_panel.dart';
import '../routing/app_router.dart';
import '../theme/app_theme.dart';

/// Router provider — created once, reads [onboardingDoneProvider] for
/// its redirect logic.
final routerProvider = Provider<GoRouter>((ref) {
  return createRouter(ref.container);
});

/// Root widget — configures MaterialApp.router with go_router and theme.
///
/// In debug mode, overlays the dev scenario simulator panel.
class AirHealthApp extends ConsumerWidget {
  const AirHealthApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);

    return MaterialApp.router(
      title: 'Air Health',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      routerConfig: router,
      builder: (context, child) {
        // In debug mode, overlay the simulator panel on top of the app.
        if (kDebugMode && child != null) {
          return Stack(
            children: [
              child,
              const DevSimulatorPanel(),
            ],
          );
        }
        return child ?? const SizedBox.shrink();
      },
    );
  }
}
