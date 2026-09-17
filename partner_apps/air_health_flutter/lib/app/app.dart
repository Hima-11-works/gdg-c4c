import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../mocks/dev_simulator_panel.dart';
import '../routing/app_router.dart';
import '../theme/app_theme.dart';
import '../theme/app_colors.dart';

/// Router provider — created once, reads [onboardingDoneProvider] for
/// its redirect logic.
final routerProvider = Provider<GoRouter>((ref) {
  return createRouter(ref.container);
});

/// Root widget — configures MaterialApp.router with go_router and theme.
///
/// In debug mode, overlays the dev scenario simulator panel.
/// The entire tree is wrapped in a dark Container so the empty space
/// on left/right of the 430px mobile frame matches the dark theme.
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
        // Dark desktop background — fills the entire window so the
        // sides of the 430px frame aren't blinding white.
        return Container(
          color: AppColors.surfaceDark,
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 430),
              child: ClipRect(
                child: Stack(
                  children: [
                    // The actual app content from the router.
                    child ?? const SizedBox.shrink(),
                    // Dev simulator — now inside the 430px frame,
                    // clipped by ClipRect so it never escapes.
                    if (kDebugMode) const DevSimulatorPanel(),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}
