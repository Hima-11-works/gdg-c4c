import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/home/home_screen.dart';
import '../features/nearby/nearby_screen.dart';
import '../features/alerts/alerts_screen.dart';
import '../features/profile/profile_screen.dart';
import '../features/onboarding/onboarding_screen.dart';
import '../providers/onboarding_providers.dart';

/// Top-level router key so tests and notification handlers can navigate
/// without needing a BuildContext.
final rootNavigatorKey = GlobalKey<NavigatorState>();
final shellNavigatorKey = GlobalKey<NavigatorState>();

/// Builds the GoRouter with an onboarding redirect.
///
/// Accepts a [ProviderContainer] so the redirect can read
/// [onboardingCompleteProvider] without needing a widget context.
GoRouter createRouter(ProviderContainer container) {
  return GoRouter(
    navigatorKey: rootNavigatorKey,
    initialLocation: '/home',
    // Re-run the redirect as soon as the async onboarding check resolves
    // (or flips), rather than only on the next navigation.
    refreshListenable: _OnboardingRefresh(container),
    redirect: (context, state) {
      final onboardingAsync = container.read(onboardingCompleteProvider);
      // While loading, don't redirect — let the current route render.
      if (onboardingAsync.isLoading) return null;
      final done = onboardingAsync.valueOrNull ?? false;
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
        builder: (context, state) => const OnboardingScreen(),
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
class ScaffoldWithNav extends StatelessWidget {
  const ScaffoldWithNav({super.key, required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
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
    );
  }
}

/// Notifies the router whenever the onboarding-completion decision changes.
///
/// [onboardingCompleteProvider] is async (it reads SharedPreferences and
/// secure storage), so its value arrives a frame or two after startup.
/// Without this, the redirect would only be re-evaluated on the next
/// navigation and a fresh/restored install could sit on the wrong screen.
class _OnboardingRefresh extends ChangeNotifier {
  _OnboardingRefresh(ProviderContainer container) {
    _subscription = container.listen<AsyncValue<bool>>(
      onboardingCompleteProvider,
      (previous, next) {
        // Only re-run the redirect when the actual decision changes, not on
        // every loading-tick, to avoid redundant redirects.
        if (previous?.valueOrNull != next.valueOrNull) notifyListeners();
      },
    );
  }

  late final ProviderSubscription<AsyncValue<bool>> _subscription;

  @override
  void dispose() {
    _subscription.close();
    super.dispose();
  }
}
