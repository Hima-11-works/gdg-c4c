import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/app_info.dart';
import '../mocks/dev_simulator_panel.dart';
import '../providers/alert_providers.dart';
import '../routing/app_router.dart';
import '../theme/app_theme.dart';
import '../theme/app_colors.dart';

/// Router provider — created once, reads [onboardingDoneProvider] for
/// its redirect logic.
final routerProvider = Provider<GoRouter>((ref) {
  return createRouter(ref.container);
});

/// How often the app refreshes data and re-runs the alert engine while it is
/// running. (The engine de-duplicates, so a shorter interval is safe; 15 min
/// matches a reasonable air-quality refresh cadence.)
const alertRefreshInterval = Duration(minutes: 15);

/// Root widget — configures MaterialApp.router with go_router and theme.
///
/// Also owns the app-wide refresh/alert cadence: it refreshes the data used by
/// the alert engine on [alertRefreshInterval], on app resume, and once at
/// startup, so alerts fire without the user having to pull-to-refresh.
/// In debug mode, overlays the dev scenario simulator panel.
/// The entire tree is wrapped in a dark Container so the empty space
/// on left/right of the 430px mobile frame matches the dark theme.
class AirHealthApp extends ConsumerStatefulWidget {
  const AirHealthApp({super.key});

  @override
  ConsumerState<AirHealthApp> createState() => _AirHealthAppState();
}

class _AirHealthAppState extends ConsumerState<AirHealthApp>
    with WidgetsBindingObserver {
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Evaluate once after the first frame. The profile loads asynchronously,
    // so the coordinator simply no-ops until one exists.
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh());
    _refreshTimer = Timer.periodic(alertRefreshInterval, (_) => _refresh());
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refresh();
  }

  Future<void> _refresh() async {
    try {
      await ref.read(alertCoordinatorProvider).refreshAndEvaluate();
    } catch (_) {
      // Non-fatal: keep the last known data. The next tick retries, and each
      // screen already surfaces its own error state.
    }
  }

  @override
  Widget build(BuildContext context) {
    final router = ref.watch(routerProvider);

    return MaterialApp.router(
      title: AppInfo.name,
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      routerConfig: router,
      builder: (context, child) {
        // On mobile (Android / iOS), render the native UI directly without desktop container constraints
        if (!kIsWeb &&
            (defaultTargetPlatform == TargetPlatform.android ||
                defaultTargetPlatform == TargetPlatform.iOS)) {
          return child ?? const SizedBox.shrink();
        }

        // Dark desktop background — fills the entire window so the
        // sides of the 430px frame aren't blinding white.
        return Container(
          color: AppColors.surfaceDark,
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 430),
              child: ClipRect(
                child: Stack(
                  fit: StackFit.expand,
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
