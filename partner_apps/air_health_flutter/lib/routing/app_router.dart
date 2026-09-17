import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/home/home_screen.dart';
import '../features/nearby/nearby_screen.dart';
import '../features/alerts/alerts_screen.dart';
import '../features/profile/profile_screen.dart';
import '../features/onboarding/onboarding_screen.dart';
import '../providers/prefs_providers.dart';

/// Top-level router key so tests and notification handlers can navigate
/// without needing a BuildContext.
final rootNavigatorKey = GlobalKey<NavigatorState>();
final shellNavigatorKey = GlobalKey<NavigatorState>();

/// Builds the GoRouter with an onboarding redirect.
///
/// Accepts a [ProviderContainer] so the redirect can read
/// [onboardingDoneProvider] without needing a widget context.
GoRouter createRouter(ProviderContainer container) {
  return GoRouter(
    navigatorKey: rootNavigatorKey,
    initialLocation: '/home',
    redirect: (context, state) {
      final onboardingAsync = container.read(onboardingDoneProvider);
      // While loading, don't redirect — let the current route render.
      if (onboardingAsync.isLoading || onboardingAsync.hasError) return null;
      final done = onboardingAsync.value ?? false;
      final goingToOnboarding = state.matchedLocation == '/onboarding';

      if (!done && !goingToOnboarding) {
        return '/onboarding';
      }
      if (done && goingToOnboarding) {
        return '/home';
      }
      return null;
    },
    routes: [
      GoRoute(
        path: '/onboarding',
        builder: (context, state) => Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 430),
            child: const OnboardingScreen(),
          ),
        ),
      ),
      StatefulShellRoute.indexedStack(
        builder: (context, state, shell) => ScaffoldWithNav(shell: shell),
        branches: [
          StatefulShellBranch(
            navigatorKey: shellNavigatorKey,
            routes: [
              GoRoute(
                path: '/home',
                builder: (context, state) => const HomeScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/nearby',
                builder: (context, state) => const NearbyScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/alerts',
                builder: (context, state) => const AlertsScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/profile',
                builder: (context, state) => const ProfileScreen(),
              ),
            ],
          ),
        ],
      ),
    ],
  );
}

/// Shell scaffold with a bottom navigation bar.
/// Wrapped in Center + ConstrainedBox so the UI looks like a mobile app
/// even when run on Flutter Web desktop.
class ScaffoldWithNav extends StatelessWidget {
  const ScaffoldWithNav({super.key, required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 430),
        child: Scaffold(
          body: shell,
          bottomNavigationBar: NavigationBar(
            selectedIndex: shell.currentIndex,
            onDestinationSelected: (index) =>
                shell.goBranch(index, initialLocation: index == shell.currentIndex),
            destinations: const [
              NavigationDestination(
                icon: Icon(Icons.home_outlined),
                selectedIcon: Icon(Icons.home),
                label: 'Home',
              ),
              NavigationDestination(
                icon: Icon(Icons.location_on_outlined),
                selectedIcon: Icon(Icons.location_on),
                label: 'Nearby',
              ),
              NavigationDestination(
                icon: Icon(Icons.notifications_outlined),
                selectedIcon: Icon(Icons.notifications),
                label: 'Alerts',
              ),
              NavigationDestination(
                icon: Icon(Icons.person_outline),
                selectedIcon: Icon(Icons.person),
                label: 'Profile',
              ),
            ],
          ),
        ),
      ),
    );
  }
}
