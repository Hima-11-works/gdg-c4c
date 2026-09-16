import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../routing/app_router.dart';
import '../theme/app_theme.dart';

/// Router provider — created once, reads [onboardingDoneProvider] for
/// its redirect logic.
final routerProvider = Provider<GoRouter>((ref) {
  return createRouter(ref.container);
});

/// Root widget — configures MaterialApp.router with go_router and theme.
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
    );
  }
}
